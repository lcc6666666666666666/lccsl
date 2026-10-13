import hashlib
import json
import uuid
from datetime import timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, delete
from sqlalchemy.dialects.mysql import insert
from .db import utcnow
from .models import User, Semester, Course, Offering, Meeting, Prerequisite, Qualification, Schedule, Enrollment, Alternate, Invoice, Outbox, Idempotency, Audit
from .errors import DomainError, require
from .validation_messages import field_detail
from .models import TeachingAssignment
from . import academic

def audit(db, actor, action, target, details=None):
    db.add(Audit(actor_id=actor, action=action, target=target, details=details or {}))

def emit(db, kind, payload):
    event = Outbox(id=str(uuid.uuid4()), kind=kind, payload=payload)
    db.add(event)
    return event


def record_teaching(db,professor_id,offering_id,state,reason=None):
    row=db.get(TeachingAssignment,(professor_id,offering_id))
    if row is None:
        row=TeachingAssignment(professor_id=professor_id,offering_id=offering_id)
        db.add(row)
    row.state,row.reason,row.updated_at=state,reason,utcnow()


def registration_time_label(value):
    try:
        return f'{value + timedelta(hours=8):%Y-%m-%d %H:%M}（北京时间）'
    except OverflowError:
        # Keep diagnostics safe even for an extreme, valid database datetime.
        return f'{value:%Y-%m-%d %H:%M}（UTC）'


def lock_semester(db, semester_id, close=False):
    term = db.scalar(select(Semester).where(Semester.id==semester_id).with_for_update(read=not close, nowait=close).execution_options(populate_existing=True))
    require(term, 'SEMESTER_NOT_FOUND', '未找到所选学期，请刷新学期列表后重新选择', 404)
    require(term.state=='OPEN', 'REGISTRATION_CLOSED', f'“{term.name}”选课已结束，正式课表和任课安排已冻结；请切换到开放选课的学期')
    if not close:
        if not term.registration_open <= utcnow() <= term.registration_close:
            opened = registration_time_label(term.registration_open)
            closed = registration_time_label(term.registration_close)
            raise DomainError('REGISTRATION_NOT_OPEN', f'“{term.name}”当前不在选课开放时间内；开放时间为 {opened} 至 {closed}，请在该时间内操作或联系教务管理员核对')
    return term

def lock_user(db, user_id):
    u = db.scalar(select(User).where(User.id==user_id).with_for_update().execution_options(populate_existing=True))
    require(u,'PERSON_NOT_FOUND','未找到当前账户，请重新登录；仍无法使用时请联系教务管理员',404)
    require(u.active, 'ACCOUNT_DISABLED', '当前账户已停用，无法修改选课或任课安排；请联系教务管理员核对账户状态', 403)
    return u

def lock_schedule(db, student_id, semester_id):
    stmt = insert(Schedule).values(student_id=student_id, semester_id=semester_id, version=0, deleted=False)
    db.execute(stmt.on_duplicate_key_update(student_id=stmt.inserted.student_id))
    return db.scalar(select(Schedule).where(Schedule.student_id==student_id, Schedule.semester_id==semester_id).with_for_update().execution_options(populate_existing=True))

def check_version(actual, expected):
    require(actual==expected, 'VERSION_CONFLICT', '本次修改未保存：当前页面的数据已被其他操作更新。请先记录尚未保存的内容，再刷新页面，以最新数据重新修改并提交', details={'current_version':actual})

def idempotent(db, user_id, operation, key, payload, action, *, semester_id, closing=False):
    require(key and 8 <= len(key) <= 80, 'IDEMPOTENCY_REQUIRED', '本次提交缺少有效的防重复请求标识（需为8～80个字符）；请刷新页面后重新提交，仍失败时请联系管理员', 422)
    # Lock parent rows BEFORE inserting a child with a user FK. Otherwise two
    # insertions can retain shared FK locks while both try to upgrade the user lock.
    # Do not check OPEN here: an already committed response can be replayed after closing.
    term=db.scalar(select(Semester).where(Semester.id==semester_id).with_for_update(read=not closing,nowait=closing))
    require(term,'SEMESTER_NOT_FOUND','未找到所选学期，请刷新学期列表后重新选择',404)
    lock_user(db,user_id)
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    stmt = insert(Idempotency).values(user_id=user_id, operation=operation, key=key, request_hash=fingerprint)
    db.execute(stmt.on_duplicate_key_update(key=stmt.inserted.key))
    record = db.get(Idempotency, (user_id,operation,key), with_for_update=True)
    require(record.request_hash==fingerprint, 'IDEMPOTENCY_CONFLICT', '本次提交内容与此前使用同一请求标识提交的内容不一致，无法确认是否为重复操作；请先刷新查看最新结果，再重新提交修改')
    if record.response is not None:
        return record.response
    result = action()
    record.response = result
    db.flush()
    return result

