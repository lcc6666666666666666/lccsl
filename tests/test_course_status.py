"""Student and professor results must agree with final schedules and invoices."""
from datetime import timedelta,timezone
import pytest
from sqlalchemy import select
from backend.app import services as svc
from backend.app.db import utcnow
from backend.app.models import Offering,Semester,TeachingAssignment,Enrollment
from backend.app.schemas import ScheduleInput,TeachingInput,SemesterInput
from .conftest import submit

def snapshot(client,role='student',semester=2):
    path='registration-status' if role=='student' else 'teaching-status'
    return client.get(f'/api/v1/{role}/{path}/{semester}')


def lcc_scenario(db,student):
    for oid in (2,3):db.get(Offering,oid).professor_id=None
    db.commit()
    assert submit(student,[1,2,3,4],[7,8]).status_code==200
    svc.close_registration(db,1,2,1);db.commit()


def test_four_requested_two_cancelled_agree_with_two_line_invoice(db,client):
    student=client('student14')
    lcc_scenario(db,student)
    result=snapshot(student)
    assert result.status_code==200,result.text
    data=result.json();rows={r['offering_id']:r for r in data['items']}
    assert data['schedule']['primary']==data['effective_ids']==[1,4]
    assert data['summary']=={'registered':2,'cancelled':2,'withdrawn':0,'billed':2,'billing_consistent':True}
    assert [x['offering_id'] for x in data['invoice']['lines']]==[1,4]
    for oid in (1,4):assert rows[oid]['status']=='CONFIRMED' and rows[oid]['billing_label']=='已计入本学期账单'
    for oid in (2,3):
        assert rows[oid]['status']=='CANCELLED' and rows[oid]['reason']=='没有有效授课教授'
        assert rows[oid]['billing_label']=='不计费'
    assert rows[7]['status']==rows[8]['status']=='NOT_PROMOTED'


def test_open_registration_and_draft_are_not_final_or_charged(client):
    student=client()
    assert submit(student).status_code==200
    data=snapshot(student).json()
    assert data['invoice'] is None and data['summary']['billed']==0
    assert [i['status'] for i in data['items']]==['REGISTERED']*4+['ALTERNATE']*2
    response=student.put('/api/v1/student/schedules/2/draft',json={'version':1,'primary':[1,7],'alternates':[8]})
    assert response.status_code==200
    data=snapshot(student).json();rows={i['offering_id']:i for i in data['items']}
    assert data['effective_ids']==[1,2,3,4]
    assert rows[7]['status']==rows[8]['status']=='DRAFT'
    assert rows[1]['status']=='REGISTERED' and rows[1]['draft_choice']=='草稿首选'
    assert rows[7]['billing_label']=='不计费'


def test_withdrawal_and_delete_remain_visible(client):
    student=client()
    assert submit(student).status_code==200
    assert submit(student,[1,2,3],[5,6],version=1).status_code==200
    rows={i['offering_id']:i for i in snapshot(student).json()['items']}
    assert rows[4]['status']=='WITHDRAWN' and '调整课表' in rows[4]['reason']
    assert student.request('DELETE','/api/v1/student/schedules/2',json={'version':2}).status_code==200
    data=snapshot(student).json()
    assert data['effective_ids']==[] and all(i['status']=='WITHDRAWN' for i in data['items'])


def test_cancel_reason_survives_reopen_and_reselection_clears_it(db,client):
    student=client('student14');lcc_scenario(db,student)
    now=utcnow().replace(tzinfo=timezone.utc)
    svc.reopen_registration(db,1,2,SemesterInput(version=2,registration_open=now-timedelta(minutes=1),registration_close=now+timedelta(days=1)))
    db.commit()
    data=snapshot(student).json();rows={i['offering_id']:i for i in data['items']}
    assert rows[2]['status']=='CANCELLED' and rows[2]['reason']=='没有有效授课教授'
    assert data['invoice']['status']=='VOIDED' and data['summary']['billed']==0
    assert rows[1]['status']=='REGISTERED' and rows[1]['billing_label']=='待结束选课结算'
    assert submit(student,[1,2,4],[],version=3).status_code==200
    rows={i['offering_id']:i for i in snapshot(student).json()['items']}
    assert rows[2]['status']=='REGISTERED'
    db.expire_all()
    assert db.scalar(select(Enrollment).where(Enrollment.student_id==114,Enrollment.offering_id==2)).status_reason is None


