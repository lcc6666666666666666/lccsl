import secrets
from datetime import timezone
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, delete, text
from sqlalchemy.orm import Session
from .db import get_db, utcnow
from .models import User, LoginSession, Semester, Course, Offering, Meeting, Prerequisite, Qualification, Schedule, Enrollment, Alternate, Invoice, Outbox, Audit
from .schemas import Login, PasswordChange, ScheduleInput, VersionInput, TeachingInput, GradesInput, PersonCreate, PersonInput, SemesterInput
from .security import principal, role, authenticate, public_user, digest, password_hasher, encrypt_identity, identity_fingerprint, masked_identity
from .errors import require, DomainError
from .catalog import ensure_catalog,offering_structure
from . import services as svc

router = APIRouter(prefix='/api/v1')
DB = Annotated[Session, Depends(get_db)]
P = Annotated[object, Depends(principal)]

def commit(db,result):
    db.commit()
    return result

@router.get('/health')
def health(db:DB):
    db.execute(text('SELECT 1'))
    return {'status':'ok'}

@router.post('/auth/login')
def login(data:Login,request:Request,response:Response):
    return authenticate(data,request,response)

@router.get('/auth/me')
def me(p:P):
    return {'id':p.id,'username':p.username,'name':p.name,'role':p.role,'must_change_password':p.must_change_password,'csrf_token':p.csrf}

@router.post('/auth/logout')
def logout(p:P,request:Request,response:Response,db:DB):
    db.execute(delete(LoginSession).where(LoginSession.token_hash==digest(request.cookies.get('registration_session',''))))
    db.commit()
    response.delete_cookie('registration_session',path='/')
    return {'ok':True}

@router.post('/auth/password')
def change_password(data:PasswordChange,p:P,db:DB,response:Response):
    user=svc.lock_user(db,p.id)
    require(password_hasher.verify(data.old_password,user.password_hash),'PASSWORD_INVALID','原密码错误',400)
    require(data.new_password!=data.old_password,'PASSWORD_UNCHANGED','新密码不能与原密码相同',422)
    user.password_hash=password_hasher.hash(data.new_password)
    user.must_change_password=False
    db.execute(delete(LoginSession).where(LoginSession.user_id==p.id))
    svc.audit(db,p.id,'CHANGE_PASSWORD',f'user:{p.id}')
    db.commit()
    response.delete_cookie('registration_session',path='/')
    return {'ok':True,'message':'密码已修改，请重新登录'}

def term_view(t):
    return {'id':t.id,'code':t.code,'name':t.name,'start_date':t.start_date,'end_date':t.end_date,
            'registration_open':t.registration_open.replace(tzinfo=timezone.utc),
            'registration_close':t.registration_close.replace(tzinfo=timezone.utc),
            'state':t.state,'version':t.version,'close_summary':t.close_summary,
            'completed':t.end_date<utcnow().date(),
            'can_register':t.state=='OPEN' and t.registration_open<=utcnow()<=t.registration_close}

@router.get('/semesters')
def semesters(p:P,db:DB):
    return [term_view(t) for t in db.scalars(select(Semester).order_by(Semester.start_date.desc()))]

@router.get('/catalog/courses')
def courses(p:P,db:DB):
    ensure_catalog()
    return [{'id':c.id,'code':c.code,'name':c.name,'department':c.department} for c in db.scalars(select(Course).order_by(Course.code))]

