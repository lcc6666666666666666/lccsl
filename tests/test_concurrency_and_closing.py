from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import time
import pytest
from sqlalchemy import select,func,delete
from sqlalchemy.exc import OperationalError
from backend.app.db import SessionLocal,utcnow
from backend.app.models import Offering,Semester,Schedule,Enrollment,Alternate,Invoice,Outbox,User,Meeting
from backend.app.schemas import ScheduleInput,TeachingInput
from backend.app.errors import DomainError
from backend.app import services as svc
from .conftest import submit

def race(actions):
    start=Barrier(len(actions))
    def run(action):
        start.wait(timeout=10)
        with SessionLocal() as db:
            try:
                result=action(db)
                db.commit()
                return ('ok',result)
            except DomainError as exc:
                db.rollback()
                return (exc.code,None)
    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run,actions))

def check_counts(db):
    db.expire_all()
    for offer in db.scalars(select(Offering)):
        count=db.scalar(select(func.count(Enrollment.id)).where(Enrollment.offering_id==offer.id,Enrollment.state=='ENROLLED'))
        assert count==offer.enrolled_count and 0<=count<=10

def test_race_for_last_seat(db):
    e=db.scalar(select(Enrollment).where(Enrollment.student_id==113,Enrollment.offering_id==11))
    e.state='DROPPED';db.get(Offering,11).enrolled_count=9;db.commit()
    body=ScheduleInput(version=0,primary=[1,2,3,11],alternates=[5,6])
    results=race([lambda s:svc.submit_schedule(s,101,2,body),lambda s:svc.submit_schedule(s,102,2,body)])
    assert sorted(x[0] for x in results)==['COURSE_FULL','ok']
    check_counts(db)

def test_same_schedule_concurrent_idempotence(db):
    body=ScheduleInput(version=0,primary=[1,2,3,4],alternates=[5,6])
    def request(s):
        return svc.idempotent(s,101,'submit:2','concurrent-idempotent',body.model_dump(),lambda:svc.submit_schedule(s,101,2,body),semester_id=2)
    results=race([request,request])
    assert results[0]==results[1] and results[0][0]=='ok'
    assert db.scalar(select(func.count(Outbox.id)))==1
    check_counts(db)

def test_same_schedule_concurrent_different_requests(db):
    first=ScheduleInput(version=0,primary=[1,2,3,4],alternates=[5,6])
    second=ScheduleInput(version=0,primary=[1,2,3,5],alternates=[4,6])
    results=race([lambda s:svc.submit_schedule(s,101,2,first),lambda s:svc.submit_schedule(s,101,2,second)])
    assert sorted(x[0] for x in results)==['VERSION_CONFLICT','ok']
    check_counts(db)

def test_concurrent_professor_claim(db):
    one=TeachingInput(offering_ids=[1,2,3,10],expected_ids=[1,2,3])
    two=TeachingInput(offering_ids=[4,5,6,10],expected_ids=[4,5,6])
    results=race([lambda s:svc.update_teaching(s,11,2,one),lambda s:svc.update_teaching(s,12,2,two)])
    assert sorted(x[0] for x in results)==['PROFESSOR_ASSIGNED','ok']
    db.expire_all()
    assert db.get(Offering,10).professor_id in {11,12}

def test_close_rejects_in_progress_immediately(client,db):
    admin=client('admin')
    svc.lock_semester(db,2)
    started=time.monotonic()
    result=admin.post('/api/v1/registrar/semesters/2/close',json={'version':1},headers={'Idempotency-Key':'close-while-busy'})
    elapsed=time.monotonic()-started
    assert result.status_code==409 and result.json()['code']=='RESOURCE_BUSY',result.text
    assert elapsed<2
    db.rollback()
    assert db.get(Semester,2).state=='OPEN'

