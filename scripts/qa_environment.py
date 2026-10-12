"""Disposable browser/load environment, separated from the demonstration database."""
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from dotenv import dotenv_values
import psutil
from sqlalchemy import delete
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
RUNTIME=ROOT/'.runtime'
values=dotenv_values(RUNTIME/'test.env')
if not values.get('DATABASE_URL') or not make_url(values['DATABASE_URL']).database.endswith('_test'):
    raise SystemExit('Refusing to use a non-test database')
values.update(CATALOG_MODE='http',CATALOG_URL='http://127.0.0.1:8104/catalog',BILLING_URL='http://127.0.0.1:8105/events',MOCK_STATE_DIR=str(RUNTIME/'qa'),POOL_SIZE='8',POOL_OVERFLOW='0',ALLOWED_ORIGINS=','.join(f'http://127.0.0.1:{port}' for port in range(8100,8104)))
os.environ.update(values)
from backend.app.db import engine,SessionLocal,Base,utcnow
from backend.app.models import User,LoginSession
from backend.app.seed import seed_demo
from backend.app.security import password_hasher,digest
from scripts.bootstrap import protect

def stop():
    path=RUNTIME/'qa-processes.json'
    if path.exists():
        for item in json.loads(path.read_text()):
            try:
                proc=psutil.Process(item['pid'])
                if abs(proc.create_time()-item['created'])<1:
                    children=proc.children(recursive=True)
                    for child in reversed(children):
                        try:child.terminate()
                        except psutil.NoSuchProcess:pass
                    proc.terminate();proc.wait(10)
                    psutil.wait_procs(children,timeout=10)
            except (psutil.NoSuchProcess,psutil.TimeoutExpired):pass
        path.unlink()

def start(users,instances=4,workers=2):
    stop()
    for port in range(8100,8106):
        with socket.socket() as probe:probe.bind(('127.0.0.1',port))
    Base.metadata.create_all(engine)
    with engine.begin() as db:
        for table in reversed(Base.metadata.sorted_tables):db.execute(delete(table))
    password=secrets.token_urlsafe(18)
    sessions=[]
    with SessionLocal() as db:
        seed_demo(db,password)
        hashed=password_hasher.hash(password)
        for i in range(users):
            uid=10000+i
            db.add(User(id=uid,username=f'load{i:04}',name=f'压测用户{i}',role='student',password_hash=hashed,must_change_password=False))
        db.flush()
        for i in range(users):
            token,csrf=secrets.token_urlsafe(32),secrets.token_hex(32)
            db.add(LoginSession(token_hash=digest(token),user_id=10000+i,csrf=csrf,expires_at=utcnow()+timedelta(hours=8),last_seen=utcnow()))
            sessions.append({'token':token,'csrf':csrf})
        db.commit()
    for name,data in [('qa-credentials.json',{'password':password}),('load-sessions.json',sessions)]:
        path=RUNTIME/name;path.write_text(json.dumps(data),encoding='utf-8');protect(path)
    processes=[]
    (RUNTIME/'qa').mkdir(exist_ok=True)
    for service in ['catalog','billing']:
        (RUNTIME/'qa'/f'{service}-mode.json').write_text('{}')
    for port in range(8100,8100+instances):
        log=(RUNTIME/f'qa-{port}.log').open('a',encoding='utf-8')
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(port),'--workers',str(workers),'--timeout-keep-alive','30','--no-access-log'],cwd=ROOT,env={**os.environ,'PYTHONUTF8':'1'},stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        processes.append({'name':f'api-{port}','pid':proc.pid,'created':psutil.Process(proc.pid).create_time()})
    for name,args in [('catalog',['-m','uvicorn','backend.app.mocks:catalog_app','--host','127.0.0.1','--port','8104','--no-access-log']),('billing',['-m','uvicorn','backend.app.mocks:billing_app','--host','127.0.0.1','--port','8105','--no-access-log']),('worker',['-m','backend.app.worker'])]:
        log=(RUNTIME/f'qa-{name}.log').open('a',encoding='utf-8')
        proc=subprocess.Popen([sys.executable,*args],cwd=ROOT,env={**os.environ,'PYTHONUTF8':'1'},stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        processes.append({'name':name,'pid':proc.pid,'created':psutil.Process(proc.pid).create_time()})
    (RUNTIME/'qa-processes.json').write_text(json.dumps(processes))
    (RUNTIME/'qa-profile.json').write_text(json.dumps({'instances':instances,'workers':workers}))
    import httpx
    for port in range(8100,8100+instances):
        for _ in range(40):
            try:
                if httpx.get(f'http://127.0.0.1:{port}/api/v1/health',timeout=2,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.5)
        else:raise SystemExit(f'QA port {port} failed to start')
    print(f'QA ready: http://127.0.0.1:8100, {users} isolated load sessions.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['start','stop']);parser.add_argument('--users',type=int,default=2000);parser.add_argument('--instances',type=int,choices=range(1,5),default=4);parser.add_argument('--workers',type=int,choices=range(1,5),default=2)
    args=parser.parse_args()
    start(args.users,args.instances,args.workers) if args.action=='start' else stop()