@router.get('/catalog/offerings')
def offerings(p:P,db:DB,semester_id:int,q:str=Query(default='',max_length=100),page:int=Query(default=1,ge=1),page_size:int=Query(default=100,ge=1,le=100)):
    document=ensure_catalog()
    structure=offering_structure(db,semester_id,document)
    query=select(Offering,User.name,func.count().over().label('total')).select_from(Offering).outerjoin(User,User.id==Offering.professor_id).where(Offering.semester_id==semester_id)
    if q:
        query=query.join(Course,Course.id==Offering.course_id).where(Course.name.contains(q,autoescape=True)|Course.code.contains(q,autoescape=True))
    rows=db.execute(query.order_by(Offering.id).offset((page-1)*page_size).limit(page_size)).all()
    total=rows[0][2] if rows else db.scalar(select(func.count()).select_from(query.subquery()))
    qualified=set(db.scalars(select(Qualification.course_id).where(Qualification.professor_id==p.id))) if p.role=='professor' else set()
    passed=set(db.scalars(select(Offering.course_id).join(Enrollment).join(Semester).where(Enrollment.student_id==p.id,Enrollment.state=='ENROLLED',Enrollment.grade.in_(['A','B','C','D']),Semester.end_date<structure['start_date'],Semester.end_date<utcnow().date()))) if p.role=='student' else set()
    result=[]
    for o,name,_total in rows:
        c=structure['courses'][o.course_id]
        result.append({'id':o.id,'code':o.code,'course_id':c.id,'course_code':c.code,'name':c.name,'department':c.department,
            'credits':str(c.credits),'description':c.description,'professor_id':o.professor_id,'professor_name':name,
            'capacity':o.capacity,'enrolled_count':o.enrolled_count,'remaining':o.capacity-o.enrolled_count,'fee':str(o.fee),
            'state':o.state,'cancellation_reason':o.cancellation_reason,'eligible':c.id in qualified,
            'prerequisites_met':structure['required'].get(c.id,set()).issubset(passed),
            'prerequisites':structure['prerequisites'].get(c.id,[]),
            'meetings':structure['meetings'].get(o.id,[])})
    # These fields are already JSON primitives. Skip FastAPI's recursive generic
    # encoder for this high-volume read path; no ORM object crosses the boundary.
    return JSONResponse({'items':result,'total':total,'page':page,'page_size':page_size})

@router.get('/student/schedules/{semester_id}')
def get_schedule(semester_id:int,p:P,db:DB):
    role(p,'student')
    return JSONResponse(svc.schedule_view(db,p.id,semester_id))

@router.put('/student/schedules/{semester_id}/draft')
def draft(semester_id:int,data:ScheduleInput,p:P,db:DB):
    role(p,'student')
    ensure_catalog()
    return JSONResponse(commit(db,svc.save_draft(db,p.id,semester_id,data)))

@router.post('/student/schedules/{semester_id}/submit')
def submit(semester_id:int,data:ScheduleInput,p:P,db:DB,idempotency_key:Annotated[str|None,Header()]=None):
    role(p,'student')
    ensure_catalog()
    result=svc.idempotent(db,p.id,f'submit:{semester_id}',idempotency_key,data.model_dump(),lambda:svc.submit_schedule(db,p.id,semester_id,data),semester_id=semester_id)
    return commit(db,result)

@router.delete('/student/schedules/{semester_id}')
def remove_schedule(semester_id:int,data:VersionInput,p:P,db:DB):
    role(p,'student')
    return commit(db,svc.delete_schedule(db,p.id,semester_id,data.version))

@router.get('/student/report-card')
def report(p:P,db:DB,semester_id:int|None=None):
    role(p,'student')
    term=db.get(Semester,semester_id) if semester_id else db.scalar(select(Semester).where(Semester.end_date<utcnow().date()).order_by(Semester.end_date.desc()))
    require(term and term.end_date<utcnow().date(),'NO_REPORT_CARD','没有已结束学期的成绩信息',404)
    rows=db.execute(select(Enrollment,Offering,Course).join(Offering,Offering.id==Enrollment.offering_id).join(Course,Course.id==Offering.course_id).where(Enrollment.student_id==p.id,Enrollment.state=='ENROLLED',Offering.semester_id==term.id)).all()
    return {'semester':term_view(term),'items':[{'offering_id':o.id,'course':c.name,'code':c.code,'credits':str(c.credits),'grade':e.grade} for e,o,c in rows]}

@router.get('/student/invoices')
def invoices(p:P,db:DB):
    role(p,'student')
    return [{'id':i.id,'semester_id':i.semester_id,'total':str(i.total),'lines':i.lines,'created_at':i.created_at} for i in db.scalars(select(Invoice).where(Invoice.student_id==p.id).order_by(Invoice.created_at.desc()))]