def course_label(db, course_id):
    course = db.get(Course, course_id)
    return f'《{course.name}》（{course.code}）' if course else '所选课程'


def offering_label(db, offering):
    return f'{course_label(db, offering.course_id)}教学班 {offering.code}'


def validate_selection(db, semester_id, primary, alternates, lock=True):
    ids = primary+alternates
    if len(ids)!=len(set(ids)):
        duplicates = '、'.join(str(oid) for oid in sorted({oid for oid in ids if ids.count(oid)>1}))
        raise DomainError('DUPLICATE_OFFERING', f'首选和备选中重复选择了教学班（编号：{duplicates}）；每个教学班只能保留一次，请删除重复项后重试', 422)
    query=select(Offering).where(Offering.id.in_(ids),Offering.semester_id==semester_id).order_by(Offering.id)
    if lock:
        query=query.with_for_update()
    offerings = list(db.scalars(query)) if ids else []
    if len(offerings)!=len(ids):
        missing = '、'.join(str(oid) for oid in sorted(set(ids)-{o.id for o in offerings}))
        raise DomainError('INVALID_OFFERING', f'教学班（编号：{missing}）不存在或不属于当前所选学期；请刷新课程目录并重新选择', 422)
    by_course = {}
    for offering in offerings:
        by_course.setdefault(offering.course_id, []).append(offering)
    duplicate_courses = [group for group in by_course.values() if len(group)>1]
    if duplicate_courses:
        names = '；'.join(f'{course_label(db, group[0].course_id)}：{"、".join(o.code for o in group)}' for group in duplicate_courses)
        raise DomainError('DUPLICATE_COURSE', f'同一门课程在首选和备选中只能保留一个教学班；重复课程为 {names}，请删除多余教学班', 422)
    closed = [offering_label(db, o) for o in offerings if o.state!='OPEN']
    require(not closed, 'OFFERING_CLOSED', f'{"、".join(closed)}已关闭或取消，无法继续选择；请刷新目录并移除这些教学班')
    return {o.id:o for o in offerings}

def meetings_conflict(a, b):
    return a.weekday==b.weekday and max(a.week_start,b.week_start)<=min(a.week_end,b.week_end) and a.start_minute<b.end_minute and b.start_minute<a.end_minute

def conflicting(db, offering_id, others):
    if not others:
        return []
    rows = list(db.scalars(select(Meeting).where(Meeting.offering_id.in_([offering_id]+list(others)))))
    mine = [m for m in rows if m.offering_id==offering_id]
    return sorted({b.offering_id for a in mine for b in rows if b.offering_id!=offering_id and meetings_conflict(a,b)})

def missing_prerequisites(db, student_id, course_id, term):
    required = set(db.scalars(select(Prerequisite.prerequisite_id).where(Prerequisite.course_id==course_id)))
    if not required:
        return set()
    passed = set(db.scalars(select(Offering.course_id).join(Enrollment, Enrollment.offering_id==Offering.id).join(Semester, Semester.id==Offering.semester_id).where(Enrollment.student_id==student_id, Enrollment.state=='ENROLLED', Enrollment.grade.in_(['A','B','C','D']), academic.completion_date() < term.start_date, academic.completed_condition())))
    return required - passed


def prerequisites_met(db, student_id, course_id, term):
    return not missing_prerequisites(db, student_id, course_id, term)

def current_enrollments(db, student_id, semester_id):
    return list(db.scalars(select(Enrollment).join(Offering).where(Enrollment.student_id==student_id, Offering.semester_id==semester_id, Enrollment.state=='ENROLLED').order_by(Enrollment.position,Enrollment.id)))

