import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.mysql import insert
from .config import settings, PROJECT
from .db import SessionLocal
from .models import LegacyCatalog, BillingReceipt, BillingInvoice

catalog_app=FastAPI(title='旧课程目录模拟服务',docs_url=None,redoc_url=None)
billing_app=FastAPI(title='计费模拟服务',docs_url=None,redoc_url=None)

async def guard(service,key):
    if not key or not hmac.compare_digest(key.encode(),settings().integration_key.encode()):
        raise HTTPException(403,'Unauthorized integration')
    directory=Path(settings().mock_state_dir) if settings().mock_state_dir else PROJECT/'.runtime'
    path=directory/f'{service}-mode.json'
    mode=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if mode.get('offline'):
        raise HTTPException(503,'Simulated service outage')
    delay=min(float(mode.get('delay',0)),30)
    if delay>0:
        await asyncio.sleep(delay)

@catalog_app.get('/catalog')
async def catalog(x_integration_key:str|None=Header(default=None)):
    await guard('catalog',x_integration_key)
    with SessionLocal() as db:
        row=db.get(LegacyCatalog,1)
        if not row:
            raise HTTPException(503,'Catalog is not initialized')
        return row.document

class Event(BaseModel):
    model_config=ConfigDict(extra='forbid')
    event_id:str=Field(min_length=36,max_length=36)
    kind:str
    payload:dict

def accept_event(event):
    if event.kind not in {'RegistrationUpdated','InvoiceFinalized'}:
        raise HTTPException(422,'Unknown event kind')
    payload=event.payload
    if any(type(payload.get(field)) is not int or not 0 < payload[field] <= 2147483647
           for field in ('student_id','semester_id')):
        raise HTTPException(422,'Invalid identity')
    fingerprint=hashlib.sha256(json.dumps(event.model_dump(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with SessionLocal() as db:
        stmt=insert(BillingReceipt).values(event_id=event.event_id,digest=fingerprint,kind=event.kind)
        db.execute(stmt.on_duplicate_key_update(event_id=stmt.inserted.event_id))
        receipt=db.get(BillingReceipt,event.event_id,with_for_update=True)
        if receipt.digest!=fingerprint:
            raise HTTPException(409,'Event content mismatch')
        if event.kind=='InvoiceFinalized':
            stmt=insert(BillingInvoice).values(student_id=payload['student_id'],semester_id=payload['semester_id'],payload=payload)
            db.execute(stmt.on_duplicate_key_update(student_id=stmt.inserted.student_id))
            invoice=db.get(BillingInvoice,(payload['student_id'],payload['semester_id']),with_for_update=True)
            if invoice.payload!=payload:
                raise HTTPException(409,'Final invoice mismatch')
        db.commit()
    return {'accepted':True,'event_id':event.event_id}

@billing_app.post('/events')
async def events(event:Event,x_integration_key:str|None=Header(default=None)):
    await guard('billing',x_integration_key)
    return accept_event(event)
