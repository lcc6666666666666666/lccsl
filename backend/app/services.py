import hashlib
import json
import uuid
from decimal import Decimal
from sqlalchemy import select, delete
from sqlalchemy.dialects.mysql import insert
from .db import utcnow
from .models import User, Semester, Course, Offering, Meeting, Prerequisite, Qualification, Schedule, Enrollment, Alternate, Invoice, Outbox, Idempotency, Audit
from .errors import DomainError, require

def audit(db, actor, action, target, details=None):
    db.add(Audit(actor_id=actor, action=action, target=target, details=details or {}))

def emit(db, kind, payload):
    event = Outbox(id=str(uuid.uuid4()), kind=kind, payload=payload)
    db.add(event)
    return event

def lock_semester(db, semester_id, close=False):
    term = db.scalar(select(Semester).where(Semester.id==semester_id).with_for_update(read=not close, nowait=close).execution_options(populate_existing=True))
    require(term, 'SEMESTER_NOT_FOUND', '学期不存在', 404)
    require(term.state=='OPEN', 'REGISTRATION_CLOSED', '该学期选课已结束')
    if not close:
        require(term.registration_open <= utcnow() <= term.registration_close, 'REGISTRATION_NOT_OPEN', '当前不在选课开放时间内')
    return term

def lock_user(db, user_id):
    u = db.scalar(select(User).where(User.id==user_id).with_for_update().execution_options(populate_existing=True))
    require(u,'PERSON_NOT_FOUND','人员不存在',404)
    require(u.active, 'ACCOUNT_DISABLED', '账户已停用', 403)
    return u

def lock_schedule(db, student_id, semester_id):
    stmt = insert(Schedule).values(student_id=student_id, semester_id=semester_id, version=0, deleted=False)
    db.execute(stmt.on_duplicate_key_update(student_id=stmt.inserted.student_id))
    return db.scalar(select(Schedule).where(Schedule.student_id==student_id, Schedule.semester_id==semester_id).with_for_update().execution_options(populate_existing=True))

def check_version(actual, expected):
    require(actual==expected, 'VERSION_CONFLICT', '数据已被其他操作修改，请刷新后重试', details={'current_version':actual})

def idempotent(db, user_id, operation, key, payload, action, *, semester_id, closing=False):
    require(key and 8 <= len(key) <= 80, 'IDEMPOTENCY_REQUIRED', '缺少有效的请求编号', 422)
    # Lock parent rows BEFORE inserting a child with a user FK. Otherwise two
    # insertions can retain shared FK locks while both try to upgrade the user lock.
    # Do not check OPEN here: an already committed response can be replayed after closing.
    term=db.scalar(select(Semester).where(Semester.id==semester_id).with_for_update(read=not closing,nowait=closing))
    require(term,'SEMESTER_NOT_FOUND','学期不存在',404)
    lock_user(db,user_id)
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    stmt = insert(Idempotency).values(user_id=user_id, operation=operation, key=key, request_hash=fingerprint)
    db.execute(stmt.on_duplicate_key_update(key=stmt.inserted.key))
    record = db.get(Idempotency, (user_id,operation,key), with_for_update=True)
    require(record.request_hash==fingerprint, 'IDEMPOTENCY_CONFLICT', '同一请求编号不能用于不同的操作内容')
    if record.response is not None:
        return record.response
    result = action()
    record.response = result
    db.flush()
    return result

def validate_selection(db, semester_id, primary, alternates, lock=True):
    ids = primary+alternates
    require(len(ids)==len(set(ids)), 'DUPLICATE_OFFERING', '首选和备选教学班不能重复', 422)
    query=select(Offering).where(Offering.id.in_(ids),Offering.semester_id==semester_id).order_by(Offering.id)
    if lock:
        query=query.with_for_update()
    offerings = list(db.scalars(query)) if ids else []
    require(len(offerings)==len(ids), 'INVALID_OFFERING', '教学班不存在或不属于该学期', 422)
    require(len({o.course_id for o in offerings})==len(ids), 'DUPLICATE_COURSE', '同一门课程不能选择多个教学班', 422)
    require(all(o.state=='OPEN' for o in offerings), 'OFFERING_CLOSED', '所选教学班已关闭')
    return {o.id:o for o in offerings}

