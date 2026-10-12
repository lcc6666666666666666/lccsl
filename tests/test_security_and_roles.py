from datetime import timedelta
import pytest
from sqlalchemy import select,func
from backend.app.db import utcnow
from backend.app.models import User,LoginSession,Offering,Enrollment,Audit,Qualification
from .conftest import TEST_PASSWORD,submit

def test_auth_csrf_origin_validation_and_logout(client):
    anon=client(None)
    assert anon.get('/api/v1/semesters').status_code==401
    assert anon.post('/api/v1/auth/login',json={'username':'missing','password':'invalid'}).status_code==401
    c=client()
    assert 'HttpOnly' in c.cookies.__repr__() or c.cookies.get('registration_session')
    assert c.put('/api/v1/student/schedules/2/draft',json={'version':0},headers={'X-CSRF-Token':''}).status_code==403
    assert c.put('/api/v1/student/schedules/2/draft',json={'version':0},headers={'Origin':'https://attacker.invalid'}).status_code==403
    r=c.post('/api/v1/auth/login',json={'username':'abc','password':'secret-value','unknown':True})
    assert r.status_code==422 and 'secret-value' not in r.text and r.json()['request_id']
    assert c.post('/api/v1/auth/logout',json={}).status_code==200
    assert c.get('/api/v1/auth/me').status_code==401

def test_password_rotation_revokes_other_sessions(client,db):
    one,two=client(),client()
    new='Changed-password-for-test-123'
    r=one.post('/api/v1/auth/password',json={'old_password':TEST_PASSWORD,'new_password':new})
    assert r.status_code==200
    assert two.get('/api/v1/auth/me').status_code==401
    anon=client(None)
    assert anon.post('/api/v1/auth/login',json={'username':'student01','password':TEST_PASSWORD}).status_code==401
    assert anon.post('/api/v1/auth/login',json={'username':'student01','password':new}).status_code==200
    assert db.get(User,101).password_hash.startswith('$argon2id$')

def test_login_throttle_and_expiry(client,db):
    c=client(None)
    for _ in range(5):
        assert c.post('/api/v1/auth/login',json={'username':'missing','password':'wrong'}).status_code==401
    assert c.post('/api/v1/auth/login',json={'username':'missing','password':'wrong'}).status_code==429
    c=client()
    session=db.scalar(select(LoginSession).where(LoginSession.user_id==101))
    session.last_seen=utcnow()-timedelta(minutes=31)
    db.commit()
    assert c.get('/api/v1/semesters').status_code==401

@pytest.mark.parametrize('who,path,method,body',[
    ('student01','/registrar/people/student','GET',None),
    ('professor01','/registrar/people/professor','GET',None),
    ('student01','/professor/offerings/101/grades','PUT',{'grades':[{'student_id':101,'grade':'A','version':1}]}),
    ('professor01','/student/report-card','GET',None),
    ('professor02','/professor/offerings/101/grades','PUT',{'grades':[{'student_id':101,'grade':'A','version':1}]}),
    ('professor02','/professor/offerings/101/roster','GET',None),
])
def test_role_and_object_authorization(client,who,path,method,body):
    response=client(who).request(method,'/api/v1'+path,json=body)
    assert response.status_code in {403,404},response.text

def test_cannot_choose_another_student(client):
    c=client()
    r=c.put('/api/v1/student/schedules/2/draft',json={'version':0,'primary':[1],'student_id':102})
    assert r.status_code==422
    assert c.get('/api/v1/student/102/report-card').status_code==404
    assert c.get('/api/v1/student/report-card?student_id=103').status_code==403
    assert c.get('/api/v1/student/report-card').json()['items'][0]['grade']=='A'
    assert client('student03').get('/api/v1/student/report-card').json()['items'][0]['grade']=='F'

