"""Reopening must preserve seats/history and supersede even delayed billing events."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from itertools import permutations
import time
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from backend.app import services as svc
from backend.app.db import SessionLocal, utcnow
from backend.app.errors import DomainError
from backend.app.mocks import Event, accept_event
from backend.app.models import (
    Audit, BillingInvoice, Enrollment, Invoice, Offering,
    Outbox, Schedule, Semester, User,
)
from backend.app.schemas import ScheduleInput, SemesterInput, TeachingInput
from backend.app.worker import process_one
from .test_concurrency_and_closing import check_counts


def window(version=2):
    now=utcnow().replace(tzinfo=timezone.utc)
    return SemesterInput(version=version,registration_open=now-timedelta(minutes=1),registration_close=now+timedelta(days=1))


def close_seed(db):
    for uid in (101,102,103):
        svc.submit_schedule(db,uid,2,ScheduleInput(version=0,primary=[1,2,3,10],alternates=[4,5]))
    summary=svc.close_registration(db,1,2,1)
    db.commit()
    return summary


def reopen_request(admin,data=None,key='reopen-request-001',semester=2):
    return admin.post(f'/api/v1/registrar/semesters/{semester}/reopen',json=(data or window()).model_dump(mode='json'),headers={'Idempotency-Key':key})


def test_reopen_retains_seats_restores_offerings_and_removes_promoted_alternates(db,client):
    summary=close_seed(db)
    first=db.scalar(select(Schedule).where(Schedule.student_id==101,Schedule.semester_id==2)).first_submitted_at
    before={o.id:o.enrolled_count for o in db.scalars(select(Offering).where(Offering.semester_id==2))}
    result=reopen_request(client('admin'))
    assert result.status_code==200,result.text
    assert result.json()['voided_invoices']==summary['invoices']
    db.expire_all()
    term=db.get(Semester,2)
    assert (term.state,term.version,term.closed_at,term.close_summary)==('OPEN',3,None,None)
    for offering in db.scalars(select(Offering).where(Offering.semester_id==2)):
        assert offering.state=='OPEN' and offering.cancellation_reason is None
        assert offering.enrolled_count==before[offering.id]
    view=client().get('/api/v1/student/schedules/2').json()
    assert view['primary']==[1,2,3,4] and view['alternates']==[5] and view['version']==3
    row=db.scalar(select(Schedule).where(Schedule.student_id==101,Schedule.semester_id==2))
    assert row.first_submitted_at==first
    assert db.scalar(select(Enrollment).where(Enrollment.student_id==101,Enrollment.offering_id==10)).state=='CANCELLED'
    assert client().get('/api/v1/student/invoices').json()[0]['status']=='VOIDED'
    assert all(i.status=='VOIDED' and i.revision==3 for i in db.scalars(select(Invoice)))
    audit=db.scalar(select(Audit).where(Audit.action=='REOPEN_REGISTRATION'))
    assert audit.details['previous_close_summary']==summary
    assert db.get(Semester,1).state=='CLOSED'
    check_counts(db)


def test_edit_and_close_again_reuses_invoice_without_double_charging(db):
    close_seed(db)
    invoice_ids={i.student_id:i.id for i in db.scalars(select(Invoice))}
    svc.reopen_registration(db,1,2,window())
    svc.update_teaching(db,11,2,TeachingInput(offering_ids=[1,2,3,10],expected_ids=[1,2,3]))
    # Existing users may choose fewer than four; seat changes remain transactional.
    svc.submit_schedule(db,101,2,ScheduleInput(version=3,primary=[1,2,3],alternates=[5]))
    svc.close_registration(db,1,2,3)
    db.commit()
    assert {i.student_id:i.id for i in db.scalars(select(Invoice))}==invoice_ids
    bill=db.scalar(select(Invoice).where(Invoice.student_id==101))
    assert bill.status=='ACTIVE' and bill.revision==4 and bill.total==1800
    assert db.get(Semester,2).state=='CLOSED'
    assert db.scalar(select(func.count(Outbox.id)).where(Outbox.kind=='InvoiceVoided'))==len(invoice_ids)
    check_counts(db)


def test_deleted_schedule_after_reopen_keeps_old_bill_voided(db):
    close_seed(db)
    svc.reopen_registration(db,1,2,window())
    svc.delete_schedule(db,101,2,3)
    svc.close_registration(db,1,2,3)
    db.commit()
    bill=db.scalar(select(Invoice).where(Invoice.student_id==101))
    assert bill.status=='VOIDED' and bill.revision==3


def test_reopen_is_idempotent_and_replay_does_not_reopen_after_next_close(db,client):
    summary=close_seed(db)
    admin=client('admin'); data=window()
    first=reopen_request(admin,data)
    assert first.status_code==200
    assert reopen_request(admin,data).json()==first.json()
    assert db.scalar(select(func.count(Outbox.id)).where(Outbox.kind=='InvoiceVoided'))==summary['invoices']
    result=admin.post('/api/v1/registrar/semesters/2/close',json={'version':3},headers={'Idempotency-Key':'close-after-reopen'})
    assert result.status_code==200,result.text
    assert reopen_request(admin,data).json()==first.json()
    db.expire_all()
    assert db.get(Semester,2).state=='CLOSED' and db.get(Semester,2).version==4


@pytest.mark.parametrize('username',['student01','professor01'])
def test_only_admin_can_reopen(client,username):
    assert reopen_request(client(username)).status_code==403


def test_reopen_requires_csrf_and_idempotency_key(client,db):
    close_seed(db)
    admin=client('admin')
    assert admin.post('/api/v1/registrar/semesters/2/reopen',json=window().model_dump(mode='json')).json()['code']=='IDEMPOTENCY_REQUIRED'
    del admin.headers['X-CSRF-Token']
    assert reopen_request(admin).status_code==403
    db.expire_all()
    assert db.get(Semester,2).state=='CLOSED'


@pytest.mark.parametrize('scenario,code',[
    ('open','REGISTRATION_ALREADY_OPEN'),('past','SEMESTER_COMPLETED'),
    ('graded','GRADES_ALREADY_RECORDED'),('stale','VERSION_CONFLICT'),
    ('expired','INVALID_WINDOW'),('beyond_term','INVALID_WINDOW'),
])
def test_invalid_reopen_has_specific_error_and_no_mutations(db,client,scenario,code):
    data=window()
    if scenario!='open':
        close_seed(db)
    else:
        data.version=1
    if scenario=='past':
        db.get(Semester,2).start_date=utcnow().date()-timedelta(days=100)
        db.get(Semester,2).end_date=utcnow().date()-timedelta(days=1)
    if scenario=='graded': db.scalar(select(Enrollment).where(Enrollment.student_id==101,Enrollment.offering_id==1)).grade='A'
    if scenario=='stale': data.version=1
    if scenario=='expired':
        data.registration_open-=timedelta(days=3);data.registration_close-=timedelta(days=2)
    if scenario=='beyond_term': data.registration_close+=timedelta(days=365)
    db.commit()
    before=db.scalar(select(func.count(Outbox.id)))
    response=reopen_request(client('admin'),data)
    assert response.status_code in (409,422) and response.json()['code']==code,response.text
    db.expire_all()
    assert db.scalar(select(func.count(Outbox.id)))==before
    assert db.get(Semester,2).state==('OPEN' if scenario=='open' else 'CLOSED')


def test_inactive_professor_is_unassigned_and_old_page_cannot_overwrite(db):
    close_seed(db)
    db.get(User,11).active=False;db.commit()
    svc.reopen_registration(db,1,2,window());db.commit()
    assert db.get(Offering,1).professor_id is None
    with pytest.raises(DomainError) as error:
        svc.submit_schedule(db,101,2,ScheduleInput(version=1,primary=[1,2,3],alternates=[]))
    assert error.value.code=='VERSION_CONFLICT'


def test_disabled_student_does_not_keep_seats_when_reopened(db,client):
    close_seed(db)
    admin=client('admin')
    version=db.get(User,101).version
    original_count=db.get(Offering,1).enrolled_count
    result=admin.request('DELETE','/api/v1/registrar/people/101',json={'version':version})
    assert result.status_code==200,result.text
    # CLOSED history is retained until reopening, then inactive seats are freed.
    db.expire_all()
    assert db.get(Offering,1).enrolled_count==original_count
    svc.reopen_registration(db,1,2,window());db.commit()
    assert svc.schedule_view(db,101,2)['primary']==[]
    assert svc.schedule_view(db,101,2)['alternates']==[]
    assert db.get(Offering,1).enrolled_count==original_count-1
    assert db.scalar(select(Invoice).where(Invoice.student_id==101)).status=='VOIDED'
    check_counts(db)


def test_person_mutation_locks_closed_semester_against_reopen(db,client):
    from backend.app.api import lock_person_mutation
    close_seed(db)
    admin=client('admin')
    lock_person_mutation(db,101)
    result=reopen_request(admin)
    assert result.status_code==409 and result.json()['code']=='RESOURCE_BUSY'
    db.rollback()


def test_reopen_failure_rolls_back_offerings_schedules_and_invoices(db,monkeypatch):
    close_seed(db)
    def fail(*args):raise RuntimeError('injected outbox failure')
    monkeypatch.setattr(svc,'emit',fail)
    with pytest.raises(RuntimeError):svc.reopen_registration(db,1,2,window())
    db.rollback()
    assert db.get(Semester,2).state=='CLOSED' and db.get(Semester,2).version==2
    assert db.get(Offering,10).state=='CANCELLED'
    assert all(i.status=='ACTIVE' for i in db.scalars(select(Invoice)))
    assert svc.schedule_view(db,101,2)['version']==2
    assert svc.schedule_view(db,101,2)['alternates']==[4,5]
    check_counts(db)


def test_reopen_fails_immediately_when_semester_is_busy(db,client):
    close_seed(db)
    admin=client('admin')
    db.scalar(select(Semester).where(Semester.id==2).with_for_update(read=True))
    start=time.monotonic()
    response=reopen_request(admin)
    assert response.status_code==409 and response.json()['code']=='RESOURCE_BUSY'
    assert time.monotonic()-start<2
    db.rollback()


def test_reopen_holds_lock_until_commit_before_new_submission(db):
    close_seed(db)
    svc.reopen_registration(db,1,2,window())
    def submit_after_reopen():
        with SessionLocal() as other:
            result=svc.submit_schedule(other,101,2,ScheduleInput(version=3,primary=[1,2,3],alternates=[]))
            other.commit()
            return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(submit_after_reopen)
        time.sleep(.15)
        assert not future.done()
        db.commit()
        assert future.result(timeout=10)['version']==4
    check_counts(db)


def billing_event(revision,kind='InvoiceFinalized',total='600.00'):
    return Event(event_id=str(uuid.uuid4()),kind=kind,payload={'student_id':101,'semester_id':2,'total':'0.00' if kind=='InvoiceVoided' else total,'lines':[],'revision':revision})


@pytest.mark.parametrize('order',list(permutations((2,3,4))))
def test_billing_handles_every_delivery_order_and_duplicates(db,order):
    events={2:billing_event(2),3:billing_event(3,'InvoiceVoided'),4:billing_event(4,total='1200.00')}
    for revision in order:
        event=events[revision]
        assert accept_event(event)==accept_event(event)
    bill=db.get(BillingInvoice,(101,2))
    assert (bill.status,bill.revision,bill.payload['total'])==('ACTIVE',4,'1200.00')
    assert db.scalar(select(func.count()).select_from(BillingInvoice))==1


def test_legacy_delayed_invoice_cannot_revive_voided_bill(db):
    accept_event(billing_event(3,'InvoiceVoided'))
    legacy=billing_event(0);del legacy.payload['revision']
    accept_event(legacy)
    bill=db.get(BillingInvoice,(101,2))
    assert bill.status=='VOIDED' and bill.revision==3 and bill.payload['total']=='0.00'


@pytest.mark.parametrize('revision',[-1,True,1.5,'3',2147483648])
def test_billing_rejects_invalid_revision(revision):
    with pytest.raises(HTTPException) as error:accept_event(billing_event(revision))
    assert error.value.status_code==422


def test_billing_rejects_conflicting_same_revision(db):
    accept_event(billing_event(3,'InvoiceVoided'))
    with pytest.raises(HTTPException) as error:accept_event(billing_event(3))
    assert error.value.status_code==409
    assert db.get(BillingInvoice,(101,2)).status=='VOIDED'


def test_void_notification_recovers_after_outage_and_lost_ack(db):
    close_seed(db)
    # The old invoices have already reached the external billing system.
    for event in db.scalars(select(Outbox)):
        accept_event(Event(event_id=event.id,kind=event.kind,payload=event.payload))
        event.state='SENT'
    db.commit()
    svc.reopen_registration(db,1,2,window());db.commit()
    def offline(event):raise ConnectionError('test outage')
    assert process_one(offline)
    db.expire_all()
    pending=list(db.scalars(select(Outbox).where(Outbox.kind=='InvoiceVoided')))
    assert any(e.state=='PENDING' and e.attempts==1 for e in pending)
    for event in pending:event.next_attempt=utcnow()-timedelta(seconds=1)
    db.commit()
    def lost_ack(event):
        accept_event(Event(**event))
        raise ConnectionError('ack lost after receipt committed')
    assert process_one(lost_ack)
    db.expire_all()
    for event in db.scalars(select(Outbox).where(Outbox.kind=='InvoiceVoided')):
        event.next_attempt=utcnow()-timedelta(seconds=1)
    db.commit()
    while process_one(lambda event:accept_event(Event(**event))):pass
    db.expire_all()
    assert all(i.status=='VOIDED' and i.revision==3 for i in db.scalars(select(BillingInvoice)))
    assert all(e.state=='SENT' for e in db.scalars(select(Outbox)))
