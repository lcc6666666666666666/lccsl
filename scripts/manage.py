import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime'
PYTHON=ROOT/'.venv'/'Scripts'/'python.exe'

def start(development=False):
    RUNTIME.mkdir(exist_ok=True)
    if not (ROOT/'.env').exists():
        raise SystemExit('请先运行 scripts/init.ps1。')
    if (RUNTIME/'mysql-admin.json').exists():
        subprocess.run([str(PYTHON),str(ROOT/'scripts/bootstrap.py'),'--start-mysql'],check=True,cwd=ROOT)
    if (RUNTIME/'processes.json').exists():
        import psutil
        existing=json.loads((RUNTIME/'processes.json').read_text())
        for item in existing:
            if psutil.pid_exists(item['pid']):
                proc=psutil.Process(item['pid'])
                if abs(proc.create_time()-item['created'])<1:
                    raise SystemExit('项目进程已经运行；请先停止或直接打开 http://127.0.0.1:8000。')
    for port in [8000,8001,8002]+([5173] if development else []):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1',port))
            except OSError:
                raise SystemExit(f'端口 {port} 已占用，请先处理该端口。')
    values=dotenv_values(ROOT/'.env')
    env={**os.environ,**values,'PYTHONUTF8':'1','POOL_SIZE':'8','POOL_OVERFLOW':'0'}
    jobs=[('catalog',[str(PYTHON),'-m','uvicorn','backend.app.mocks:catalog_app','--host','127.0.0.1','--port','8001'],{'DATABASE_URL':values['CATALOG_DATABASE_URL']}),
          ('billing',[str(PYTHON),'-m','uvicorn','backend.app.mocks:billing_app','--host','127.0.0.1','--port','8002'],{'DATABASE_URL':values['MOCK_BILLING_DATABASE_URL']}),
          ('api',[str(PYTHON),'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8000','--workers','2','--timeout-keep-alive','30'],{}),
          ('worker',[str(PYTHON),'-m','backend.app.worker'],{})]
    if development:
        node=next((ROOT/'.tools').glob('node-v22.*-win-x64/node.exe'))
        jobs.append(('frontend',[str(node),str(ROOT/'frontend/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port','5173'],{}))
    import psutil
    processes=[]
    for name,args,override in jobs:
        out=(RUNTIME/f'{name}.log').open('a',encoding='utf-8')
        proc=subprocess.Popen(args,cwd=ROOT/'frontend' if name=='frontend' else ROOT,env={**env,**override},stdout=out,stderr=out,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        processes.append({'name':name,'pid':proc.pid,'created':psutil.Process(proc.pid).create_time()})
    (RUNTIME/'processes.json').write_text(json.dumps(processes),encoding='utf-8')
    import httpx
    for _ in range(40):
        try:
            if httpx.get('http://127.0.0.1:8000/api/v1/health',timeout=2,trust_env=False).status_code==200:
                print('系统已启动：http://127.0.0.1:5173' if development else '系统已启动：http://127.0.0.1:8000')
                return
        except httpx.HTTPError:
            pass
        time.sleep(.5)
    raise SystemExit('启动未就绪，请查看 .runtime 中相应日志，再运行停止脚本。')

def stop():
    import psutil
    path=RUNTIME/'processes.json'
    if path.exists():
        for item in reversed(json.loads(path.read_text())):
            try:
                proc=psutil.Process(item['pid'])
                if abs(proc.create_time()-item['created'])<1:
                    children=proc.children(recursive=True)
                    for child in reversed(children):
                        try:child.terminate()
                        except psutil.NoSuchProcess:pass
                    proc.terminate()
                    proc.wait(timeout=10)
                    psutil.wait_procs(children,timeout=10)
            except (psutil.NoSuchProcess,psutil.TimeoutExpired):
                pass
        path.unlink()
    print('项目应用进程已停止。独立 MySQL 保留运行，以便保存和检查数据。')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','dev','stop','mock-mode'])
    parser.add_argument('--service',choices=['catalog','billing'])
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--delay',type=float,default=0)
    args=parser.parse_args()
    if args.action=='stop':
        stop()
    elif args.action=='mock-mode':
        if not args.service:
            parser.error('--service 必填')
        RUNTIME.mkdir(exist_ok=True)
        (RUNTIME/f'{args.service}-mode.json').write_text(json.dumps({'offline':args.offline,'delay':max(0,min(args.delay,30))}),encoding='utf-8')
        print('模拟故障设置已更新。课程目录已有缓存会在60秒内过期。')
    else:
        start(args.action=='dev')