def meetings_conflict(a, b):
    return a.weekday==b.weekday and max(a.week_start,b.week_start)<=min(a.week_end,b.week_end) and a.start_minute<b.end_minute and b.start_minute<a.end_minute

def conflicting(db, offering_id, others):
    if not others:
        return []
    rows = list(db.scalars(select(Meeting).where(Meeting.offering_id.in_([offering_id]+list(others)))))
    mine = [m for m in rows if m.offering_id==offering_id]
    return sorted({b.offering_id for a in mine for b in rows if b.offering_id!=offering_id and meetings_conflict(a,b)})

def prerequisites_met(db, student_id, course_id, term):
    required = set(db.scalars(select(Prerequisite.prerequisite_id).where(Prerequisite.course_id==course_id)))
    if not required:
        return True
    passed = set(db.scalars(select(Offering.course_id).join(Enrollment, Enrollment.offering_id==Offering.id).join(Semester, Semester.id==Offering.semester_id).where(Enrollment.student_id==student_id, Enrollment.state=='ENROLLED', Enrollment.grade.in_(['A','B','C','D']), Semester.end_date < term.start_date, Semester.end_date < utcnow().date())))
    return required.issubset(passed)

def current_enrollments(db, student_id, semester_id):
    return list(db.scalars(select(Enrollment).join(Offering).where(Enrollment.student_id==student_id, Offering.semester_id==semester_id, Enrollment.state=='ENROLLED').order_by(Enrollment.position,Enrollment.id)))

def schedule_view(db, student_id, semester_id):
    require(db.get(Semester, semester_id), 'SEMESTER_NOT_FOUND', '学期不存在', 404)
    row = db.scalar(select(Schedule).where(Schedule.student_id==student_id,Schedule.semester_id==semester_id))
    enrolled = current_enrollments(db, student_id, semester_id)
    alt = list(db.scalars(select(Alternate.offering_id).where(Alternate.schedule_id==row.id).order_by(Alternate.position))) if row else []
    return {'version':row.version if row else 0, 'exists':bool(row and not row.deleted),
            'ever_submitted':bool(row and row.first_submitted_at), 'primary':[e.offering_id for e in enrolled],
            'alternates':alt, 'draft':row.draft if row else None}

def save_draft(db, actor, semester_id, data):
    lock_semester(db, semester_id)
    lock_user(db, actor)
    row = lock_schedule(db, actor, semester_id)
    check_version(row.version,data.version)
    validate_selection(db,semester_id,data.primary,data.alternates,lock=False)
    row.draft = {'primary':data.primary,'alternates':data.alternates}
    row.deleted = False
    row.version += 1
    audit(db,actor,'SAVE_DRAFT',f'schedule:{row.id}')
    db.flush()
    return schedule_view(db,actor,semester_id)

