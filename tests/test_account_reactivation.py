"""Reactivation restores access, never already released academic resources."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from backend.app import services
from backend.app.models import Audit, Enrollment, LoginSession, LoginThrottle, Offering, Qualification, User
from backend.app.security import digest
from .conftest import TEST_PASSWORD, submit


def disable(admin, uid):
    r=admin.request('DELETE',f'/api/v1/registrar/people/{uid}',json={'version':1})
    assert r.status_code==200,r.text


def restore(admin, uid, version=2, **kwargs):
    return admin.post(f'/api/v1/registrar/people/{uid}/reactivate',json={'version':version},**kwargs)


def test_student_access_history_and_reselection(client,db):
    admin=client('admin'); student=client('student04')
    previous=student.get('/api/v1/student/report-card?semester_id=1').json()
    u=db.get(User,104)
    retained=(u.password_hash,u.identity_cipher,u.identity_fingerprint,u.status,u.birth_date)
    disable(admin,104)
    assert student.get('/api/v1/auth/me').status_code==401
    r=restore(admin,104)
    assert r.status_code==200,r.text
    assert r.json()['active'] and r.json()['version']==3
    assert not {'password','password_hash','initial_password','identity_cipher','identity_fingerprint'} & r.json().keys()
    assert student.get('/api/v1/auth/me').status_code==401  # old cookie never revives
    fresh=client('student04')
    assert fresh.get('/api/v1/student/report-card?semester_id=1').json()==previous
    assert fresh.get('/api/v1/student/schedules/2').json()['primary']==[]
    db.expire_all();u=db.get(User,104)
    assert (u.password_hash,u.identity_cipher,u.identity_fingerprint,u.status,u.birth_date)==retained
    assert db.get(Offering,11).enrolled_count==9
    assert db.scalar(select(Enrollment).where(Enrollment.student_id==104,Enrollment.offering_id==11)).state=='DROPPED'
    schedule=fresh.get('/api/v1/student/schedules/2').json()
    assert submit(fresh,version=schedule['version']).status_code==200
    assert admin.get('/api/v1/registrar/people/student?q=student04').json()['total']==1
    event=db.scalar(select(Audit).where(Audit.action=='REACTIVATE_PERSON'))
    assert (event.actor_id,event.target)==(1,'user:104')


def test_professor_restores_qualifications_but_not_reassigned_courses(client,db):
    admin=client('admin'); old=client('professor01')
    qualifications=list(db.scalars(select(Qualification.course_id).where(Qualification.professor_id==11).order_by(Qualification.course_id)))
    disable(admin,11)
    other=client('professor04')
    assigned=other.get('/api/v1/professor/teaching/2').json()['offering_ids']
    # Claim the released slot through the normal eligibility/conflict checks.
    assert other.put('/api/v1/professor/teaching/2',json={'offering_ids':[1],'expected_ids':assigned}).status_code==200
    assert restore(admin,11).status_code==200
    assert old.get('/api/v1/auth/me').status_code==401
    fresh=client('professor01')
    assert fresh.get('/api/v1/professor/teaching/2').json()['offering_ids']==[]
    assert fresh.get('/api/v1/professor/offerings/101/roster').status_code==200
    assert fresh.put('/api/v1/professor/teaching/2',json={'offering_ids':[1],'expected_ids':[]}).json()['code']=='PROFESSOR_ASSIGNED'
    assert fresh.put('/api/v1/professor/teaching/2',json={'offering_ids':[2],'expected_ids':[]}).status_code==200
    db.expire_all()
    assert db.get(Offering,1).professor_id==14
    assert list(db.scalars(select(Qualification.course_id).where(Qualification.professor_id==11).order_by(Qualification.course_id)))==qualifications


def test_finalized_invoices_and_enrollments_are_unchanged(client,db):
    admin=client('admin'); student=client('student04')
    services.close_registration(db,1,2,1);db.commit()
    bill=student.get('/api/v1/student/invoices').json()
    schedule=student.get('/api/v1/student/schedules/2').json()
    assert bill
    disable(admin,104)
    assert restore(admin,104).status_code==200
    fresh=client('student04')
    assert fresh.get('/api/v1/student/invoices').json()==bill
    assert fresh.get('/api/v1/student/schedules/2').json()==schedule


def test_disabled_login_throttle_cleared_without_clearing_address_limit(client,db):
    admin=client('admin'); disable(admin,104)
    anon=client(None)
    for _ in range(5):
        assert anon.post('/api/v1/auth/login',json={'username':'student04','password':TEST_PASSWORD}).status_code==401
    assert anon.post('/api/v1/auth/login',json={'username':'student04','password':TEST_PASSWORD}).status_code==429
    address=db.get(LoginThrottle,digest('address:testclient')).attempts
    assert restore(admin,104).status_code==200
    db.expire_all()
    assert db.get(LoginThrottle,digest('account:student04')) is None
    assert db.get(LoginThrottle,digest('address:testclient')).attempts==address
    assert client('student04').get('/api/v1/semesters').status_code==200


def test_initial_password_change_is_still_required(client,db):
    admin=client('admin')
    db.get(User,104).must_change_password=True;db.commit()
    disable(admin,104)
    assert restore(admin,104).status_code==200
    fresh=client('student04')
    assert fresh.get('/api/v1/auth/me').json()['must_change_password'] is True
    assert fresh.get('/api/v1/semesters').json()['code']=='PASSWORD_CHANGE_REQUIRED'


@pytest.mark.parametrize('username,status',[(None,401),('student01',403),('professor01',403)])
def test_only_admin_can_restore(client,db,username,status):
    admin=client('admin');disable(admin,104)
    assert restore(client(username),104).status_code==status
    assert db.get(User,104).active is False


@pytest.mark.parametrize('headers',[{'X-CSRF-Token':''},{'Origin':'https://attacker.invalid'}])
def test_restore_requires_csrf_and_trusted_origin(client,db,headers):
    admin=client('admin');disable(admin,104)
    assert restore(admin,104,headers=headers).status_code==403
    assert db.get(User,104).active is False


@pytest.mark.parametrize('uid',[1,999999])
def test_cannot_restore_registrar_or_missing_record(client,uid):
    assert restore(client('admin'),uid,1).status_code==404


def test_version_conflicts_and_repeat_requests(client,db):
    admin=client('admin')
    assert restore(admin,104,1).json()['code']=='ACCOUNT_ALREADY_ACTIVE'
    disable(admin,104)
    assert restore(admin,104,1).json()['code']=='VERSION_CONFLICT'
    assert restore(admin,104).status_code==200
    assert restore(admin,104).json()['code']=='VERSION_CONFLICT'
    assert restore(admin,104,3).json()['code']=='ACCOUNT_ALREADY_ACTIVE'
    assert db.get(User,104).version==3
    assert db.scalar(select(func.count(Audit.id)).where(Audit.action=='REACTIVATE_PERSON'))==1


@pytest.mark.parametrize('payload',[{}, {'version':-1}, {'version':2,'role':'registrar'}])
def test_malformed_restore_cannot_change_account(client,db,payload):
    admin=client('admin');disable(admin,104)
    assert admin.post('/api/v1/registrar/people/104/reactivate',json=payload).status_code==422
    u=db.get(User,104)
    assert not u.active and u.version==2 and u.role=='student'


def test_concurrent_restore_only_transitions_once(client,db):
    a,b=client('admin'),client('admin');disable(a,104)
    barrier=Barrier(2)
    def request(admin):
        barrier.wait(timeout=10)
        return restore(admin,104)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(request,[a,b]))
    assert sorted(r.status_code for r in results)==[200,409]
    assert db.get(User,104).version==3
    assert db.scalar(select(func.count(Audit.id)).where(Audit.action=='REACTIVATE_PERSON'))==1


def test_failed_restore_rolls_back_account_and_throttle(client,db,monkeypatch):
    admin=client('admin');disable(admin,104)
    anon=client(None)
    assert anon.post('/api/v1/auth/login',json={'username':'student04','password':TEST_PASSWORD}).status_code==401
    original=services.audit
    def fail_audit(db,actor,action,*args,**kwargs):
        if action=='REACTIVATE_PERSON':
            raise RuntimeError('injected reactivation failure')
        return original(db,actor,action,*args,**kwargs)
    monkeypatch.setattr(services,'audit',fail_audit)
    with pytest.raises(RuntimeError,match='injected reactivation failure'):
        restore(admin,104)
    u=db.get(User,104)
    assert not u.active and u.version==2
    assert db.get(LoginThrottle,digest('account:student04')).attempts==1
    assert db.scalar(select(func.count(LoginSession.token_hash)).where(LoginSession.user_id==104))==0
    assert db.scalar(select(func.count(Audit.id)).where(Audit.action=='REACTIVATE_PERSON'))==0
