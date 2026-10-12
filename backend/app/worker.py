import logging
import time
import uuid
from functools import lru_cache
from datetime import timedelta
import httpx
from sqlalchemy import select, or_, and_
from .db import SessionLocal, utcnow
from .models import Outbox
from .config import settings

@lru_cache
def billing_client():
    return httpx.Client(timeout=8,trust_env=False)

def process_one(send=None):
    now=utcnow()
    with SessionLocal() as db:
        event=db.scalar(select(Outbox).where(or_(and_(Outbox.state=='PENDING',Outbox.next_attempt<=now),and_(Outbox.state=='PROCESSING',Outbox.lease_until<now))).order_by(Outbox.created_at,Outbox.id).limit(1).with_for_update(skip_locked=True))
        if not event:
            return False
        token=str(uuid.uuid4())
        event.state,event.lease_token,event.lease_until='PROCESSING',token,now+timedelta(seconds=30)
        event.attempts+=1
        body={'event_id':event.id,'kind':event.kind,'payload':event.payload}
        eid,attempt=event.id,event.attempts
        db.commit()
    failure=None
    try:
        if send:
            send(body)
        else:
            response=billing_client().post(settings().billing_url,json=body,headers={'X-Integration-Key':settings().integration_key})
            response.raise_for_status()
            acknowledgement=response.json()
            if acknowledgement.get('accepted') is not True or acknowledgement.get('event_id')!=eid:
                raise ValueError('Invalid billing acknowledgement')
    except Exception as exc:
        failure=f'发送失败（{type(exc).__name__}），将自动重试'
    with SessionLocal() as db:
        event=db.get(Outbox,eid,with_for_update=True)
        if event.lease_token!=token:
            return True
        event.lease_until,event.lease_token=None,None
        event.last_error=failure
        if failure:
            event.state='PENDING'
            event.next_attempt=utcnow()+timedelta(seconds=min(5*(2**min(attempt-1,6)),300))
        else:
            event.state='SENT'
        db.commit()
    return True

def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    while True:
        try:
            if not process_one():
                time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            logging.error('worker error_type=%s; retrying',type(exc).__name__)
            time.sleep(5)

if __name__=='__main__':
    main()
