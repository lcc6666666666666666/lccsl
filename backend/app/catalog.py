import json
import asyncio
import threading
import time
import queue
from types import SimpleNamespace
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import httpx
from sqlalchemy import create_engine, text, select
from sqlalchemy.dialects.mysql import insert
from .config import settings
from .db import SessionLocal, utcnow, database_connect_args
from .models import CatalogCache, Course, Semester, Meeting, Offering, Prerequisite, FileCatalog
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
_memory_source=None
_memory_deadline=0.0
_failure_deadline=0.0
_structure_lock=threading.Lock()
_structure_document=None
_structures={}

def validate_catalog_document(document):
    """Validate the adapter boundary before mutating display data or the cache.

    The legacy adapter may provide partial display metadata. Structural fields
    remain term snapshots, but supplied identifiers and numeric values must be
    unambiguous; invalid/duplicate entries must not silently overwrite a course.
    """
    def invalid(reason):
        raise DomainError('CATALOG_INVALID', f'外部课程目录数据不符合要求：{reason}。本次目录未更新，请联系目录维护人员修正后刷新页面', 503)

    def positive_id(value, location):
        # bool is an int in Python, but is never a valid course identifier.
        if type(value) is not int or not 0 < value <= 2147483647:
            invalid(f'{location}必须为1～2147483647之间的整数')

    def amount(value, maximum, places, location):
        places_count = max(0, -Decimal(places).as_tuple().exponent)
        rule = f'{location}须为0～{maximum}之间的数值，最多保留{places_count}位小数'
        if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 40:
            invalid(rule)
        try:
            number = Decimal(str(value))
            if not number.is_finite() or number < 0 or number > maximum or number != number.quantize(Decimal(places)):
                invalid(rule)
        except InvalidOperation:
            invalid(rule)

    if not isinstance(document, dict):
        invalid('目录应为包含课程列表和教学班列表的数据对象')
    labels = {'courses':'课程列表', 'offerings':'教学班列表', 'id':'编号', 'code':'代码', 'name':'名称', 'department':'院系', 'description':'简介', 'semester_id':'学期编号', 'course_id':'课程编号'}
    for collection in ('courses', 'offerings'):
        rows = document.get(collection)
        if not isinstance(rows, list):
            invalid(f'缺少{labels[collection]}或该字段不是列表')
        ids, codes = set(), set()
        text_fields = {'code':40, 'name':100, 'department':80, 'description':10000} if collection == 'courses' else {'code':50}
        for index, row in enumerate(rows, start=1):
            location = f'{labels[collection]}第{index}条'
            if not isinstance(row, dict):
                invalid(f'{location}应为数据对象')
            positive_id(row.get('id'), f'{location}的编号')
            if row['id'] in ids:
                invalid(f'{location}的编号与前面的记录重复，每条记录的编号必须唯一')
            ids.add(row['id'])
            for field, limit in text_fields.items():
                if field in row:
                    value = row[field]
                    if not isinstance(value, str):
                        invalid(f'{location}的{labels[field]}必须为文本')
                    if len(value) > limit:
                        invalid(f'{location}的{labels[field]}超过{limit}个字符')
                    if field != 'description' and not value.strip():
                        invalid(f'{location}的{labels[field]}不能为空或全部为空格')
            if 'code' in row:
                if row['code'].casefold() in codes:
                    invalid(f'{location}的代码与前面的记录重复，代码必须唯一（不区分大小写）')
                codes.add(row['code'].casefold())
            if collection == 'courses' and 'credits' in row:
                amount(row['credits'], Decimal('999.9'), '0.1', f'{location}的学分')
            if collection == 'offerings':
                for field in ('semester_id', 'course_id'):
                    if field in row:
                        positive_id(row[field], f'{location}的{labels[field]}')
                if 'fee' in row:
                    amount(row['fee'], Decimal('99999999.99'), '0.01', f'{location}的费用')
    return document

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
                raise DomainError('SEMESTER_NOT_FOUND','未找到所选学期，请刷新学期列表后重新选择',404)
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
    global _memory_document,_memory_deadline,_failure_deadline,_memory_source
    _memory_document=None
    _memory_source=None
    _memory_deadline=_failure_deadline=0.0