def submit_schedule(db, actor, semester_id, data):
    term = lock_semester(db,semester_id)
    lock_user(db,actor)
    row = lock_schedule(db,actor,semester_id)
    check_version(row.version,data.version)
    require(len(data.primary)>=1, 'PRIMARY_REQUIRED','至少选择一个首选教学班',422)
    if not row.first_submitted_at:
        require(len(data.primary)==4 and len(data.alternates)==2,'INITIAL_SELECTION_COUNT','首次提交必须选择4个首选和2个备选',422)
    old = current_enrollments(db,actor,semester_id)
    all_ids = sorted(set([e.offering_id for e in old]+data.primary+data.alternates))
    list(db.scalars(select(Offering).where(Offering.id.in_(all_ids)).order_by(Offering.id).with_for_update()))
    offers = validate_selection(db,semester_id,data.primary,data.alternates)
    old_ids = {e.offering_id for e in old}
    for oid in data.primary:
        offer = offers[oid]
        require(prerequisites_met(db,actor,offer.course_id,term),'PREREQUISITE_NOT_MET','未满足所选课程的先修要求',details={'offering_id':oid})
        require(oid in old_ids or offer.enrolled_count<offer.capacity,'COURSE_FULL','教学班已满，请调整选课',details={'offering_id':oid})
        conflicts = conflicting(db,oid,[x for x in data.primary if x!=oid])
        require(not conflicts,'SCHEDULE_CONFLICT','所选课程上课时间冲突',details={'offering_ids':[oid]+conflicts})
    # All validation precedes any removal: an unsuccessful update retains the old schedule.
    for e in old:
        if e.offering_id not in data.primary:
            e.state = 'DROPPED'
            db.get(Offering,e.offering_id).enrolled_count -= 1
    for position, oid in enumerate(data.primary):
        enrollment = db.scalar(select(Enrollment).where(Enrollment.student_id==actor,Enrollment.offering_id==oid))
        if enrollment is None:
            db.add(Enrollment(student_id=actor,offering_id=oid,position=position,state='ENROLLED'))
            offers[oid].enrolled_count += 1
        else:
            if enrollment.state!='ENROLLED':
                offers[oid].enrolled_count += 1
            enrollment.state, enrollment.position = 'ENROLLED',position
    db.execute(delete(Alternate).where(Alternate.schedule_id==row.id))
    for position,oid in enumerate(data.alternates):
        db.add(Alternate(schedule_id=row.id,offering_id=oid,position=position))
    row.draft, row.deleted = None,False
    row.first_submitted_at = row.first_submitted_at or utcnow()
    row.version += 1
    emit(db,'RegistrationUpdated',{'student_id':actor,'semester_id':semester_id,'version':row.version,'offering_ids':data.primary})
    audit(db,actor,'SUBMIT_SCHEDULE',f'schedule:{row.id}',{'version':row.version})
    db.flush()
    return schedule_view(db,actor,semester_id)

def delete_schedule(db,actor,semester_id,version):
    lock_semester(db,semester_id)
    lock_user(db,actor)
    row = lock_schedule(db,actor,semester_id)
    require(not row.deleted and row.version>0,'SCHEDULE_NOT_FOUND','未找到课表',404)
    check_version(row.version,version)
    old = current_enrollments(db,actor,semester_id)
    list(db.scalars(select(Offering).where(Offering.id.in_([e.offering_id for e in old])).order_by(Offering.id).with_for_update()))
    for e in old:
        e.state = 'DROPPED'
        db.get(Offering,e.offering_id).enrolled_count -= 1
    db.execute(delete(Alternate).where(Alternate.schedule_id==row.id))
    row.deleted,row.draft = True,None
    row.version += 1
    emit(db,'RegistrationUpdated',{'student_id':actor,'semester_id':semester_id,'version':row.version,'offering_ids':[]})
    audit(db,actor,'DELETE_SCHEDULE',f'schedule:{row.id}')
    db.flush()
    return schedule_view(db,actor,semester_id)

def update_teaching(db,actor,semester_id,data):
    lock_semester(db,semester_id)
    lock_user(db,actor)
    old = list(db.scalars(select(Offering).where(Offering.semester_id==semester_id,Offering.professor_id==actor)))
    old_ids = {o.id for o in old}
    require(old_ids==set(data.expected_ids),'VERSION_CONFLICT','任课安排已改变，请刷新重试')
    require(len(set(data.offering_ids))==len(data.offering_ids),'DUPLICATE_OFFERING','教学班重复',422)
    offers = list(db.scalars(select(Offering).where(Offering.id.in_(sorted(old_ids|set(data.offering_ids)))).order_by(Offering.id).with_for_update().execution_options(populate_existing=True)))
    selected = {o.id:o for o in offers if o.id in data.offering_ids}
    require(len(selected)==len(data.offering_ids),'INVALID_OFFERING','教学班不存在',422)
    qualified = set(db.scalars(select(Qualification.course_id).where(Qualification.professor_id==actor)))
    for oid,o in selected.items():
        require(o.semester_id==semester_id and o.state=='OPEN','OFFERING_CLOSED','教学班不属于开放学期')
        require(o.course_id in qualified,'NOT_QUALIFIED','不具备该课程的授课资格',403)
        require(o.professor_id in {None,actor},'PROFESSOR_ASSIGNED','该教学班已由其他教授认领')
        conflicts=conflicting(db,oid,[i for i in selected if i!=oid])
        require(not conflicts,'TEACHING_CONFLICT',f'教学班 {o.code} 与 {"、".join(selected[i].code for i in conflicts)} 授课时间冲突',details={'offering_ids':[oid]+conflicts})
    for o in offers:
        if o.id in selected:
            o.professor_id = actor
        elif o.professor_id==actor:
            o.professor_id = None
    audit(db,actor,'UPDATE_TEACHING',f'semester:{semester_id}',{'offering_ids':data.offering_ids})
    return {'offering_ids':data.offering_ids}

