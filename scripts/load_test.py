"""500 users/one process or 2000 users/four processes, against one MySQL schema.

Pre-authenticated independent accounts; synchronized bursts with no think time.
Each round: read catalog, read own schedule, save own draft. This is not a soak
test and does not benchmark password hashing or force seats above the ten limit.
"""
import argparse
import asyncio
from collections import Counter
import json
import math
import platform
from pathlib import Path
import time
import aiohttp
import psutil

ROOT=Path(__file__).resolve().parents[1]

async def run(users,rounds):
    sessions=json.loads((ROOT/'.runtime/load-sessions.json').read_text())[:users]
    assert len(sessions)==users
    samples=[]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120,connect=30),connector=aiohttp.TCPConnector(limit=users,keepalive_timeout=30),trust_env=False) as client:
        # Warm the shared catalog before timed bursts; cold/outage latency is a
        # separate integration check. Session identities stay independent.
        for port in range(8100,8101 if users==500 else 8104):
            async with client.get(f'http://127.0.0.1:{port}/api/v1/catalog/offerings?semester_id=2',headers={'Cookie':f'registration_session={sessions[0]["token"]}'}) as response:
                response.raise_for_status()
                await response.read()
        warmup_start=time.perf_counter()
        # Model users who have reached their workspace before acting together.
        # Ramp connection establishment separately from the synchronized workload.
        async def warm(index,auth):
            await asyncio.sleep(index/users*10)
            port=8100+(index//500 if users>500 else 0)
            async with client.get(f'http://127.0.0.1:{port}/api/v1/student/schedules/2',headers={'Cookie':f'registration_session={auth["token"]}'}) as response:
                response.raise_for_status();await response.read()
        await asyncio.gather(*(warm(i,s) for i,s in enumerate(sessions)))
        warmup_seconds=time.perf_counter()-warmup_start
        start=asyncio.Event()
        async def actor(index,auth):
            port=8100+(index//500 if users>500 else 0)
            base=f'http://127.0.0.1:{port}/api/v1'
            headers={'Cookie':f'registration_session={auth["token"]}','X-CSRF-Token':auth['csrf'],'Origin':f'http://127.0.0.1:{port}'}
            await start.wait()
            for _ in range(rounds):
                version=None
                for label,method,path in [('catalog','GET','/catalog/offerings?semester_id=2'),('schedule','GET','/student/schedules/2'),('draft','PUT','/student/schedules/2/draft')]:
                    if label=='draft' and version is None:continue
                    before=time.perf_counter()
                    try:
                        kwargs={'headers':headers}
                        if label=='draft':kwargs['json']={'version':version,'primary':[1,2],'alternates':[]}
                        async with client.request(method,base+path,**kwargs) as response:
                            status=response.status
                            data=await response.json()
                            if label=='schedule' and status==200:version=data['version']
                            code=data.get('code','') if status!=200 else ''
                    except (aiohttp.ClientError,TimeoutError) as exc:status=0;code=type(exc).__name__
                    samples.append({'operation':label,'seconds':time.perf_counter()-before,'status':status,'error_code':code})
        tasks=[asyncio.create_task(actor(i,s)) for i,s in enumerate(sessions)]
        begin=time.perf_counter();start.set();await asyncio.gather(*tasks);duration=time.perf_counter()-begin
    def percentile(values,p):return sorted(values)[max(0,math.ceil(len(values)*p)-1)] if values else None
    profile=json.loads((ROOT/'.runtime/qa-profile.json').read_text())
    result={'users':users,'api_instances':1 if users==500 else 4,'workers_per_instance':profile['workers'],'users_per_instance':500,'rounds':rounds,'duration_seconds':duration,'transactions':len(samples),'status_counts':dict(Counter(s['status'] for s in samples)),'error_codes':dict(Counter(s['error_code'] for s in samples if s['error_code'])),
        'successful_fraction':sum(s['status']==200 for s in samples)/len(samples),'p80_seconds':percentile([s['seconds'] for s in samples],.8),'p95_seconds':percentile([s['seconds'] for s in samples],.95),
        'completed_within_120s_fraction':sum(s['status']==200 and s['seconds']<=120 for s in samples)/len(samples),
        'catalog_max_seconds':max(s['seconds'] for s in samples if s['operation']=='catalog'),
        'hardware':{'os':platform.platform(),'processor':platform.processor(),'logical_cpus':psutil.cpu_count(),'ram_gib':round(psutil.virtual_memory().total/2**30,2),'python':platform.python_version()},
        'client':'aiohttp','connection_ramp_seconds':10,'warmup_seconds':warmup_seconds,'limits':'Users first load their workspace over a 10-second connection ramp, then issue synchronized bursts without think time; warm catalog; no login hashing; clients, API workers and MySQL share one machine; not a cold-connection spike, distributed-host or long-running availability test.'}
    (ROOT/'test-results').mkdir(exist_ok=True)
    (ROOT/f'test-results/load-{users}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--users',type=int,choices=[500,2000],default=500);parser.add_argument('--rounds',type=int,default=2);args=parser.parse_args()
    asyncio.run(run(args.users,args.rounds))
