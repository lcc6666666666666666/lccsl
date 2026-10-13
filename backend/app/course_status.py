"""Consistent, identity-scoped views of registration, teaching and billing results."""
from sqlalchemy import select, or_
from .models import User, Semester, Schedule, Enrollment, Alternate, Offering, Course, Invoice, TeachingAssignment
from .errors import require
from . import services as svc
from . import academic


def lock_snapshot(db,actor,semester_id):
    # Semester mutations take an exclusive parent lock; personal edits take an
    # exclusive user lock. Holding both shared locks yields a coherent snapshot.
    term=db.scalar(select(Semester).where(Semester.id==semester_id).with_for_update(read=True,nowait=True))
    require(term,'SEMESTER_NOT_FOUND','未找到所选学期，请刷新学期列表后重新选择',404)
    user=db.scalar(select(User).where(User.id==actor).with_for_update(read=True,nowait=True).execution_options(populate_existing=True))
    require(user and user.active,'ACCOUNT_DISABLED','当前账户已停用，请联系教务管理员',403)
    return term


def invoice_view(invoice):
    if invoice is None:return None
    return {'id':invoice.id,'semester_id':invoice.semester_id,'total':str(invoice.total),
            'lines':invoice.lines,'created_at':invoice.created_at,'status':invoice.status,'revision':invoice.revision}


def registration_status(db,actor,term):
    schedule=svc.schedule_view(db,actor,term.id)
    row=db.scalar(select(Schedule).where(Schedule.student_id==actor,Schedule.semester_id==term.id))
    enrolled={e.offering_id:e for e in db.scalars(select(Enrollment).join(Offering).where(Enrollment.student_id==actor,Offering.semester_id==term.id))}
    alternates={a.offering_id:a.position+1 for a in db.scalars(select(Alternate).where(Alternate.schedule_id==row.id))} if row else {}
    draft=schedule['draft'] or {'primary':[],'alternates':[]}
    ids=set(enrolled)|set(alternates)|set(draft['primary'])|set(draft['alternates'])
    invoice=db.scalar(select(Invoice).where(Invoice.student_id==actor,Invoice.semester_id==term.id))
    charged={line['offering_id'] for line in invoice.lines} if invoice and invoice.status=='ACTIVE' else set()
    rows=db.execute(select(Offering,Course).join(Course).where(Offering.semester_id==term.id,Offering.id.in_(ids)).order_by(Offering.id)).all()
    items=[]
    for offer,course in rows:
        enrollment=enrolled.get(offer.id)
        registered=enrollment is not None and enrollment.state=='ENROLLED'
        choice='正式选课'
        if registered and offer.state!='CANCELLED':
            if term.state=='CLOSED':
                status,label,reason='CONFIRMED','开课成功','选课已结束，已进入最终课表'
                if academic.is_completed(term):label,reason='已结课','学期已结束，可在“学期成绩单”查看教授录入的成绩'
            else:
                status,label='REGISTERED','选课成功，待确认开班'
                reason='已注册并占用名额；结束选课时确认能否开班'
                if offer.professor_id is None:reason+='；目前未安排教授，结束时仍无教授将取消'
                elif offer.enrolled_count<3:reason+='；目前不足3人，结束时仍不足3人将取消'
        elif offer.id in alternates and term.state=='OPEN':
            status,label,reason='ALTERNATE','备选等待补位','仅保存备选顺序，不占名额；结束选课时按条件尝试补位'
            choice=f'备选第{alternates[offer.id]}顺位'
        elif enrollment is not None and enrollment.state=='CANCELLED':
            status,label='CANCELLED','选课已取消'
            reason=enrollment.status_reason or offer.cancellation_reason or '结束选课时教学班被取消，原系统未记录详细原因'
        elif offer.id in alternates:
            status,label='NOT_PROMOTED','备选未入选'
            choice=f'备选第{alternates[offer.id]}顺位'
            reason=offer.cancellation_reason if offer.state=='CANCELLED' else '结束选课时未转为正式选课；可能已满4门或未满足补位条件'
        elif enrollment is not None and enrollment.state=='DROPPED':
            status,label,reason='WITHDRAWN','已退课',enrollment.status_reason or '已从正式课表中移除'
        elif registered:
            status,label,reason='CANCELLED','选课已取消',offer.cancellation_reason or '教学班已取消'
        else:
            status,label,reason='DRAFT','草稿未提交','仅保存在草稿中，不占名额；正式提交成功后才生效'
            choice='草稿'
        effective=status in {'REGISTERED','CONFIRMED'}
        draft_choice='草稿首选' if offer.id in draft['primary'] else '草稿备选' if offer.id in draft['alternates'] else None
        items.append({'offering_id':offer.id,'code':offer.code,'course_code':course.code,'course':course.name,
                      'status':status,'status_label':label,'reason':reason,'selection':choice,'draft_choice':draft_choice,
                      'offering_state':offer.state,'fee':str(offer.fee),'effective':effective,
                      'billing_label':'已计入本学期账单' if offer.id in charged else '待结束选课结算' if effective and term.state=='OPEN' else '账单待核对' if effective else '不计费'})
    effective_ids=[item['offering_id'] for item in items if item['effective']]
    # A revoked invoice is history, not the cost of the current open registration.
    return {'schedule':schedule,'items':items,'invoice':invoice_view(invoice),'effective_ids':effective_ids,
            'summary':{'registered':len(effective_ids),'cancelled':sum(i['status']=='CANCELLED' for i in items),
                       'withdrawn':sum(i['status']=='WITHDRAWN' for i in items),'billed':len(charged),
                       'billing_consistent':set(effective_ids)==charged if term.state=='CLOSED' and invoice and invoice.status=='ACTIVE' else None}}


def teaching_status(db,actor,term):
    rows=db.execute(select(Offering,Course,TeachingAssignment).join(Course).outerjoin(
        TeachingAssignment,(TeachingAssignment.offering_id==Offering.id)&(TeachingAssignment.professor_id==actor)
    ).where(Offering.semester_id==term.id,or_(Offering.professor_id==actor,TeachingAssignment.professor_id==actor)).order_by(Offering.id)).all()
    items=[];assigned=[];effective=[]
    for offer,course,history in rows:
        if offer.professor_id==actor:
            assigned.append(offer.id)
            if offer.state=='CANCELLED':
                status,label,reason='CANCELLED','教学班已取消',offer.cancellation_reason or '结束选课时未能开班'
            elif term.state=='CLOSED':
                status,label,reason='CONFIRMED','任课成功，已确认开班','该教学班已进入最终授课安排'
                if academic.is_completed(term):label,reason='已结课，可录入成绩','学期已结束，可在“名单与成绩”中录入或修改成绩'
                effective.append(offer.id)
            else:
                status,label,reason='ASSIGNED','认领成功，待确认开班','任课已保存，结束选课时确认能否开班'
                if offer.enrolled_count<3:reason+='；目前不足3人，结束时仍不足3人将取消'
                effective.append(offer.id)
        else:
            status,label,reason='WITHDRAWN','已取消任课',history.reason or '任课安排已变更，当前不由本人授课'
        items.append({'offering_id':offer.id,'code':offer.code,'course_code':course.code,'course':course.name,
                      'status':status,'status_label':label,'reason':reason,'offering_state':offer.state,
                      'enrolled_count':offer.enrolled_count,'capacity':offer.capacity,'effective':offer.id in effective})
    return {'offering_ids':assigned,'effective_ids':effective,'items':items}
