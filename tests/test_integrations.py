from datetime import timedelta
import time
import uuid
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func,delete
from backend.app.db import SessionLocal,utcnow
from backend.app.models import CatalogCache,Outbox,BillingInvoice,BillingReceipt,Course
from backend.app.catalog import ensure_catalog,HttpCatalogAdapter,SQLCatalogAdapter,clear_memory_cache
from backend.app.errors import DomainError
from backend.app.config import settings
from backend.app.worker import process_one
from backend.app.mocks import Event,accept_event
from backend.app.services import emit

def test_catalog_cache_expiry_outage_and_recovery(db,monkeypatch):
    def unavailable(self):
        raise httpx.ConnectError('private upstream URL must not leak')
    monkeypatch.setattr(SQLCatalogAdapter,'read',unavailable)
    assert len(ensure_catalog()['courses'])==12
    db.get(CatalogCache,1).expires_at=utcnow()-timedelta(seconds=1);db.commit()
    clear_memory_cache()
    with pytest.raises(DomainError) as info:
        ensure_catalog()
    assert info.value.code=='CATALOG_UNAVAILABLE' and 'private' not in info.value.message
    monkeypatch.setattr(SQLCatalogAdapter,'read',lambda self:{'courses':[],'offerings':[]})
    clear_memory_cache()
    assert ensure_catalog()=={'courses':[],'offerings':[]}

def test_catalog_timeout_total_deadline(monkeypatch):
    import asyncio
    class SlowClient:
        def __init__(self,*args,**kwargs): pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*args,**kwargs):
            await asyncio.sleep(30)
    monkeypatch.setattr(httpx,'AsyncClient',SlowClient)
    before=time.monotonic()
    with pytest.raises(TimeoutError):HttpCatalogAdapter().read()
    assert 7<=time.monotonic()-before<8.1

def test_external_display_metadata_refreshes_readonly_snapshot(db,monkeypatch):
    db.execute(delete(CatalogCache));db.commit()
    monkeypatch.setattr(SQLCatalogAdapter,'read',lambda self:{'courses':[{'id':1,'name':'外部更新的课程名称','department':'外部院系'}],'offerings':[]})
    ensure_catalog()
    db.expire_all()
    assert db.get(Course,1).name=='外部更新的课程名称'
    assert db.get(Course,1).department=='外部院系'

def test_sql_catalog_success_and_total_deadline(monkeypatch):
    from threading import Event
    assert len(SQLCatalogAdapter().read()['courses'])==12
    release=Event();finished=Event()
    def delayed(self):
        try:release.wait(20)
        finally:finished.set()
    monkeypatch.setattr(SQLCatalogAdapter,'_read',delayed)
    start=time.monotonic()
    try:
        with pytest.raises(TimeoutError):SQLCatalogAdapter().read()
        assert time.monotonic()-start<8.1
        with pytest.raises(TimeoutError):SQLCatalogAdapter().read()
    finally:
        release.set();assert finished.wait(2)

def test_catalog_unavailable_aborts_student_and_professor(client,db,monkeypatch):
    db.execute(delete(CatalogCache));db.commit()
    monkeypatch.setattr(SQLCatalogAdapter,'read',lambda self:(_ for _ in ()).throw(OSError()))
    for c,path,method,data in [
        (client(),'/student/schedules/2/draft','PUT',{'version':0,'primary':[1]}),
        (client('professor01'),'/professor/teaching/2','PUT',{'expected_ids':[1,2,3],'offering_ids':[]}),
        (client(),'/catalog/offerings?semester_id=2','GET',None),
    ]:
        r=c.request(method,'/api/v1'+path,json=data)
        assert r.status_code==503 and r.json()['code']=='CATALOG_UNAVAILABLE'

def test_outbox_retry_restart_and_duplicate_delivery(db):
    event=emit(db,'InvoiceFinalized',{'student_id':101,'semester_id':2,'total':'600.00','lines':[{'course':'程序设计基础','fee':'600.00'}]})
    db.commit();eid=event.id
    def fail(body):raise httpx.ConnectError('secret-url')
    assert process_one(fail)
    db.expire_all();e=db.get(Outbox,eid)
    assert e.state=='PENDING' and e.attempts==1 and e.next_attempt>utcnow()
    assert 'secret-url' not in e.last_error
    # Simulate process death after receiver commit but before sender acknowledgement.
    body={'event_id':eid,'kind':e.kind,'payload':e.payload}
    accept_event(Event(**body))
    e.state='PROCESSING';e.lease_until=utcnow()-timedelta(seconds=1);e.lease_token=str(uuid.uuid4());db.commit()
    assert process_one(lambda body:accept_event(Event(**body)))
    db.expire_all()
    assert db.get(Outbox,eid).state=='SENT'
    assert db.get(Outbox,eid).attempts==2
    for _ in range(3):accept_event(Event(**body))
    assert db.scalar(select(func.count(BillingInvoice.student_id)))==1
    assert db.scalar(select(func.count(BillingReceipt.event_id)))==1
    assert not process_one()

def test_invoice_unique_across_different_events_and_payload_mismatch(db):
    payload={'student_id':101,'semester_id':2,'total':'600.00','lines':[]}
    event=Event(event_id=str(uuid.uuid4()),kind='InvoiceFinalized',payload=payload)
    accept_event(event)
    accept_event(event.model_copy(update={'event_id':str(uuid.uuid4())}))
    assert db.scalar(select(func.count(BillingInvoice.student_id)))==1
    with pytest.raises(HTTPException) as err:
        accept_event(event.model_copy(update={'payload':{**payload,'total':'900.00'}}))
    assert err.value.status_code==409
    with pytest.raises(HTTPException):
        accept_event(event.model_copy(update={'event_id':str(uuid.uuid4()),'payload':{**payload,'total':'900.00'}}))
    assert db.scalar(select(func.count(BillingReceipt.event_id)))==2

def test_registration_notice_does_not_create_invoice(db):
    accept_event(Event(event_id=str(uuid.uuid4()),kind='RegistrationUpdated',payload={'student_id':101,'semester_id':2,'version':1,'offering_ids':[1]}))
    assert db.scalar(select(func.count(BillingInvoice.student_id)))==0

def test_worker_continues_retrying_with_capped_interval(db):
    e=emit(db,'RegistrationUpdated',{'student_id':101,'semester_id':2});db.commit()
    e.attempts=100;e.next_attempt=utcnow()-timedelta(seconds=1);db.commit()
    process_one(lambda _:(_ for _ in ()).throw(ConnectionError()))
    db.expire_all();e=db.get(Outbox,e.id)
    assert e.state=='PENDING' and e.attempts==101
    assert 298<=(e.next_attempt-utcnow()).total_seconds()<=301

def test_worker_requires_positive_matching_acknowledgement(db,monkeypatch):
    from backend.app import worker
    e=emit(db,'RegistrationUpdated',{'student_id':101,'semester_id':2});db.commit()
    class WrongAcknowledgement:
        def post(self,*args,**kwargs):
            return httpx.Response(200,json={'accepted':False,'event_id':'wrong'},request=httpx.Request('POST','http://example.invalid/events'))
    monkeypatch.setattr(worker,'billing_client',lambda:WrongAcknowledgement())
    assert process_one()
    db.expire_all()
    assert db.get(Outbox,e.id).state=='PENDING'