@router.get('/professor/teaching/{semester_id}')
def teaching(semester_id:int,p:P,db:DB):
    role(p,'professor')
    return {'offering_ids':list(db.scalars(select(Offering.id).where(Offering.semester_id==semester_id,Offering.professor_id==p.id).order_by(Offering.id)))}

@router.put('/professor/teaching/{semester_id}')
def set_teaching(semester_id:int,data:TeachingInput,p:P,db:DB):
    role(p,'professor')
    ensure_catalog()
    return commit(db,svc.update_teaching(db,p.id,semester_id,data))

@router.get('/professor/offerings/{offering_id}/roster')
def roster(offering_id:int,p:P,db:DB):
    role(p,'professor')
    o=db.get(Offering,offering_id)
    require(o and o.professor_id==p.id,'OFFERING_NOT_FOUND','教学班不存在或不属于本人',404)
    term=db.get(Semester,o.semester_id)
    rows=db.execute(select(Enrollment,User).join(User,User.id==Enrollment.student_id).where(Enrollment.offering_id==o.id,Enrollment.state=='ENROLLED').order_by(User.id)).all()
    return {'can_grade':term.end_date<utcnow().date() and o.state=='CLOSED','items':[{'student_id':u.id,'name':u.name,'username':u.username,'grade':e.grade,'version':e.grade_version} for e,u in rows]}

@router.put('/professor/offerings/{offering_id}/grades')
def grades(offering_id:int,data:GradesInput,p:P,db:DB):
    role(p,'professor')
    svc.lock_user(db,p.id)
    o=db.scalar(select(Offering).where(Offering.id==offering_id).with_for_update())
    require(o and o.professor_id==p.id,'OFFERING_NOT_FOUND','教学班不存在或不属于本人',404)
    term=db.get(Semester,o.semester_id)
    require(term.end_date<utcnow().date() and o.state=='CLOSED','GRADING_NOT_OPEN','只能录入本人已结束教学班的成绩')
    require(len({v.student_id for v in data.grades})==len(data.grades),'DUPLICATE_STUDENT','学生重复',422)
    for value in sorted(data.grades,key=lambda v:v.student_id):
        e=db.scalar(select(Enrollment).where(Enrollment.student_id==value.student_id,Enrollment.offering_id==o.id,Enrollment.state=='ENROLLED').with_for_update())
        require(e,'STUDENT_NOT_ENROLLED','学生不在该教学班名单中',422)
        svc.check_version(e.grade_version,value.version)
        e.grade=value.grade
        e.grade_version += 1
    svc.audit(db,p.id,'UPDATE_GRADES',f'offering:{o.id}',{'student_ids':[v.student_id for v in data.grades]})
    db.commit()
    return roster(offering_id,p,db)

def person_view(db,u):
    return {**public_user(u),'birth_date':u.birth_date,'identity_masked':masked_identity(u.identity_cipher),
            'status':u.status,'department':u.department,'graduation_date':u.graduation_date,'active':u.active,'version':u.version,
            'qualification_ids':list(db.scalars(select(Qualification.course_id).where(Qualification.professor_id==u.id))) if u.role=='professor' else []}

@router.get('/registrar/people/{kind}')
def people(kind:str,p:P,db:DB,q:str=Query(default='',max_length=100),page:int=Query(default=1,ge=1),page_size:int=Query(default=20,ge=1,le=100),include_inactive:bool=False):
    role(p,'registrar')
    require(kind in {'student','professor'},'INVALID_ROLE','人员类型不正确',422)
    query=select(User).where(User.role==kind)
    if not include_inactive:
        query=query.where(User.active.is_(True))
    if q:
        query=query.where(User.name.contains(q,autoescape=True)|User.username.contains(q,autoescape=True)|(User.id==int(q) if q.isdigit() else False))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    return {'total':total,'items':[person_view(db,u) for u in db.scalars(query.order_by(User.id).offset((page-1)*page_size).limit(page_size))]}

