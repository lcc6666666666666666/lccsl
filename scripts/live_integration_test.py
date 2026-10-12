"""Real HTTP outage, timeout, billing duplicate and worker restart checks on QA."""
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import httpx
import psutil
from dotenv import dotenv_values
from sqlalchemy import select,func,delete
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)
values=dotenv_values(ROOT/'.runtime/test.env')
assert make_url(values['DATABASE_URL']).database.endswith('_test')
os.environ.update(values)
from backend.app.db import SessionLocal,utcnow
from backend.app.models import CatalogCache,Outbox,BillingInvoice
from backend.app.catalog import HttpCatalogAdapter
from backend.app.config import settings

def until(check,seconds=20):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if check():return
        time.sleep(.25)
    raise AssertionError('Condition did not become true before timeout')

def main():
    directory=ROOT/'.runtime/qa';directory.mkdir(exist_ok=True)
    catalog_mode=directory/'catalog-mode.json';billing_mode=directory/'billing-mode.json'
    results={}
    try:
        # Adapter uses the real independent service; does not monkeypatch httpx.
        settings().catalog_url='http://127.0.0.1:8104/catalog'
        settings().billing_url='http://127.0.0.1:8105/events'
        settings().catalog_mode='http'
        catalog_mode.write_text(json.dumps({'delay':12}))
        start=time.monotonic()
        try:
            HttpCatalogAdapter().read()
            raise AssertionError('Delayed catalog unexpectedly succeeded')
        except (httpx.TimeoutException,TimeoutError):pass
        results['real_catalog_timeout_seconds']=round(time.monotonic()-start,3)
        assert results['real_catalog_timeout_seconds']<8.1
        catalog_mode.write_text(json.dumps({'offline':True}))
        try:
            HttpCatalogAdapter().read()
            raise AssertionError('Offline catalog unexpectedly succeeded')
        except httpx.HTTPStatusError as exc:assert exc.response.status_code==503
        catalog_mode.write_text('{}')
        assert len(HttpCatalogAdapter().read()['courses'])==12
        results['catalog_recovery']=True

        # The browser workflow has already closed the term and delivered invoices.
        with SessionLocal() as db:
            event=db.scalar(select(Outbox).where(Outbox.kind=='InvoiceFinalized',Outbox.state=='SENT').limit(1))
            assert event,'Run the browser workflow first so finalized invoices exist'
            eid=event.id
            before=db.scalar(select(func.count(BillingInvoice.student_id)))
            attempts=event.attempts
            billing_mode.write_text(json.dumps({'offline':True}))
            event.state='PENDING';event.next_attempt=utcnow()-timedelta(seconds=1);db.commit()
        def retried():
            with SessionLocal() as db:
                row=db.get(Outbox,eid)
                return row.state=='PENDING' and row.attempts>attempts and bool(row.last_error)
        until(retried)
        results['billing_outage_persisted_retry']=True

        # Stop only the tracked worker's verified process tree; simulate death
        # after claiming a task, then create a genuinely new worker process.
        file=ROOT/'.runtime/qa-processes.json'
        processes=json.loads(file.read_text())
        item=next(p for p in processes if p.get('name')=='worker')
        proc=psutil.Process(item['pid'])
        assert abs(proc.create_time()-item['created'])<1
        children=proc.children(recursive=True)
        for child in reversed(children):
            try:child.terminate()
            except psutil.NoSuchProcess:pass
        proc.terminate();psutil.wait_procs([proc,*children],timeout=10)
        with SessionLocal() as db:
            row=db.get(Outbox,eid);row.state='PROCESSING';row.lease_until=utcnow()-timedelta(seconds=1);row.lease_token=str(uuid.uuid4());db.commit()
        billing_mode.write_text('{}')
        env={**os.environ,'BILLING_URL':'http://127.0.0.1:8105/events','PYTHONUTF8':'1'}
        with (ROOT/'.runtime/qa-worker.log').open('a',encoding='utf-8') as log:
            worker=subprocess.Popen([sys.executable,'-m','backend.app.worker'],cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        item.update(pid=worker.pid,created=psutil.Process(worker.pid).create_time())
        file.write_text(json.dumps(processes))
        def sent():
            with SessionLocal() as db:return db.get(Outbox,eid).state=='SENT'
        until(sent)
        with SessionLocal() as db:
            after=db.scalar(select(func.count(BillingInvoice.student_id)))
            row=db.get(Outbox,eid)
            body={'event_id':row.id,'kind':row.kind,'payload':row.payload}
        with httpx.Client(trust_env=False) as client:
            for _ in range(3):
                response=client.post(settings().billing_url,json=body,headers={'X-Integration-Key':settings().integration_key})
                response.raise_for_status()
        with SessionLocal() as db:assert db.scalar(select(func.count(BillingInvoice.student_id)))==before==after
        results.update(real_worker_restart_recovered=True,duplicate_http_deliveries=3,invoice_count_unchanged=True)
    finally:
        catalog_mode.write_text('{}');billing_mode.write_text('{}')
    (ROOT/'test-results/live-integration.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
