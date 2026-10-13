import copy
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, func

from backend.app import catalog_import as importing
from backend.app.catalog import ensure_catalog, HttpCatalogAdapter, SQLCatalogAdapter
from backend.app.db import SessionLocal
from backend.app.models import Course, Offering, Meeting, Prerequisite, FileCatalog, LegacyCatalog, Semester, Audit


def payload(db):
    return importing.example_document(db.get(Semester,2))


def upload(client, document, preview=False, semester=2, filename='课程目录.json'):
    return client.post('/api/v1/registrar/catalog/'+('preview' if preview else 'import'), json={
        'semester_id':semester,'filename':filename,'content':json.dumps(document,ensure_ascii=False)})


def test_import_persists_all_four_categories_and_repeat_is_noop(client,db):
    admin=client('admin');document=payload(db)
    legacy=copy.deepcopy(db.get(LegacyCatalog,1).document)
    response=upload(admin,document)
    assert response.status_code==200,response.text
    assert response.json()['new_courses']==response.json()['new_offerings']==2
    assert response.json()['new_prerequisites']==1 and response.json()['new_meetings']==2
    db.expire_all()
    source=db.get(FileCatalog,1);revision=source.revision
    assert source.source==document and source.filename=='课程目录.json'
    first=db.scalar(select(Course).where(Course.code=='EXT101'))
    second=db.scalar(select(Course).where(Course.code=='EXT102'))
    assert db.get(Prerequisite,(second.id,first.id))
    offering=db.scalar(select(Offering).where(Offering.course_id==first.id))
    assert offering.enrolled_count==0 and offering.professor_id is None and offering.capacity==10
    assert db.scalar(select(Meeting).where(Meeting.offering_id==offering.id)).start_minute==840
    assert db.get(LegacyCatalog,1).document==legacy
    repeated=upload(admin,document)
    assert repeated.status_code==200 and repeated.json()['new_courses']==repeated.json()['new_offerings']==0
    assert repeated.json()['existing_offerings']==2
    db.expire_all()
    assert db.get(FileCatalog,1).revision==revision
    assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action=='IMPORT_CATALOG'))==1


def test_preview_does_not_persist_and_student_sees_imported_schedule(client,db):
    admin=client('admin');document=payload(db)
    response=upload(admin,document,preview=True)
    assert response.status_code==200 and not response.json()['applied']
    assert db.get(FileCatalog,1) is None
    assert db.scalar(select(func.count()).select_from(Course))==12
    student=client()
    # Populate structures before import, then require an immediately fresh view.
    assert student.get('/api/v1/catalog/offerings?semester_id=2').status_code==200
    assert upload(admin,document).status_code==200
    response=student.get('/api/v1/catalog/offerings?semester_id=2&q=Web')
    assert response.status_code==200,response.text
    row=response.json()['items'][0]
    assert row['name']=='Web应用开发进阶' and row['remaining']==10
    assert row['prerequisites'][0]['code']=='EXT101' and not row['prerequisites_met']
    assert row['meetings'][0]['room']=='实验楼B202'


@pytest.mark.parametrize('username',['student01','professor01'])
def test_only_administrator_can_import_or_read_source(username,client,db):
    actor=client(username)
    assert upload(actor,payload(db)).status_code==403
    assert upload(actor,payload(db),preview=True).status_code==403
    assert actor.get('/api/v1/registrar/catalog/source').status_code==403
    assert actor.get('/api/v1/registrar/catalog/template?semester_id=2').status_code==403
    assert db.get(FileCatalog,1) is None


def test_import_requires_csrf_and_trusted_origin(client,db):
    admin=client('admin');document=payload(db)
    token=admin.headers.pop('X-CSRF-Token')
    assert upload(admin,document).json()['code']=='CSRF_INVALID'
    admin.headers['X-CSRF-Token']=token
    admin.headers['Origin']='https://evil.invalid'
    assert upload(admin,document).json()['code']=='ORIGIN_INVALID'
    assert db.get(FileCatalog,1) is None


@pytest.mark.parametrize('mutation,field',[
    (lambda d:d['courses'][0].update(credits=True),'credits'),
    (lambda d:d['courses'][0].update(credits='3.25'),'credits'),
    (lambda d:d['courses'][0].update(credits='NaN'),'credits'),
    (lambda d:d['courses'][0].update(name='   '),'name'),
    (lambda d:d['courses'].append(copy.deepcopy(d['courses'][0])),'code'),
    (lambda d:d['offerings'].append(copy.deepcopy(d['offerings'][0])),'code'),
    (lambda d:d['courses'][1].update(prerequisites=['MISSING']),'prerequisites'),
    (lambda d:d['courses'][0].update(prerequisites=['EXT102']),'prerequisites'),
    (lambda d:d['offerings'][0].update(course_code='MISSING'),'course_code'),
    (lambda d:d['offerings'][0].update(capacity=11),'capacity'),
    (lambda d:d['offerings'][0].update(professor_id=11),'professor_id'),
    (lambda d:d['offerings'][0].update(fee='-1'),'fee'),
    (lambda d:d['offerings'][0]['meetings'][0].update(weekday=True),'weekday'),
    (lambda d:d['offerings'][0]['meetings'][0].update(end='25:00'),'end'),
    (lambda d:d['offerings'][0]['meetings'][0].update(end='13:00'),'end'),
    (lambda d:d['offerings'][0]['meetings'][0].update(week_end=53),'week_end'),
    (lambda d:d['offerings'][0]['meetings'].append(copy.deepcopy(d['offerings'][0]['meetings'][0])),'meetings'),
    (lambda d:d.update(semester_code='WRONG'),'semester_code'),
])
def test_invalid_batch_reports_position_and_writes_nothing(mutation,field,client,db):
    document=payload(db);mutation(document)
    response=upload(client('admin'),document)
    assert response.status_code==422,response.text
    error=response.json()
    assert error['code']=='CATALOG_IMPORT_INVALID'
    assert any(field in item['field'] and item['location'] for item in error['details'])
    assert db.scalar(select(func.count()).select_from(Course))==12
    assert db.get(FileCatalog,1) is None