def set_person(db,u,data):
    u.name,u.birth_date,u.status=data.name,data.birth_date,data.status
    if data.identity_number:
        u.identity_cipher=encrypt_identity(data.identity_number)
        u.identity_fingerprint=identity_fingerprint(data.identity_number)
    if u.role=='professor':
        require(bool(data.department),'DEPARTMENT_REQUIRED','教授必须填写所属院系',422)
        u.department=data.department
        require(len(set(data.qualification_ids))==len(data.qualification_ids),'DUPLICATE_QUALIFICATION','任课资格重复',422)
        valid=set(db.scalars(select(Course.id).where(Course.id.in_(data.qualification_ids))))
        require(valid==set(data.qualification_ids),'INVALID_QUALIFICATION','任课资格包含不存在的课程',422)
        assigned=set(db.scalars(select(Offering.course_id).join(Semester).where(Offering.professor_id==u.id,Semester.state=='OPEN')))
        require(assigned.issubset(valid),'QUALIFICATION_IN_USE','请先取消相关任课，再移除任课资格')
        db.execute(delete(Qualification).where(Qualification.professor_id==u.id))
        for cid in valid:
            db.add(Qualification(professor_id=u.id,course_id=cid))
    else:
        u.graduation_date=data.graduation_date

@router.post('/registrar/people/{kind}')
def create_person(kind:str,data:PersonCreate,p:P,db:DB):
    role(p,'registrar')
    require(kind in {'student','professor'},'INVALID_ROLE','人员类型不正确',422)
    require(data.identity_number,'IDENTITY_REQUIRED','请输入身份号码',422)
    password=secrets.token_urlsafe(18)
    u=User(username=data.username.lower(),name=data.name,role=kind,password_hash=password_hasher.hash(password),active=True,must_change_password=True)
    db.add(u)
    db.flush()
    set_person(db,u,data)
    svc.audit(db,p.id,'CREATE_PERSON',f'user:{u.id}',{'role':kind})
    db.flush()
    result={'person':person_view(db,u),'initial_password':password}
    return commit(db,result)

def lock_person_mutation(db,user_id):
    # Consistent term -> user -> offering order also serializes with closing a term.
    list(db.scalars(select(Semester).where(Semester.state=='OPEN').order_by(Semester.id).with_for_update(read=True)))
    return svc.lock_user(db,user_id)

@router.put('/registrar/people/{user_id}')
def update_person(user_id:int,data:PersonInput,p:P,db:DB):
    role(p,'registrar')
    u=lock_person_mutation(db,user_id)
    require(u.role in {'student','professor'},'PERSON_NOT_FOUND','人员不存在',404)
    svc.check_version(u.version,data.version)
    set_person(db,u,data)
    u.version += 1
    svc.audit(db,p.id,'UPDATE_PERSON',f'user:{u.id}')
    db.flush()
    return commit(db,person_view(db,u))

@router.delete('/registrar/people/{user_id}')
def deactivate(user_id:int,data:VersionInput,p:P,db:DB):
    role(p,'registrar')
    u=lock_person_mutation(db,user_id)
    require(u.role in {'student','professor'},'PERSON_NOT_FOUND','人员不存在',404)
    svc.check_version(u.version,data.version)
    terms=list(db.scalars(select(Semester.id).where(Semester.state=='OPEN')))
    if u.role=='student':
        rows=list(db.scalars(select(Enrollment).join(Offering).where(Enrollment.student_id==u.id,Enrollment.state=='ENROLLED',Offering.semester_id.in_(terms))))
        list(db.scalars(select(Offering).where(Offering.id.in_([e.offering_id for e in rows])).order_by(Offering.id).with_for_update()))
        for e in rows:
            e.state='DROPPED'
            db.get(Offering,e.offering_id).enrolled_count-=1
        for s in db.scalars(select(Schedule).where(Schedule.student_id==u.id,Schedule.semester_id.in_(terms)).with_for_update()):
            s.deleted,s.draft=True,None
            s.version+=1
            db.execute(delete(Alternate).where(Alternate.schedule_id==s.id))
            svc.emit(db,'RegistrationUpdated',{'student_id':u.id,'semester_id':s.semester_id,'version':s.version,'offering_ids':[]})
    else:
        for o in db.scalars(select(Offering).where(Offering.professor_id==u.id,Offering.semester_id.in_(terms)).order_by(Offering.id).with_for_update()):
            o.professor_id=None
    u.active=False
    u.version+=1
    db.execute(delete(LoginSession).where(LoginSession.user_id==u.id))
    svc.audit(db,p.id,'DEACTIVATE_PERSON',f'user:{u.id}')
    return commit(db,{'ok':True})