def test_teacher_cancelled_class_excluded_from_timetable_and_exit_retained(db,client):
    professor=client('professor03')
    # Seeded 7/8/9 have no students and will be cancelled for insufficient seats.
    before=snapshot(professor,'professor').json()
    assert before['offering_ids']==before['effective_ids']==[7,8,9]
    svc.close_registration(db,1,2,1);db.commit()
    data=snapshot(professor,'professor').json()
    assert data['offering_ids']==[7,8,9] and data['effective_ids']==[]
    assert all(i['status']=='CANCELLED' and i['reason']=='最终选课人数不足3人' for i in data['items'])


def test_teacher_claim_exit_reclaim_and_failed_request_history(db,client):
    professor=client('professor01')
    result=professor.put('/api/v1/professor/teaching/2',json={'offering_ids':[1,2,3,10],'expected_ids':[1,2,3]})
    assert result.status_code==200,result.text
    assert next(i for i in snapshot(professor,'professor').json()['items'] if i['offering_id']==10)['status']=='ASSIGNED'
    result=professor.put('/api/v1/professor/teaching/2',json={'offering_ids':[1,2,3],'expected_ids':[1,2,3,10]})
    assert result.status_code==200
    rows={i['offering_id']:i for i in snapshot(professor,'professor').json()['items']}
    assert rows[10]['status']=='WITHDRAWN' and rows[10]['reason']=='教授主动取消任课'
    # Invalid claim does not invent a successful application record.
    result=professor.put('/api/v1/professor/teaching/2',json={'offering_ids':[1,2,3,4],'expected_ids':[1,2,3]})
    assert result.status_code==409
    assert db.get(TeachingAssignment,(11,4)) is None
    result=professor.put('/api/v1/professor/teaching/2',json={'offering_ids':[1,2,3,10],'expected_ids':[1,2,3]})
    assert result.status_code==200
    assert next(i for i in snapshot(professor,'professor').json()['items'] if i['offering_id']==10)['status']=='ASSIGNED'


@pytest.mark.parametrize('user,path',[('student01','/professor/teaching-status/2'),('professor01','/student/registration-status/2'),('admin','/student/registration-status/2')])
def test_status_role_boundary(client,user,path):
    assert client(user).get('/api/v1'+path).status_code==403


def test_status_identity_scope_and_term_filter(client):
    one=client();two=client('student02')
    assert submit(one).status_code==200
    assert snapshot(two).json()['items']==[]
    assert two.get('/api/v1/student/registration-status/2?student_id=101').status_code==403
    assert snapshot(one,semester=999).status_code==404
    past=snapshot(one,semester=1).json()
    assert all(i['offering_id']>=101 for i in past['items'])
    assert past['invoice'] is None


def test_status_reads_refuse_half_finished_close(db,client):
    student=client();professor=client('professor01')
    svc.lock_semester(db,2,close=True)
    for c,role in ((student,'student'),(professor,'professor')):
        result=snapshot(c,role)
        assert result.status_code==409 and result.json()['code']=='RESOURCE_BUSY'
    db.rollback()


def test_teaching_history_rolls_back_with_failed_transaction(db,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('audit failure')
    monkeypatch.setattr(svc,'audit',fail)
    with pytest.raises(RuntimeError):svc.update_teaching(db,11,2,TeachingInput(offering_ids=[1,2,3,10],expected_ids=[1,2,3]))
    db.rollback()
    assert db.get(Offering,10).professor_id is None
    assert db.get(TeachingAssignment,(11,10)) is None
