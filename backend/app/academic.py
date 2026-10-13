"""One definition of completed teaching for grading, reports and prerequisites."""
from sqlalchemy import and_, or_, case, func
from .db import utcnow
from .models import Semester


def is_completed(term):
    return term.state=='CLOSED' and (term.completed_at is not None or term.end_date<utcnow().date())


def completed_condition():
    return and_(Semester.state=='CLOSED',or_(Semester.completed_at.is_not(None),Semester.end_date<utcnow().date()))


def completion_date():
    return case((Semester.completed_at.is_not(None),func.date(Semester.completed_at)),else_=Semester.end_date)


def grading_status(term,offering):
    if offering.state=='CANCELLED':
        return {'can_grade':False,'grading_message':f'该教学班已取消（{offering.cancellation_reason or "未能开班"}），不能录入成绩。'}
    if term.state!='CLOSED' or offering.state!='CLOSED':
        return {'can_grade':False,'grading_message':'选课尚未结束或教学班尚未确认开班，当前只能查看名单。请管理员先结束选课，再结束学期以开放成绩录入。'}
    if not is_completed(term):
        return {'can_grade':False,'grading_message':f'该教学班已结束选课并确认开班，但学期尚未结课。计划结束日期为 {term.end_date:%Y-%m-%d}；管理员可在“学期与选课”中点击“结束学期，开放成绩录入”，无需等待该日期。'}
    return {'can_grade':True,'grading_message':'学期已结课，可录入或修改 A、B、C、D、F、I；暂未确定的成绩可留空。'}