def schedule_view(db, student_id, semester_id):
    require(db.get(Semester, semester_id), 'SEMESTER_NOT_FOUND', '未找到所选学期，请刷新学期列表后重新选择', 404)
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
    require(len(data.primary)>=1, 'PRIMARY_REQUIRED','当前未选择首选教学班，无法提交；请至少添加1个首选，首次提交须选满4个首选和2个备选。若要清空已提交的课表，请使用“删除课表”',422)
    if not row.first_submitted_at:
        require(len(data.primary)==4 and len(data.alternates)==2,'INITIAL_SELECTION_COUNT',f'首次提交必须选择4个首选和2个备选；当前为{len(data.primary)}个首选、{len(data.alternates)}个备选，请补齐后提交，或先保存草稿',422)
    old = current_enrollments(db,actor,semester_id)
    all_ids = sorted(set([e.offering_id for e in old]+data.primary+data.alternates))
    list(db.scalars(select(Offering).where(Offering.id.in_(all_ids)).order_by(Offering.id).with_for_update()))
    offers = validate_selection(db,semester_id,data.primary,data.alternates)
    old_ids = {e.offering_id for e in old}
    for oid in data.primary:
        offer = offers[oid]
        # The Submit Schedule use case (PDF p. 14) checks prerequisites for
        # newly selected offerings only. A correction to an old grade must not
        # prevent unrelated add/drop changes to an already enrolled course.
        if oid not in old_ids:
            missing = missing_prerequisites(db,actor,offer.course_id,term)
            if missing:
                names = '、'.join(course_label(db, cid) for cid in sorted(missing))
                raise DomainError('PREREQUISITE_NOT_MET', f'{offering_label(db, offer)}未满足先修要求：{names}尚无符合条件的通过成绩。先修课须在此前已结束的学期取得 A、B、C 或 D；请核对成绩或更换首选，也可先保存草稿', details={'offering_id':oid,'missing_course_ids':sorted(missing)})
        if oid not in old_ids and offer.enrolled_count>=offer.capacity:
            raise DomainError('COURSE_FULL', f'{offering_label(db, offer)}已满员（已选{offer.enrolled_count}人／容量{offer.capacity}人，剩余0个名额）；请刷新余量后更换首选，或将其作为备选、先保存草稿', details={'offering_id':oid,'enrolled_count':offer.enrolled_count,'capacity':offer.capacity,'available':0})
        conflicts = conflicting(db,oid,[x for x in data.primary if x!=oid])
        if conflicts:
            names = '、'.join(offering_label(db, offers[x]) for x in conflicts)
            raise DomainError('SCHEDULE_CONFLICT', f'{offering_label(db, offer)}与{names}上课时间冲突（存在重叠）；请查看课程时间并更换其中一个首选教学班，当前正式课表未改变', details={'offering_ids':[oid]+conflicts})
    # All validation precedes any removal: an unsuccessful update retains the old schedule.
    for e in old:
        if e.offering_id not in data.primary:
            e.state = 'DROPPED'
            e.status_reason = '学生调整课表时退选'
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
            enrollment.status_reason = None
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
    require(not row.deleted and row.version>0,'SCHEDULE_NOT_FOUND','当前学期没有可删除的课表，可能尚未保存或已被删除；请刷新页面确认',404)
    check_version(row.version,version)
    old = current_enrollments(db,actor,semester_id)
    list(db.scalars(select(Offering).where(Offering.id.in_([e.offering_id for e in old])).order_by(Offering.id).with_for_update()))
    for e in old:
        e.state = 'DROPPED'
        e.status_reason = '学生删除了本学期课表'
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
    require(old_ids==set(data.expected_ids),'VERSION_CONFLICT','本次任课修改未保存：任课安排已被其他操作更新。请刷新查看最新安排，再重新选择教学班')
    require(len(set(data.offering_ids))==len(data.offering_ids),'DUPLICATE_OFFERING','任课列表中存在重复教学班；每个教学班只能选择一次，请移除重复项后重试',422)
    offers = list(db.scalars(select(Offering).where(Offering.id.in_(sorted(old_ids|set(data.offering_ids)))).order_by(Offering.id).with_for_update().execution_options(populate_existing=True)))
    selected = {o.id:o for o in offers if o.id in data.offering_ids}
    require(len(selected)==len(data.offering_ids),'INVALID_OFFERING','部分所选教学班已不存在，无法保存任课安排；请刷新课程目录后重新选择',422)
    qualified = set(db.scalars(select(Qualification.course_id).where(Qualification.professor_id==actor)))
    for oid,o in selected.items():
        if o.semester_id!=semester_id or o.state!='OPEN':
            raise DomainError('OFFERING_CLOSED', f'{offering_label(db, o)}不属于当前所选的开放学期，或已关闭、取消；请刷新课程目录并选择开放的教学班')
        if o.course_id not in qualified:
            raise DomainError('NOT_QUALIFIED', f'您尚未获得{course_label(db, o.course_id)}的授课资格，不能选择教学班 {o.code}；请联系教务管理员核对任课资格', 403)
        if o.professor_id not in {None,actor}:
            raise DomainError('PROFESSOR_ASSIGNED', f'{offering_label(db, o)}已由其他教授认领；请刷新任课列表并选择尚未认领的教学班')
        conflicts=conflicting(db,oid,[i for i in selected if i!=oid])
        if conflicts:
            names = '、'.join(offering_label(db, selected[i]) for i in conflicts)
            raise DomainError('TEACHING_CONFLICT', f'{offering_label(db, o)}与{names}授课时间冲突（存在重叠）；请查看上课时间并取消其中一个教学班的选择，原任课安排未改变', details={'offering_ids':[oid]+conflicts})
    for o in offers:
        if o.id in selected:
            o.professor_id = actor
            record_teaching(db,actor,o.id,'ASSIGNED')
        elif o.professor_id==actor:
            o.professor_id = None
            record_teaching(db,actor,o.id,'WITHDRAWN','教授主动取消任课')
    audit(db,actor,'UPDATE_TEACHING',f'semester:{semester_id}',{'offering_ids':data.offering_ids,'previous_ids':sorted(old_ids)})
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
            enrollment.status_reason = reason
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
                enrollment.status_reason = None
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
        invoice=db.scalar(select(Invoice).where(Invoice.student_id==row.student_id,Invoice.semester_id==semester_id).with_for_update())
        if invoice is None:
            invoice=Invoice(student_id=row.student_id,semester_id=semester_id)
            db.add(invoice)
        invoice.total,invoice.lines=total,lines
        invoice.status,invoice.revision,invoice.created_at='ACTIVE',term.version+1,utcnow()
        emit(db,'InvoiceFinalized',{'student_id':row.student_id,'semester_id':semester_id,'total':str(total),'lines':lines,'revision':invoice.revision})
        row.version += 1
        row.draft = None
    term.state,term.closed_at = 'CLOSED',utcnow()
    term.version += 1
    summary={'semester_id':term.id,'cancelled':cancelled,'promoted':promoted,'invoices':len(schedules),'confirmed_offerings':sum(o.state=='CLOSED' for o in offers)}
    term.close_summary=summary
    audit(db,actor,'CLOSE_REGISTRATION',f'semester:{term.id}',summary)
    db.flush()
    return summary