def test_close_blocks_new_submission_and_rolls_back(db):
    svc.lock_semester(db,2,close=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def closing_waiter():
            with SessionLocal() as other:
                result=svc.submit_schedule(other,101,2,ScheduleInput(version=0,primary=[1,2,3,4],alternates=[5,6]))
                other.commit()
                return result
        future=pool.submit(closing_waiter)
        # The waiting transaction must not bypass the semester lock.
        time.sleep(.15)
        assert not future.done()
        db.rollback()
        assert future.result(timeout=10)['version']==1
    check_counts(db)

def test_close_cancellation_promotion_billing_idempotency(client,db):
    for name in ['student01','student02','student03']:
        assert submit(client(name),[1,2,3,10],[4,5]).status_code==200
    admin=client('admin')
    r=admin.post('/api/v1/registrar/semesters/2/close',json={'version':1},headers={'Idempotency-Key':'close-complete-01'})
    assert r.status_code==200,r.text
    summary=r.json()
    assert 10 in [x['id'] for x in summary['cancelled']]
    assert all({'student_id':uid,'offering_id':4} in summary['promoted'] for uid in [101,102,103])
    assert admin.post('/api/v1/registrar/semesters/2/close',json={'version':1},headers={'Idempotency-Key':'close-complete-01'}).json()==summary
    assert db.scalar(select(func.count(Invoice.id)))==summary['invoices']
    assert db.scalar(select(func.count(Outbox.id)).where(Outbox.kind=='InvoiceFinalized'))==summary['invoices']
    assert client().get('/api/v1/student/schedules/2').json()['primary']==[1,2,3,4]
    assert client().get('/api/v1/student/invoices').json()[0]['total']=='2400.00'
    assert submit(client(),version=2).json()['code']=='REGISTRATION_CLOSED'
    assert client('professor01').put('/api/v1/professor/teaching/2',json={'offering_ids':[],'expected_ids':[1,2,3]}).json()['code']=='REGISTRATION_CLOSED'
    check_counts(db)

def test_close_failure_is_all_or_nothing(db,monkeypatch):
    original=svc.emit
    def fail(db,kind,payload):
        if kind=='InvoiceFinalized':
            raise RuntimeError('Injected database transaction failure')
        return original(db,kind,payload)
    monkeypatch.setattr(svc,'emit',fail)
    with pytest.raises(RuntimeError):
        svc.close_registration(db,1,2,1)
    db.rollback()
    assert db.get(Semester,2).state=='OPEN'
    assert db.get(Offering,10).state=='OPEN'
    assert db.scalar(select(func.count(Invoice.id)))==0
    assert db.scalar(select(func.count(Outbox.id)))==0
    check_counts(db)

def clear_open_schedules(db):
    db.execute(delete(Alternate))
    db.execute(delete(Schedule))
    db.execute(delete(Enrollment).where(Enrollment.offering_id<100))
    for o in db.scalars(select(Offering).where(Offering.semester_id==2)):
        o.enrolled_count=0
    db.commit()

def make_schedule(db,uid,primary,alternates,submitted):
    s=Schedule(student_id=uid,semester_id=2,version=1,first_submitted_at=submitted)
    db.add(s);db.flush()
    for i,oid in enumerate(primary):
        db.add(Enrollment(student_id=uid,offering_id=oid,position=i))
        db.get(Offering,oid).enrolled_count+=1
    for i,oid in enumerate(alternates):
        db.add(Alternate(schedule_id=s.id,offering_id=oid,position=i))
    db.flush()

def test_alternates_check_prerequisites_conflicts_capacity_order_single_pass(db):
    clear_open_schedules(db)
    now=utcnow()
    # Nine existing seats; the earlier of two applicants gets the last seat.
    for uid in range(104,113):
        make_schedule(db,uid,[11],[],now-timedelta(hours=2))
    make_schedule(db,102,[1,2,10],[11,5],now)
    make_schedule(db,101,[1,2,10],[11,6],now)
    # F prerequisites and a time collision each prevent promotion.
    make_schedule(db,103,[4,10],[7,9],now+timedelta(seconds=1))
    # Two-seat offering survives filling stage, then is cancelled, no second pass.
    make_schedule(db,116,[12],[],now)
    make_schedule(db,117,[12],[],now)
    db.commit()
    result=svc.close_registration(db,1,2,1);db.commit()
    assert {'student_id':101,'offering_id':11} in result['promoted']
    assert {'student_id':102,'offering_id':11} not in result['promoted']
    assert not any(x['student_id']==103 for x in result['promoted'])
    assert db.get(Offering,12).state=='CANCELLED'
    assert db.get(Offering,11).enrolled_count==10
    check_counts(db)

def test_first_submit_microseconds_precede_student_id(db):
    clear_open_schedules(db)
    now=utcnow().replace(microsecond=100000)
    for uid in range(104,113):make_schedule(db,uid,[11],[],now-timedelta(hours=1))
    # Higher student number applied 0.1 s earlier; timestamp must win over ID.
    make_schedule(db,102,[1],[11],now)
    make_schedule(db,101,[1],[11],now+timedelta(microseconds=100000))
    db.commit();db.expire_all()
    result=svc.close_registration(db,1,2,1);db.commit()
    assert {'student_id':102,'offering_id':11} in result['promoted']
    assert {'student_id':101,'offering_id':11} not in result['promoted']