def ensure_catalog():
    global _memory_document,_memory_deadline,_failure_deadline,_memory_source
    # Check the durable revision on every call, including across API workers.
    # Imported file snapshots are immutable between revisions and need no network.
    with SessionLocal() as db:
        revision=db.scalar(select(FileCatalog.revision).where(FileCatalog.id==1))
    if revision:
        if _memory_source==revision and _memory_document is not None:
            return _memory_document
        if not _cache_lock.acquire(timeout=7.8):
            raise DomainError('CATALOG_UNAVAILABLE','文件目录正在刷新，请稍后重试',503)
        try:
            with SessionLocal() as db:
                current=db.get(FileCatalog,1)
                if current is not None:
                    _memory_document=validate_catalog_document({**current.document,'file_revision':current.revision})
                    _memory_source=current.revision
                    return _memory_document
        finally:
            _cache_lock.release()
    if _memory_source is None and _memory_document is not None and time.monotonic()<_memory_deadline:
        return _memory_document
    if not _cache_lock.acquire(timeout=7.8):
        raise DomainError('CATALOG_UNAVAILABLE','课程目录刷新等待超时，暂时无法核验最新课程信息；请稍后刷新重试，持续失败时请联系教务管理员检查目录服务',503)
    try:
        if _memory_source is None and _memory_document is not None and time.monotonic()<_memory_deadline:
            return _memory_document
        if time.monotonic()<_failure_deadline:
            raise DomainError('CATALOG_UNAVAILABLE','外部课程目录刚刚访问失败，且当前没有有效缓存；请稍后刷新重试，持续失败时请联系教务管理员检查目录服务',503)
        try:
            document,expires=_read_catalog()
        except Exception:
            _failure_deadline=time.monotonic()+1
            raise
        _memory_document=document
        _memory_source=None
        _memory_deadline=time.monotonic()+max(0,(expires-utcnow()).total_seconds())
        return document
    finally:
        _cache_lock.release()

def _read_catalog():
    with SessionLocal() as db:
        cache = db.get(CatalogCache, 1)
        if cache and cache.expires_at > utcnow():
            return validate_catalog_document(cache.document),cache.expires_at
    try:
        document = (SQLCatalogAdapter() if settings().catalog_mode=='sql' else HttpCatalogAdapter()).read()
        validate_catalog_document(document)
    except DomainError:
        raise
    except Exception as exc:
        # No credential, URL or upstream response body is sent to the browser.
        raise DomainError('CATALOG_UNAVAILABLE', '外部课程目录连接失败、响应超时或返回了无法读取的数据，且当前没有有效缓存；暂时无法查询或提交选课，请稍后刷新重试，持续失败时请联系教务管理员检查目录服务', 503) from exc
    with SessionLocal() as db:
        # Local tables retain a term's structural snapshot (IDs, prerequisites,
        # meetings and prices); display metadata comes from the read-only source.
        # Shared semester locks serialize this refresh with final billing.
        list(db.scalars(select(Semester).order_by(Semester.id).with_for_update(read=True,nowait=True)))
        current=db.get(FileCatalog,1)
        if current is not None:
            # A file import may have committed while the external read was pending.
            # Never apply the older HTTP/SQL display data over that snapshot.
            return validate_catalog_document(current.document),utcnow()+timedelta(seconds=settings().catalog_cache_seconds)
        incoming={row['id']:row for row in document['courses']}
        for course in db.scalars(select(Course).where(Course.id.in_(sorted(incoming))).order_by(Course.id).with_for_update(nowait=True)):
            entry=incoming[course.id]
            for field in ('name','department','description'):
                if field in entry:
                    value=entry[field]
                    setattr(course,field,value)
        expires=(utcnow()+timedelta(seconds=settings().catalog_cache_seconds)).replace(microsecond=0)
        stmt = insert(CatalogCache).values(id=1, document=document, expires_at=expires)
        db.execute(stmt.on_duplicate_key_update(document=stmt.inserted.document, expires_at=stmt.inserted.expires_at))
        db.commit()
    return document,expires