def test_conflicting_existing_record_rejects_whole_batch_without_changing_seats(client,db):
    admin=client('admin');document=payload(db)
    assert upload(admin,document).status_code==200
    document['courses'][0]['name']='不应覆盖的改名'
    document['courses'].append({**document['courses'][0],'code':'EXTRA'})
    before=db.scalar(select(func.sum(Offering.enrolled_count)))
    response=upload(admin,document)
    assert response.status_code==422 and 'name' in str(response.json()['details'])
    db.expire_all()
    assert db.scalar(select(Course).where(Course.code=='EXT101')).name=='Python应用开发'
    assert db.scalar(select(Course).where(Course.code=='EXTRA')) is None
    assert db.scalar(select(func.sum(Offering.enrolled_count)))==before


def test_closed_semester_and_wrong_term_rejected(client,db):
    assert upload(client('admin'),payload(db),semester=1).json()['code']=='CATALOG_TERM_CLOSED'
    assert db.get(FileCatalog,1) is None


def test_parse_errors_duplicate_json_fields_and_size_limits(client,db):
    admin=client('admin')
    for content,code in [('{\n"broken": }','CATALOG_JSON_INVALID'),('{"courses":[],"courses":[]}','CATALOG_JSON_DUPLICATE'),
                         ('{"fee":NaN}','CATALOG_JSON_NUMBER'),('['*2000,'CATALOG_JSON_INVALID'),
                         ('汉'*100000,'CATALOG_FILE_SIZE')]:
        response=admin.post('/api/v1/registrar/catalog/import',json={'semester_id':2,'filename':'目录.json','content':content})
        assert response.json()['code']==code,response.text
        if code=='CATALOG_JSON_INVALID' and content.startswith('{'):
            assert '第2行' in response.json()['message']
    assert upload(admin,payload(db),filename='../目录.json').json()['code']=='CATALOG_FILE_TYPE'
    assert db.get(FileCatalog,1) is None


def test_db_error_after_insert_rolls_back_everything(client,db,monkeypatch):
    admin=client('admin')
    def fail(*args):raise importing.DomainError('TEST_ABORT','模拟写入失败',503)
    monkeypatch.setattr(importing,'snapshot',fail)
    response=upload(admin,payload(db))
    assert response.status_code==503
    assert db.scalar(select(func.count()).select_from(Course))==12
    assert db.scalar(select(func.count()).select_from(Offering))==16
    assert db.get(FileCatalog,1) is None


def test_import_nowait_when_registration_or_closing_holds_lock(client,db):
    admin=client('admin');document=payload(db)
    with SessionLocal() as other:
        other.scalar(select(Semester).where(Semester.id==2).with_for_update(read=True))
        before=time.monotonic();response=upload(admin,document)
        assert response.status_code==409 and response.json()['code']=='RESOURCE_BUSY'
        assert time.monotonic()-before<2
    assert db.get(FileCatalog,1) is None


def test_file_source_survives_upstream_outage_and_shared_revision_change(client,db,monkeypatch):
    admin=client('admin');assert upload(admin,payload(db)).status_code==200
    first=ensure_catalog()
    def fail(_):raise AssertionError('Imported file should not access upstream')
    monkeypatch.setattr(SQLCatalogAdapter,'read',fail);monkeypatch.setattr(HttpCatalogAdapter,'read',fail)
    with SessionLocal() as other:
        source=other.get(FileCatalog,1)
        source.document={**source.document,'external_marker':'revision2'}
        source.revision=uuid.uuid4().hex;other.commit()
    refreshed=ensure_catalog()
    assert refreshed is not first and refreshed['external_marker']=='revision2'
    status=admin.get('/api/v1/registrar/catalog/source').json()
    assert status['mode']=='file' and status['course_count']==14


def test_concurrent_same_file_import_never_duplicates(client,db):
    document=payload(db);admins=[client('admin'),client('admin')]
    with ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda c:upload(c,document),admins))
    assert any(r.status_code==200 for r in responses)
    assert all(r.status_code in (200,409) for r in responses)
    assert upload(admins[0],document).status_code==200
    assert db.scalar(select(func.count()).select_from(Course))==14
    assert db.scalar(select(func.count()).select_from(Offering))==18


def test_add_offering_for_existing_course_without_resubmitting_its_metadata(client,db):
    document=payload(db)
    document['courses']=[]
    document['offerings']=document['offerings'][:1]
    document['offerings'][0]['course_code']=db.get(Course,1).code
    response=upload(client('admin'),document)
    assert response.status_code==200,response.text
    assert response.json()['new_courses']==0 and response.json()['new_offerings']==1


def test_import_endpoint_accepts_large_valid_file_without_relaxing_other_limits(client,db):
    admin=client('admin');document=payload(db)
    for i in range(10):
        document['courses'].append({**document['courses'][0],'code':f'LARGE{i}','description':'课程简介'*1500})
    content=json.dumps(document,ensure_ascii=False)
    assert 65536<len(content.encode())<importing.MAX_FILE_BYTES
    assert upload(admin,document,preview=True).status_code==200
    response=admin.post('/api/v1/auth/logout',json={'content':content})
    assert response.status_code==413
