import json
import asyncio
import threading
import time
import queue
from types import SimpleNamespace
from datetime import timedelta
import httpx
from sqlalchemy import create_engine, text, select
from sqlalchemy.dialects.mysql import insert
from .config import settings
from .db import SessionLocal, utcnow, database_connect_args
from .models import CatalogCache, Course, Semester, Meeting, Offering, Prerequisite
from .errors import DomainError

class HttpCatalogAdapter:
    def read(self):
        async def fetch():
            async with httpx.AsyncClient(timeout=httpx.Timeout(7, connect=2), trust_env=False) as client:
                response = await client.get(settings().catalog_url, headers={'X-Integration-Key':settings().integration_key})
                response.raise_for_status()
                return response.json()
        async def deadline():
            return await asyncio.wait_for(fetch(), timeout=7.5)
        return asyncio.run(deadline())

class SQLCatalogAdapter:
    """The supplied database account must have SELECT permission only.

    The migration/seed tools provision legacy_catalog(id, document). A real legacy
    driver can replace this adapter without changing registration transactions.
    """
    _inflight=threading.Lock()

    def read(self):
        # The DB driver's timeouts apply per packet/operation. Bound the complete
        # read as well, with at most one outstanding read per process on timeout.
        if not self._inflight.acquire(blocking=False):
            raise TimeoutError('Catalog read is still finishing')
        result=queue.Queue(maxsize=1)
        def run():
            try:
                result.put((True,self._read()))
            except Exception as exc:
                result.put((False,exc))
            finally:
                self._inflight.release()
        threading.Thread(target=run,daemon=True,name='catalog-sql-read').start()
        try:
            ok,value=result.get(timeout=7.5)
        except queue.Empty as exc:
            raise TimeoutError('Catalog deadline exceeded') from exc
        if not ok:
            raise value
        return value

    def _read(self):
        s = settings()
        url = s.catalog_database_url
        if not url:
            raise ValueError('缺少只读课程目录数据库配置')
        engine = create_engine(url, connect_args={**database_connect_args(),'connect_timeout':2,'read_timeout':5})
        try:
            with engine.connect() as db:
                result = db.execute(text('SELECT document FROM legacy_catalog WHERE id = :id'), {'id':1}).scalar_one()
                return json.loads(result) if isinstance(result, str) else result
        finally:
            engine.dispose()

_cache_lock=threading.Lock()
_memory_document=None
_memory_deadline=0.0
_failure_deadline=0.0
_structure_lock=threading.Lock()
_structure_document=None
_structures={}

def offering_structure(db,semester_id,document):
    """Only immutable term structure is cached; seats/teacher/state stay live."""
    global _structure_document,_structures
    with _structure_lock:
        if _structure_document is not document:
            _structure_document=document
            _structures={}
        if semester_id not in _structures:
            term=db.get(Semester,semester_id)
            if not term:
                raise DomainError('SEMESTER_NOT_FOUND','学期不存在',404)
            meetings={}
            for m in db.scalars(select(Meeting).join(Offering,Offering.id==Meeting.offering_id).where(Offering.semester_id==semester_id)):
                meetings.setdefault(m.offering_id,[]).append({field:getattr(m,field) for field in ['weekday','start_minute','end_minute','week_start','week_end','room']})
            prereqs={};required={}
            for cid,pid,code,name in db.execute(select(Prerequisite.course_id,Prerequisite.prerequisite_id,Course.code,Course.name).join(Course,Course.id==Prerequisite.prerequisite_id)):
                prereqs.setdefault(cid,[]).append({'code':code,'name':name})
                required.setdefault(cid,set()).add(pid)
            courses={c.id:SimpleNamespace(id=c.id,code=c.code,name=c.name,department=c.department,credits=str(c.credits),description=c.description) for c in db.scalars(select(Course).join(Offering,Offering.course_id==Course.id).where(Offering.semester_id==semester_id).distinct())}
            _structures[semester_id]={'start_date':term.start_date,'meetings':meetings,'prerequisites':prereqs,'required':required,'courses':courses}
        return _structures[semester_id]

def clear_memory_cache():
    global _memory_document,_memory_deadline,_failure_deadline
    _memory_document=None
    _memory_deadline=_failure_deadline=0.0

def ensure_catalog():
    global _memory_document,_memory_deadline,_failure_deadline
    if _memory_document is not None and time.monotonic()<_memory_deadline:
        return _memory_document
    if not _cache_lock.acquire(timeout=7.8):
        raise DomainError('CATALOG_UNAVAILABLE','课程目录系统暂不可用，请稍后重试',503)
    try:
        if _memory_document is not None and time.monotonic()<_memory_deadline:
            return _memory_document
        if time.monotonic()<_failure_deadline:
            raise DomainError('CATALOG_UNAVAILABLE','课程目录系统暂不可用，请稍后重试',503)
        try:
            document,expires=_read_catalog()
        except Exception:
            _failure_deadline=time.monotonic()+1
            raise
        _memory_document=document
        _memory_deadline=time.monotonic()+max(0,(expires-utcnow()).total_seconds())
        return document
    finally:
        _cache_lock.release()

def _read_catalog():
    with SessionLocal() as db:
        cache = db.get(CatalogCache, 1)
        if cache and cache.expires_at > utcnow():
            return cache.document,cache.expires_at
    try:
        document = (SQLCatalogAdapter() if settings().catalog_mode=='sql' else HttpCatalogAdapter()).read()
        if not isinstance(document, dict) or not isinstance(document.get('courses'), list) or not isinstance(document.get('offerings'), list):
            raise ValueError('课程目录响应结构错误')
    except Exception as exc:
        # No credential, URL or upstream response body is sent to the browser.
        raise DomainError('CATALOG_UNAVAILABLE', '课程目录系统暂不可用，请稍后重试', 503) from exc
    with SessionLocal() as db:
        # Local tables retain a term's structural snapshot (IDs, prerequisites,
        # meetings and prices); display metadata comes from the read-only source.
        # Shared semester locks serialize this refresh with final billing.
        list(db.scalars(select(Semester).order_by(Semester.id).with_for_update(read=True,nowait=True)))
        incoming={row['id']:row for row in document['courses'] if isinstance(row,dict) and isinstance(row.get('id'),int)}
        for course in db.scalars(select(Course).where(Course.id.in_(sorted(incoming))).order_by(Course.id).with_for_update(nowait=True)):
            entry=incoming[course.id]
            for field,limit in [('name',100),('department',80),('description',10000)]:
                if field in entry:
                    value=entry[field]
                    if not isinstance(value,str) or not value.strip() or len(value)>limit:
                        raise DomainError('CATALOG_INVALID','课程目录数据格式不正确',503)
                    setattr(course,field,value)
        expires=(utcnow()+timedelta(seconds=settings().catalog_cache_seconds)).replace(microsecond=0)
        stmt = insert(CatalogCache).values(id=1, document=document, expires_at=expires)
        db.execute(stmt.on_duplicate_key_update(document=stmt.inserted.document, expires_at=stmt.inserted.expires_at))
        db.commit()
    return document,expires
