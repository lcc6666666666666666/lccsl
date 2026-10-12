from datetime import timedelta
from sqlalchemy import select,delete
from backend.app.models import Qualification,Semester,User
from backend.app.db import utcnow
from .conftest import TEST_PASSWORD

def test_no_schedule_and_empty_report(client):
    c=client('student15')
    assert c.get('/api/v1/student/schedules/2').json()['exists'] is False
    result=c.request('DELETE','/api/v1/student/schedules/2',json={'version':0})
    assert result.status_code==404 and result.json()['code']=='SCHEDULE_NOT_FOUND'
    assert c.get('/api/v1/student/report-card').json()['items']==[]
    assert c.get('/api/v1/student/report-card?semester_id=2').status_code==404

def test_no_qualification_and_closed_teaching(client,db):
    db.execute(delete(Qualification).where(Qualification.professor_id==14));db.commit()
    c=client('professor04')
    items=c.get('/api/v1/catalog/offerings?semester_id=2').json()['items']
    assert not any(x['eligible'] for x in items)
    db.get(Semester,2).state='CLOSED';db.commit()
    r=c.put('/api/v1/professor/teaching/2',json={'expected_ids':[11,12],'offering_ids':[]})
    assert r.json()['code']=='REGISTRATION_CLOSED'

def test_reset_password_revokes_and_forces_change(client):
    old=client();admin=client('admin')
    result=admin.post('/api/v1/registrar/people/101/reset-password',json={})
    assert result.status_code==200
    assert old.get('/api/v1/auth/me').status_code==401
    anon=client(None)
    result=anon.post('/api/v1/auth/login',json={'username':'student01','password':result.json()['initial_password']})
    assert result.status_code==200 and result.json()['must_change_password']
    assert anon.get('/api/v1/student/report-card').json()['code']=='PASSWORD_CHANGE_REQUIRED'

def test_registration_window_and_error_headers(client):
    admin=client('admin')
    data={'version':1,'registration_open':'2020-01-01T00:00:00+08:00','registration_close':(utcnow()+timedelta(days=1)).isoformat()+'Z'}
    result=admin.put('/api/v1/registrar/semesters/2',json=data)
    assert result.status_code==200 and result.json()['version']==2
    assert admin.put('/api/v1/registrar/semesters/2',json=data).json()['code']=='VERSION_CONFLICT'
    data['registration_open']='2020-01-01T00:00:00'
    assert admin.put('/api/v1/registrar/semesters/2',json=data).status_code==422
    r=admin.get('/api/v1/not-an-endpoint')
    assert r.status_code==404 and r.headers['X-Request-ID']==r.json()['request_id']
    assert r.headers['X-Frame-Options']=='DENY' and r.headers['Cache-Control']=='no-store'

def test_request_size_content_type_and_sql_injection_are_rejected(client):
    c=client()
    assert c.post('/api/v1/auth/login',content='x'*65537,headers={'Content-Type':'application/json'}).status_code==413
    assert c.post('/api/v1/auth/login',content='{}',headers={'Content-Type':'text/plain'}).status_code==415
    assert c.get('/api/v1/catalog/offerings',params={'semester_id':2,'q':"' OR 1=1 --"}).json()['total']==0
    assert c.get('/api/v1/catalog/offerings',params={'semester_id':2,'q':'%'}).json()['total']==0
    assert client('admin').get('/api/v1/registrar/people/student',params={'q':"' OR 1=1 --"}).json()['total']==0

def test_qualifications_in_use_and_invalid_person_dates(client):
    c=client('admin')
    body={'name':'教授','birth_date':'1980-01-01','status':'在职','department':'计算机学院','qualification_ids':[],'version':1}
    assert c.put('/api/v1/registrar/people/11',json=body).json()['code']=='QUALIFICATION_IN_USE'
    body['birth_date']='2999-01-01'
    assert c.put('/api/v1/registrar/people/11',json=body).status_code==422

def test_outbox_audit_and_retry_authorization(client,db):
    from backend.app.services import emit
    from backend.app.models import Outbox
    e=emit(db,'RegistrationUpdated',{'student_id':101,'semester_id':2});db.commit()
    admin=client('admin')
    assert admin.get('/api/v1/registrar/outbox').json()['counts']['PENDING']==1
    assert client().post(f'/api/v1/registrar/outbox/{e.id}/retry',json={}).status_code==403
    assert admin.post(f'/api/v1/registrar/outbox/{e.id}/retry',json={}).status_code==200
    assert admin.get('/api/v1/registrar/audit').json()['total']>=1