def close_registration(db,actor,semester_id,version):
    term = lock_semester(db,semester_id,close=True)
    check_version(term.version,version)
    # The exclusive semester lock prevents new registration mutations until commit.
    offers = list(db.scalars(select(Offering).where(Offering.semester_id==semester_id).order_by(Offering.id).with_for_update()))
    active_professors = set(db.scalars(select(User.id).where(User.role=='professor',User.active.is_(True))))
    cancelled, promoted = [],[]

    def cancel(o,reason):
        o.state,o.cancellation_reason,o.enrolled_count = 'CANCELLED',reason,0
        for enrollment in db.scalars(select(Enrollment).where(Enrollment.offering_id==o.id,Enrollment.state=='ENROLLED')):
            enrollment.state = 'CANCELLED'
        cancelled.append({'id':o.id,'code':o.code,'reason':reason})
        db.flush()

    for o in offers:
        if o.professor_id not in active_professors:
            cancel(o,'没有有效授课教授')
    schedules = list(db.scalars(select(Schedule).join(User,User.id==Schedule.student_id).where(Schedule.semester_id==semester_id,Schedule.first_submitted_at.is_not(None),Schedule.deleted.is_(False),User.active.is_(True)).order_by(Schedule.first_submitted_at,Schedule.student_id).with_for_update()))
    for row in schedules:
        chosen = current_enrollments(db,row.student_id,semester_id)
        ids = [e.offering_id for e in chosen]
        course_ids = {db.get(Offering,oid).course_id for oid in ids}
        for alt in db.scalars(select(Alternate).where(Alternate.schedule_id==row.id).order_by(Alternate.position)):
            if len(ids)>=4:
                break
            o = db.get(Offering,alt.offering_id)
            if o.state!='OPEN' or o.professor_id not in active_professors or o.enrolled_count>=10 or o.course_id in course_ids:
                continue
            if not prerequisites_met(db,row.student_id,o.course_id,term) or conflicting(db,o.id,ids):
                continue
            enrollment = db.scalar(select(Enrollment).where(Enrollment.student_id==row.student_id,Enrollment.offering_id==o.id))
            if enrollment:
                enrollment.state,enrollment.position = 'ENROLLED',len(ids)
            else:
                db.add(Enrollment(student_id=row.student_id,offering_id=o.id,state='ENROLLED',position=len(ids)))
            o.enrolled_count += 1
            ids.append(o.id)
            course_ids.add(o.course_id)
            promoted.append({'student_id':row.student_id,'offering_id':o.id})
            db.flush()
    for o in offers:
        if o.state=='OPEN':
            if o.enrolled_count<3:
                cancel(o,'最终选课人数不足3人')
            else:
                o.state='CLOSED'
    for row in schedules:
        lines=[]
        for e in current_enrollments(db,row.student_id,semester_id):
            o=db.get(Offering,e.offering_id)
            c=db.get(Course,o.course_id)
            lines.append({'offering_id':o.id,'code':o.code,'course':c.name,'fee':str(o.fee)})
        total=sum((Decimal(line['fee']) for line in lines),Decimal('0.00'))
        db.add(Invoice(student_id=row.student_id,semester_id=semester_id,total=total,lines=lines))
        emit(db,'InvoiceFinalized',{'student_id':row.student_id,'semester_id':semester_id,'total':str(total),'lines':lines})
        row.version += 1
        row.draft = None
    term.state,term.closed_at = 'CLOSED',utcnow()
    term.version += 1
    summary={'semester_id':term.id,'cancelled':cancelled,'promoted':promoted,'invoices':len(schedules),'confirmed_offerings':sum(o.state=='CLOSED' for o in offers)}
    term.close_summary=summary
    audit(db,actor,'CLOSE_REGISTRATION',f'semester:{term.id}',summary)
    db.flush()
    return summary