@router.post('/registrar/people/{user_id}/reset-password')
def reset_password(user_id:int,p:P,db:DB):
    role(p,'registrar')
    u=svc.lock_user(db,user_id)
    require(u.role in {'student','professor'},'PERSON_NOT_FOUND','人员不存在',404)
    password=secrets.token_urlsafe(18)
    u.password_hash=password_hasher.hash(password)
    u.must_change_password=True
    db.execute(delete(LoginSession).where(LoginSession.user_id==u.id))
    svc.audit(db,p.id,'RESET_PASSWORD',f'user:{u.id}')
    return commit(db,{'initial_password':password})

@router.put('/registrar/semesters/{semester_id}')
def update_term(semester_id:int,data:SemesterInput,p:P,db:DB):
    role(p,'registrar')
    t=svc.lock_semester(db,semester_id,close=True)
    svc.check_version(t.version,data.version)
    require(data.registration_close.date()<=t.end_date,'INVALID_WINDOW','选课结束时间不能晚于学期结束',422)
    t.registration_open=data.registration_open.astimezone(timezone.utc).replace(tzinfo=None)
    t.registration_close=data.registration_close.astimezone(timezone.utc).replace(tzinfo=None)
    t.version+=1
    svc.audit(db,p.id,'UPDATE_REGISTRATION_WINDOW',f'semester:{t.id}')
    return commit(db,term_view(t))

@router.post('/registrar/semesters/{semester_id}/close')
def close(semester_id:int,data:VersionInput,p:P,db:DB,idempotency_key:Annotated[str|None,Header()]=None):
    role(p,'registrar')
    return commit(db,svc.idempotent(db,p.id,f'close:{semester_id}',idempotency_key,data.model_dump(),lambda:svc.close_registration(db,p.id,semester_id,data.version),semester_id=semester_id,closing=True))

@router.get('/registrar/outbox')
def outbox(p:P,db:DB,page:int=Query(default=1,ge=1),page_size:int=Query(default=20,ge=1,le=100)):
    role(p,'registrar')
    counts=dict(db.execute(select(Outbox.state,func.count()).group_by(Outbox.state)).all())
    rows=db.scalars(select(Outbox).order_by(Outbox.created_at.desc()).offset((page-1)*page_size).limit(page_size))
    return {'counts':counts,'total':sum(counts.values()),'items':[{'id':x.id,'kind':x.kind,'state':x.state,'attempts':x.attempts,'next_attempt':x.next_attempt.replace(tzinfo=timezone.utc),'last_error':x.last_error,'created_at':x.created_at.replace(tzinfo=timezone.utc)} for x in rows]}

@router.post('/registrar/outbox/{event_id}/retry')
def retry(event_id:str,p:P,db:DB):
    role(p,'registrar')
    event=db.get(Outbox,event_id,with_for_update=True)
    require(event,'EVENT_NOT_FOUND','任务不存在',404)
    require(event.state=='PENDING','EVENT_NOT_PENDING','只能重试等待中的任务')
    event.next_attempt=utcnow()
    svc.audit(db,p.id,'RETRY_BILLING',f'event:{event_id}')
    return commit(db,{'ok':True})

@router.get('/registrar/audit')
def audit_log(p:P,db:DB,page:int=Query(default=1,ge=1),page_size:int=Query(default=20,ge=1,le=100)):
    role(p,'registrar')
    return {'total':db.scalar(select(func.count(Audit.id))), 'items':[{'id':x.id,'actor_id':x.actor_id,'action':x.action,'target':x.target,'created_at':x.created_at.replace(tzinfo=timezone.utc)} for x in db.scalars(select(Audit).order_by(Audit.id.desc()).offset((page-1)*page_size).limit(page_size))]}