def complete_semester(db,actor,semester_id,version):
    term=db.scalar(select(Semester).where(Semester.id==semester_id).with_for_update(nowait=True).execution_options(populate_existing=True))
    require(term,'SEMESTER_NOT_FOUND','未找到所选学期，请刷新学期列表后重新选择',404)
    check_version(term.version,version)
    require(term.state=='CLOSED','REGISTRATION_STILL_OPEN','不能直接结课：该学期尚未结束选课，请先点击“结束选课”，确认最终教学班和学生名单')
    require(not academic.is_completed(term),'SEMESTER_COMPLETED','该学期已经结课，无需重复操作；教授可在本人的“名单与成绩”中录入成绩')
    term.completed_at=utcnow()
    term.version+=1
    audit(db,actor,'COMPLETE_SEMESTER',f'semester:{term.id}',{'completed_at':term.completed_at.isoformat(),'planned_end_date':term.end_date.isoformat()})
    db.flush()
    return {'semester_id':term.id,'completed':True,'completed_at':term.completed_at.isoformat(),'version':term.version}


def reopen_registration(db,actor,semester_id,data):
    # Same NOWAIT exclusive lock as closing: never reopen over a live mutation.
    term=db.scalar(select(Semester).where(Semester.id==semester_id).with_for_update(nowait=True).execution_options(populate_existing=True))
    require(term,'SEMESTER_NOT_FOUND','未找到所选学期，请刷新学期列表后重新选择',404)
    check_version(term.version,data.version)
    require(term.state=='CLOSED','REGISTRATION_ALREADY_OPEN','该学期选课已开放，无需重复开启；如需延长选课时间，请使用“调整开放时间”')
    now=utcnow()
    require(not academic.is_completed(term),'SEMESTER_COMPLETED',f'“{term.name}”已结课，不能重新开启选课；请保留最终名单并在教授端录入或修改成绩')
    opening=data.registration_open.astimezone(timezone.utc).replace(tzinfo=None)
    closing=data.registration_close.astimezone(timezone.utc).replace(tzinfo=None)
    require(closing>now,'INVALID_WINDOW','重新开启失败：选课截止时间必须晚于当前时间，请重新选择截止时间',422,
            [field_detail('body.registration_close','选课截止时间已过去，请选择未来时间')])
    require(closing.date()<=term.end_date,'INVALID_WINDOW',f'选课截止时间不能晚于学期结束日期（{term.end_date:%Y-%m-%d}，按UTC日期核对）',422,
            [field_detail('body.registration_close',f'截止时间转换为UTC后的日期不能晚于{term.end_date:%Y-%m-%d}')])
    graded=db.scalar(select(Enrollment.id).join(Offering).where(Offering.semester_id==semester_id,Enrollment.grade.is_not(None)).limit(1))
    require(graded is None,'GRADES_ALREADY_RECORDED','该学期已有录入的成绩，不能重新开启选课；请保留现有课表与成绩并联系教务核对')
    offers=list(db.scalars(select(Offering).where(Offering.semester_id==semester_id).order_by(Offering.id).with_for_update()))
    active_professors=set(db.scalars(select(User.id).where(User.role=='professor',User.active.is_(True))))
    # Deactivation while CLOSED intentionally kept history. Those relations
    # must no longer occupy seats once the semester accepts registration again.
    inactive_students=set(db.scalars(select(User.id).where(User.role=='student',User.active.is_(False))))
    for enrollment in db.scalars(select(Enrollment).join(Offering).where(Offering.semester_id==semester_id,Enrollment.student_id.in_(inactive_students),Enrollment.state=='ENROLLED')):
        enrollment.state='DROPPED'
        enrollment.status_reason='账户已停用，重新开启选课时释放名额'
        db.get(Offering,enrollment.offering_id).enrolled_count-=1
    for offer in offers:
        offer.state,offer.cancellation_reason='OPEN',None
        if offer.professor_id not in active_professors:
            if offer.professor_id is not None:
                record_teaching(db,offer.professor_id,offer.id,'WITHDRAWN','账户已停用，重新开启选课时撤销任课')
            offer.professor_id=None
    schedules=list(db.scalars(select(Schedule).where(Schedule.semester_id==semester_id).order_by(Schedule.id).with_for_update()))
    for row in schedules:
        # Keep confirmed seats and the original submission order. Cancelled seats
        # are not restored: alternates may already have filled those places.
        courses={db.get(Offering,e.offering_id).course_id for e in current_enrollments(db,row.student_id,semester_id)}
        for alt in list(db.scalars(select(Alternate).where(Alternate.schedule_id==row.id))):
            if row.student_id in inactive_students or db.get(Offering,alt.offering_id).course_id in courses:
                db.delete(alt)
        if row.student_id in inactive_students:
            row.deleted=True
        row.version+=1
        row.draft=None
    invoices=list(db.scalars(select(Invoice).where(Invoice.semester_id==semester_id,Invoice.status=='ACTIVE').order_by(Invoice.id).with_for_update()))
    for invoice in invoices:
        invoice.status,invoice.revision='VOIDED',term.version+1
        emit(db,'InvoiceVoided',{'student_id':invoice.student_id,'semester_id':semester_id,'total':'0.00','lines':[],'revision':invoice.revision})
    previous_summary=term.close_summary
    term.state,term.closed_at,term.close_summary='OPEN',None,None
    term.registration_open,term.registration_close=opening,closing
    term.version+=1
    result={'semester_id':semester_id,'version':term.version,'reopened_offerings':len(offers),'voided_invoices':len(invoices)}
    audit(db,actor,'REOPEN_REGISTRATION',f'semester:{semester_id}',{
        **result,'previous_close_summary':previous_summary,
        'registration_open':data.registration_open.isoformat(),'registration_close':data.registration_close.isoformat()})
    db.flush()
    return result