@pytest.mark.parametrize('kind',['student','professor'])
def test_registrar_crud_encrypted_identity(client,db,kind):
    c=client('admin')
    person={'username':'new_person','name':'新建人员','birth_date':'2000-01-01','identity_number':'PRIVATE-IDENTITY-123456','status':'正常','department':'计算机学院','graduation_date':'2028-06-01','qualification_ids':[1,2] if kind=='professor' else []}
    r=c.post(f'/api/v1/registrar/people/{kind}',json=person)
    assert r.status_code==200,r.text
    data=r.json(); uid=data['person']['id']
    assert 'initial_password' in data and data['person']['identity_masked'].endswith('3456')
    stored=db.get(User,uid)
    assert stored.identity_cipher!=person['identity_number'] and 'PRIVATE' not in stored.identity_cipher
    result=c.get(f'/api/v1/registrar/people/{kind}?q={uid}')
    assert result.json()['total']==1 and person['identity_number'] not in result.text
    body={k:v for k,v in person.items() if k!='username'}
    body.update(name='修改姓名',version=1,identity_number=None)
    assert c.put(f'/api/v1/registrar/people/{uid}',json=body).status_code==200
    assert c.put(f'/api/v1/registrar/people/{uid}',json=body).json()['code']=='VERSION_CONFLICT'
    new=client(None)
    r=new.post('/api/v1/auth/login',json={'username':'new_person','password':data['initial_password']})
    assert r.status_code==200
    assert new.get('/api/v1/semesters').json()['code']=='PASSWORD_CHANGE_REQUIRED'
    assert c.request('DELETE',f'/api/v1/registrar/people/{uid}',json={'version':2}).status_code==200
    assert new.get('/api/v1/auth/me').status_code==401
    assert c.get(f'/api/v1/registrar/people/{kind}?q={uid}').json()['total']==0
    assert c.get(f'/api/v1/registrar/people/{kind}?q={uid}&include_inactive=true').json()['total']==1

def test_deactivation_releases_open_keeps_history(client,db):
    student=client('student04'); admin=client('admin')
    assert admin.request('DELETE','/api/v1/registrar/people/104',json={'version':1}).status_code==200
    assert student.get('/api/v1/auth/me').status_code==401
    assert db.get(Offering,11).enrolled_count==9
    assert db.scalar(select(func.count(Enrollment.id)).where(Enrollment.student_id==104,Enrollment.offering_id>=101,Enrollment.state=='ENROLLED'))==4
    assert admin.request('DELETE','/api/v1/registrar/people/11',json={'version':1}).status_code==200
    db.expire_all()
    assert db.get(Offering,1).professor_id is None
    assert db.get(Offering,101).professor_id==11
    assert admin.request('DELETE','/api/v1/registrar/people/9999',json={'version':1}).status_code in {403,404}

def test_deactivation_preserves_finalized_invoices(client,db):
    from backend.app.services import close_registration
    from backend.app.models import Invoice
    close_registration(db,1,2,1);db.commit()
    before=db.scalar(select(func.count(Invoice.id)).where(Invoice.student_id==104))
    assert before==1
    assert client('admin').request('DELETE','/api/v1/registrar/people/104',json={'version':1}).status_code==200
    db.expire_all()
    assert db.scalar(select(func.count(Invoice.id)).where(Invoice.student_id==104))==before
    assert db.scalar(select(func.count(Enrollment.id)).where(Enrollment.student_id==104,Enrollment.state=='ENROLLED'))>=4

def test_grades_allowed_null_version_and_atomicity(client,db):
    c=client('professor01')
    assert c.get('/api/v1/professor/offerings/101/roster').json()['can_grade']
    def put(items,oid=101):
        return c.put(f'/api/v1/professor/offerings/{oid}/grades',json={'grades':items})
    row={'student_id':101,'grade':None,'version':1}
    assert put([row]).status_code==200
    assert put([row]).json()['code']=='VERSION_CONFLICT'
    assert put([{**row,'grade':'E','version':2}]).status_code==422
    assert put([{**row,'grade':'B','version':2},{'student_id':120,'grade':'A','version':0}]).json()['code']=='STUDENT_NOT_ENROLLED'
    e=db.scalar(select(Enrollment).where(Enrollment.offering_id==101,Enrollment.student_id==101))
    assert e.grade is None and e.grade_version==2
    for i,grade in enumerate('ABCDFI',2):
        assert put([{**row,'grade':grade,'version':i}]).status_code==200
    assert put([{'student_id':118,'grade':'A','version':0}],oid=1).json()['code']=='GRADING_NOT_OPEN'

def test_teaching_eligibility_conflicts_and_ownership(client,db):
    c=client('professor02')
    path='/api/v1/professor/teaching/2'
    assert c.put(path,json={'offering_ids':[1],'expected_ids':[4,5,6]}).json()['code']=='PROFESSOR_ASSIGNED'
    db.get(Offering,9).professor_id=None
    db.commit()
    r=c.put(path,json={'offering_ids':[4,9],'expected_ids':[4,5,6]})
    assert r.json()['code']=='TEACHING_CONFLICT'
    assert c.get(path).json()['offering_ids']==[4,5,6]
    assert c.put(path,json={'offering_ids':[10],'expected_ids':[4,5,6]}).status_code==200
    assert c.put(path,json={'offering_ids':[],'expected_ids':[10]}).status_code==200
    db.delete(db.get(Qualification,(12,10)));db.commit()
    assert c.put(path,json={'offering_ids':[10],'expected_ids':[]}).json()['code']=='NOT_QUALIFIED'
    assert client('professor02').get('/api/v1/professor/teaching/1').json()['offering_ids']==[]
