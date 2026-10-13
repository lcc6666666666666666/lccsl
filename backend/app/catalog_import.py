"""Append-only import from a JSON external catalog; every batch is atomic."""
import hashlib
import json
import uuid
from datetime import timezone
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from .db import utcnow
from .errors import DomainError, require
from .models import Course, Offering, Meeting, Prerequisite, Semester, FileCatalog
from .services import audit, lock_user

MAX_FILE_BYTES = 256 * 1024
IMPORT_PATHS = {'/api/v1/registrar/catalog/preview', '/api/v1/registrar/catalog/import'}


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    semester_id: int = Field(gt=0, le=2147483647)
    filename: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=MAX_FILE_BYTES)


class Row(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


Code = Annotated[str, Field(strict=True, min_length=1, max_length=40, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]


class CourseRow(Row):
    code: Code
    name: str = Field(strict=True, min_length=1, max_length=100)
    department: str = Field(strict=True, min_length=1, max_length=80)
    credits: Decimal = Field(gt=0, le=999.9, max_digits=4, decimal_places=1)
    description: str = Field(default='', strict=True, max_length=10000)
    prerequisites: list[Code] = Field(default_factory=list, max_length=32)

    @field_validator('credits', mode='before')
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError('数值必须是数字或数字文本，不能使用布尔值或其他类型')
        return value


class MeetingRow(Row):
    weekday: int = Field(strict=True, ge=1, le=7)
    start: str = Field(strict=True, pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    end: str = Field(strict=True, pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    week_start: int = Field(strict=True, ge=1, le=53)
    week_end: int = Field(strict=True, ge=1, le=53)
    room: str = Field(strict=True, min_length=1, max_length=60)


class OfferingRow(Row):
    code: str = Field(strict=True, min_length=1, max_length=50, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
    course_code: Code
    fee: Decimal = Field(ge=0, le=99999999.99, max_digits=10, decimal_places=2)
    capacity: int = Field(default=10, strict=True, ge=10, le=10)
    meetings: list[MeetingRow] = Field(min_length=1, max_length=16)

    @field_validator('fee', mode='before')
    @classmethod
    def numeric(cls, value):
        return CourseRow.numeric(value)


class CatalogFile(Row):
    format_version: int = Field(strict=True, ge=1, le=1)
    semester_code: str = Field(strict=True, min_length=1, max_length=40)
    courses: list[CourseRow] = Field(max_length=500)
    offerings: list[OfferingRow] = Field(min_length=1, max_length=500)


LABELS = {'courses':'课程', 'offerings':'教学班', 'code':'代码', 'name':'课程名称',
          'department':'院系', 'credits':'学分', 'description':'简介', 'prerequisites':'先修课程代码',
          'course_code':'所属课程代码', 'fee':'费用', 'capacity':'人数上限', 'meetings':'上课时间',
          'weekday':'星期', 'start':'开始时间', 'end':'结束时间', 'week_start':'起始周',
          'week_end':'结束周', 'room':'教室', 'format_version':'文件格式版本', 'semester_code':'学期代码'}


def issue(parts, message):
    parts = tuple(parts)
    location = ' · '.join(f'第{x + 1}条' if isinstance(x, int) else LABELS.get(x, '不支持的字段') for x in parts)
    return {'field': '.'.join(map(str, parts)), 'location': location or '文件', 'message': message}


def fail(issues):
    if issues:
        raise DomainError('CATALOG_IMPORT_INVALID', f'文件校验发现{len(issues)}处问题，本次未导入任何数据；请按下方位置修改文件后重试', 422, issues[:100])


def parse_file(data):
    require(data.filename.lower().endswith('.json') and not any(c in data.filename for c in '/\\')
            and not any(ord(c) < 32 for c in data.filename), 'CATALOG_FILE_TYPE', '请选择UTF-8编码的.json文件，文件名不能包含路径或控制字符', 422)
    try:
        raw = data.content.encode('utf-8')
    except UnicodeError:
        raise DomainError('CATALOG_FILE_ENCODING', '文件包含无效Unicode字符，请另存为UTF-8编码后重试', 422) from None
    require(len(raw) <= MAX_FILE_BYTES, 'CATALOG_FILE_SIZE', '课程目录文件不能超过256KB，请拆分为多个文件导入', 413)
    def pairs(entries):
        result = {}
        for key, value in entries:
            require(key not in result, 'CATALOG_JSON_DUPLICATE', 'JSON对象包含重复字段，不能确定应使用哪个值；请删除重复字段后重试', 422)
            result[key] = value
        return result
    def invalid_constant(_):
        raise DomainError('CATALOG_JSON_NUMBER', 'JSON不支持NaN或Infinity，学分和费用必须填写有限数值', 422)
    try:
        source = json.loads(data.content.lstrip('\ufeff'), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except json.JSONDecodeError as exc:
        raise DomainError('CATALOG_JSON_INVALID', f'JSON格式错误：第{exc.lineno}行、第{exc.colno}列，请检查双引号、逗号和括号；可以先下载示例文件', 422) from None
    except (RecursionError, ValueError):
        raise DomainError('CATALOG_JSON_INVALID', 'JSON嵌套过深或数字长度异常，请按照示例文件的结构填写', 422) from None
    try:
        document = CatalogFile.model_validate(source)
    except ValidationError as exc:
        errors = []
        for e in exc.errors(include_input=False, include_url=False)[:100]:
            kind, ctx = e['type'], e.get('ctx', {})
            message = {'missing':'此字段必填', 'extra_forbidden':'不支持此字段，请删除；编号、教师和已选人数由系统维护',
                       'string_type':'必须填写文本', 'int_type':'必须填写整数，不能填写小数、布尔值或数字文本',
                       'list_type':'必须为列表，请使用方括号', 'model_type':'必须为包含字段的数据对象',
                       'decimal_parsing':'必须填写有效数字', 'finite_number':'必须为有限数值',
                       'decimal_max_digits':'数值位数过多，请参照模板中的取值范围',
                       'decimal_max_places':'小数位数超限：学分最多1位，费用最多2位',
                       'decimal_whole_digits':'整数部分过大，请减小数值'}.get(kind)
            if kind == 'string_pattern_mismatch':
                message = '时间必须使用24小时HH:MM格式，例如08:00' if e['loc'][-1] in {'start','end'} else '代码只能包含英文字母、数字、点、下划线和短横线，且以字母或数字开头'
            for error_type, template, key in [('string_too_short','至少需要{}个字符','min_length'),('string_too_long','最多允许{}个字符','max_length'),
                    ('too_short','至少需要{}条记录','min_length'),('too_long','最多允许{}条记录','max_length'),
                    ('greater_than','必须大于{}','gt'),('greater_than_equal','必须大于或等于{}','ge'),
                    ('less_than','必须小于{}','lt'),('less_than_equal','必须小于或等于{}','le')]:
                if kind == error_type:
                    message = template.format(ctx.get(key, '规定值'))
            errors.append(issue(e['loc'], message or '类型或格式不正确，请按示例填写；数值不能使用布尔值'))
        fail(errors)
    return document, source, hashlib.sha256(raw).hexdigest()


def minutes(value):
    h, m = map(int, value.split(':'))
    return h * 60 + m


def meeting_values(m):
    return (m.weekday, minutes(m.start), minutes(m.end), m.week_start, m.week_end, m.room)


def plan_import(db, term, document):
    errors = []
    if document.semester_code != term.code:
        errors.append(issue(['semester_code'], f'文件学期代码必须是所选学期的“{term.code}”，请下载该学期的示例文件或更换学期'))
    courses = {c.code.casefold(): c for c in db.scalars(select(Course).order_by(Course.id))}
    by_id = {c.id: c for c in courses.values()}
    offerings = {o.code.casefold(): o for o in db.scalars(select(Offering).order_by(Offering.id))}
    graph = {key: set() for key in courses}
    for cid, pid in db.execute(select(Prerequisite.course_id, Prerequisite.prerequisite_id)):
        graph[by_id[cid].code.casefold()].add(by_id[pid].code.casefold())
    incoming, seen = {}, set()
    new_courses, new_offerings, preview = [], [], []
    for i, row in enumerate(document.courses):
        key = row.code.casefold()
        if key in incoming:
            errors.append(issue(['courses', i, 'code'], '文件中课程代码重复（不区分大小写），每门课程只能出现一次'))
        incoming[key] = row
    known = set(courses) | set(incoming)
    for i, row in enumerate(document.courses):
        key, prereqs = row.code.casefold(), {x.casefold() for x in row.prerequisites}
        if len(prereqs) != len(row.prerequisites):
            errors.append(issue(['courses', i, 'prerequisites'], '同一先修课程重复出现，请删除重复项'))
        if key in prereqs:
            errors.append(issue(['courses', i, 'prerequisites'], '课程不能将自身设置为先修课程'))
        for j, code in enumerate(row.prerequisites):
            if code.casefold() not in known:
                errors.append(issue(['courses', i, 'prerequisites', j], f'先修课程“{code}”不存在，请先导入或在本文件courses中补充'))
        existing = courses.get(key)
        if existing:
            for field in ('name','department','credits','description'):
                if getattr(row, field) != getattr(existing, field):
                    errors.append(issue(['courses', i, field], f'课程代码“{row.code}”已存在，但此字段与已保存信息不同；为保留历史数据，本次不覆盖。新增课程请使用新代码，引用现有课程无需在courses中重填'))
            if prereqs != graph[key]:
                errors.append(issue(['courses', i, 'prerequisites'], '已有课程的先修关系与文件不同，不能通过新增导入修改历史规则'))
        else:
            new_courses.append(row)
            graph[key] = prereqs
    # Iterative traversal avoids recursion exhaustion on large valid source graphs.
    indegree = {key: 0 for key in graph}
    dependants = {key: [] for key in graph}
    for key, requirements in graph.items():
        for parent in requirements:
            if parent in graph:
                indegree[key] += 1
                dependants[parent].append(key)
    ready = [key for key, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        key = ready.pop(); visited += 1
        for child in dependants[key]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited < len(graph):
        for i, row in enumerate(document.courses):
            if indegree.get(row.code.casefold(), 0):
                errors.append(issue(['courses', i, 'prerequisites'], '先修关系形成循环或依赖循环课程，请移除循环依赖后重试'))
    weeks = ((term.end_date-term.start_date).days // 7) + 1
    for i, row in enumerate(document.offerings):
        key = row.code.casefold()
        if key in seen:
            errors.append(issue(['offerings', i, 'code'], '文件中教学班代码重复（不区分大小写），每班只能出现一次'))
        seen.add(key)
        if row.course_code.casefold() not in known:
            errors.append(issue(['offerings', i, 'course_code'], f'课程“{row.course_code}”不存在，请先导入或在courses中补充'))
        periods = [meeting_values(m) for m in row.meetings]
        for j, m in enumerate(row.meetings):
            if minutes(m.end) <= minutes(m.start):
                errors.append(issue(['offerings', i, 'meetings', j, 'end'], '结束时间必须晚于开始时间，不支持跨日上课'))
            if m.week_end < m.week_start or m.week_end > weeks:
                errors.append(issue(['offerings', i, 'meetings', j, 'week_end'], f'结束周不能早于起始周，也不能超过本学期的{weeks}周'))
            for previous in row.meetings[:j]:
                if (m.weekday == previous.weekday and max(m.week_start,previous.week_start) <= min(m.week_end,previous.week_end)
                    and max(minutes(m.start),minutes(previous.start)) < min(minutes(m.end),minutes(previous.end))):
                    errors.append(issue(['offerings', i, 'meetings', j], '本教学班的上课时间与前面的记录重叠，请合并或调整'))
                    break
        existing = offerings.get(key)
        if existing:
            if existing.semester_id != term.id or by_id[existing.course_id].code.casefold() != row.course_code.casefold():
                errors.append(issue(['offerings', i, 'code'], '该教学班代码已被其他学期或课程使用，请为新教学班使用唯一代码'))
            if existing.fee != row.fee or existing.capacity != row.capacity:
                errors.append(issue(['offerings', i, 'fee'], '已有教学班的费用或人数上限与文件不一致，不能通过新增导入覆盖'))
            saved = [(m.weekday,m.start_minute,m.end_minute,m.week_start,m.week_end,m.room)
                     for m in db.scalars(select(Meeting).where(Meeting.offering_id==existing.id))]
            if sorted(saved) != sorted(periods):
                errors.append(issue(['offerings', i, 'meetings'], '已有教学班的上课时间或教室与文件不同，不能改写已有课表'))
        else:
            new_offerings.append(row)
        course = incoming.get(row.course_code.casefold()) or courses.get(row.course_code.casefold())
        preview.append({'code':row.code,'course_code':row.course_code,'name':course.name if course else '',
                        'fee':str(row.fee),'action':'跳过已有' if existing else '新增','meetings':[m.model_dump() for m in row.meetings]})
    fail(errors)
    return courses, new_courses, new_offerings, {
        'new_courses':len(new_courses),'existing_courses':len(document.courses)-len(new_courses),
        'new_offerings':len(new_offerings),'existing_offerings':len(document.offerings)-len(new_offerings),
        'new_prerequisites':sum(len(c.prerequisites) for c in new_courses),
        'new_meetings':sum(len(o.meetings) for o in new_offerings),'items':preview,
    }


def snapshot(db):
    return {'courses':[{'id':c.id,'code':c.code,'name':c.name,'department':c.department,'credits':str(c.credits),'description':c.description}
                        for c in db.scalars(select(Course).order_by(Course.id))],
            'offerings':[{'id':o.id,'code':o.code,'semester_id':o.semester_id,'course_id':o.course_id,'fee':str(o.fee)}
                         for o in db.scalars(select(Offering).order_by(Offering.id))]}


def import_file(db, actor, data, *, preview=False):
    document, source, sha256 = parse_file(data)
    # A single fixed order serializes imports, closing and selection transactions.
    # NOWAIT also prevents a long-running upload from hiding a busy semester.
    terms = list(db.scalars(select(Semester).order_by(Semester.id).with_for_update(read=preview, nowait=True)))
    term = next((t for t in terms if t.id == data.semester_id), None)
    require(term, 'SEMESTER_NOT_FOUND', '所选学期不存在，请刷新学期列表重新选择', 404)
    require(term.state=='OPEN' and term.end_date>=utcnow().date(), 'CATALOG_TERM_CLOSED', '只能向尚未结束的开放学期导入，已结束学期的课程、成绩和账单保持冻结', 409)
    if not preview:
        lock_user(db, actor)
    courses, new_courses, new_offerings, result = plan_import(db, term, document)
    result.update(filename=data.filename, semester_id=term.id, semester_name=term.name, sha256=sha256, applied=not preview)
    if preview:
        return result
    for row in new_courses:
        course = Course(**row.model_dump(exclude={'prerequisites'}))
        db.add(course); courses[row.code.casefold()] = course
    db.flush()
    for row in new_courses:
        for code in row.prerequisites:
            db.add(Prerequisite(course_id=courses[row.code.casefold()].id,prerequisite_id=courses[code.casefold()].id))
    for row in new_offerings:
        offering = Offering(code=row.code,course_id=courses[row.course_code.casefold()].id,semester_id=term.id,
                            fee=row.fee,capacity=10,enrolled_count=0,state='OPEN')
        db.add(offering); db.flush()
        for m in row.meetings:
            day, start, end, first, last, room = meeting_values(m)
            db.add(Meeting(offering_id=offering.id,weekday=day,start_minute=start,end_minute=end,week_start=first,week_end=last,room=room))
    db.flush()
    current = db.get(FileCatalog, 1)
    if new_courses or new_offerings or current is None:
        values = dict(revision=uuid.uuid4().hex,filename=data.filename,sha256=sha256,source=source,
                      document=snapshot(db),imported_at=utcnow(),imported_by=actor)
        if current is None:
            db.add(FileCatalog(id=1,**values))
        else:
            for field, value in values.items():
                setattr(current, field, value)
        audit(db,actor,'IMPORT_CATALOG',f'semester:{term.id}',{k:v for k,v in result.items() if k!='items'})
    return result


def source_status(db):
    row = db.get(FileCatalog, 1)
    if not row:
        return {'mode':'external','filename':None,'message':'当前使用外部目录服务；成功导入后切换为持久化文件目录。'}
    return {'mode':'file','filename':row.filename,'revision':row.revision,'sha256':row.sha256,
            'imported_at':row.imported_at.replace(tzinfo=timezone.utc),
            'course_count':len(row.document['courses']),'offering_count':len(row.document['offerings']),
            'message':'当前使用已导入文件的持久化目录，重启后继续有效。'}


def example_document(term):
    weeks = min(16, (term.end_date-term.start_date).days//7+1)
    return {'format_version':1,'semester_code':term.code,
            'courses':[
                {'code':'EXT101','name':'Python应用开发','department':'计算机科学与技术学院','credits':'3.0',
                 'description':'使用Python实现实际应用，练习模块设计与自动化测试。','prerequisites':[]},
                {'code':'EXT102','name':'Web应用开发进阶','department':'计算机科学与技术学院','credits':'3.0',
                 'description':'基于Web接口构建应用，需要通过Python应用开发。','prerequisites':['EXT101']}],
            'offerings':[{'code':f'EXT10{i}-{term.id}-01','course_code':f'EXT10{i}','fee':'600.00','capacity':10,
                         'meetings':[{'weekday':i,'start':'14:00','end':'15:40','week_start':1,'week_end':weeks,'room':f'实验楼B20{i}'}]}
                        for i in (1,2)]}
