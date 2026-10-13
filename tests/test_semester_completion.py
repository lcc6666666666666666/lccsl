"""Admin teaching completion is distinct from closing registration."""
from datetime import timedelta
import time

import pytest
from sqlalchemy import select,func

from backend.app import academic,services as svc
from backend.app.db import SessionLocal,utcnow
from backend.app.models import Audit,Enrollment,Invoice,Offering,Outbox,Semester
from .test_registration_reopen import reopen_request,window


def close(db):
    svc.close_registration(db,1,2,1);db.commit()


def complete(c,version=2,key='complete-semester-001',semester=2):
    return c.post(f'/api/v1/registrar/semesters/{semester}/complete',json={'version':version},headers={'Idempotency-Key':key})


def test_manual_completion_unlocks_grades_report_and_preserves_final_records(client,db):
    close(db);teacher=client('professor01');admin=client('admin')
    term=db.get(Semester,2);dates=(term.start_date,term.end_date)
    before=[(i.id,i.total,i.lines,i.status,i.revision) for i in db.scalars(select(Invoice).order_by(Invoice.id))]
    seats=[(o.id,o.enrolled_count) for o in db.scalars(select(Offering).order_by(Offering.id))]
    events=db.scalar(select(func.count(Outbox.id)))
    r=complete(admin)
    assert r.status_code==200,r.text
    assert r.json()['completed'] and r.json()['version']==3
    db.expire_all();term=db.get(Semester,2)
    assert term.completed_at and (term.start_date,term.end_date)==dates and term.state=='CLOSED'
    assert before==[(i.id,i.total,i.lines,i.status,i.revision) for i in db.scalars(select(Invoice).order_by(Invoice.id))]
    assert seats==[(o.id,o.enrolled_count) for o in db.scalars(select(Offering).order_by(Offering.id))]
    assert db.scalar(select(func.count(Outbox.id)))==events
    roster=teacher.get('/api/v1/professor/offerings/1/roster').json()
    assert roster['can_grade'] and roster['semester']['completed']
    rows=roster['items'];assert rows
    rows[0]['grade']='A'
    r=teacher.put('/api/v1/professor/offerings/1/grades',json={'grades':[{k:row[k] for k in ('student_id','grade','version')} for row in rows]})
    assert r.status_code==200,r.text
    report=client('student18').get('/api/v1/student/report-card').json()
    assert report['semester']['id']==2
    assert next(i for i in report['items'] if i['offering_id']==1)['grade']=='A'
    assert not r.json()['semester']['can_reopen'] and not r.json()['semester']['can_complete']
    assert reopen_request(admin,window(3)).json()['code']=='SEMESTER_COMPLETED'
    assert teacher.get('/api/v1/professor/teaching-status/2').json()['items'][0]['status_label']=='已结课，可录入成绩'


def test_detailed_reason_distinguishes_registration_close_and_teaching_completion(client,db):
    teacher=client('professor01')
    assert '选课尚未结束' in teacher.get('/api/v1/professor/offerings/1/roster').json()['grading_message']
    close(db)
    r=teacher.get('/api/v1/professor/offerings/1/roster').json()
    assert not r['can_grade'] and r['semester']['can_complete']
    assert str(db.get(Semester,2).end_date) in r['grading_message']
    assert '结束学期，开放成绩录入' in r['grading_message']
    denied=teacher.put('/api/v1/professor/offerings/1/grades',json={'grades':[{'student_id':118,'grade':'A','version':0}]})
    assert denied.json()['code']=='GRADING_NOT_OPEN'
    assert denied.json()['message']==r['grading_message']


@pytest.mark.parametrize('who,status',[(None,401),('student01',403),('professor01',403)])
def test_only_admin_can_complete(client,db,who,status):
    close(db)
    assert complete(client(who)).status_code==status
    db.expire_all();assert db.get(Semester,2).completed_at is None


def test_completion_requires_csrf_origin_and_idempotency(client,db):
    close(db);admin=client('admin');path='/api/v1/registrar/semesters/2/complete'
    for headers in ({'X-CSRF-Token':''},{'Origin':'https://attacker.invalid'}):
        assert admin.post(path,json={'version':2},headers=headers).status_code==403
    assert admin.post(path,json={'version':2}).json()['code']=='IDEMPOTENCY_REQUIRED'
    assert admin.post(path,json={'version':2,'completed_at':'2000-01-01'},headers={'Idempotency-Key':'invalid-body-test'}).status_code==422
    assert complete(admin,semester=9999).status_code==404


@pytest.mark.parametrize('scenario,code',[('open','REGISTRATION_STILL_OPEN'),('stale','VERSION_CONFLICT'),('past','SEMESTER_COMPLETED')])
def test_completion_preconditions(client,db,scenario,code):
    if scenario!='open':close(db)
    if scenario=='past':
        t=db.get(Semester,2);t.start_date=utcnow().date()-timedelta(days=100);t.end_date=utcnow().date()-timedelta(days=1);db.commit()
    r=complete(client('admin'),version=1 if scenario in {'open','stale'} else 2)
    assert r.status_code==409 and r.json()['code']==code,r.text
    db.expire_all();assert db.get(Semester,2).completed_at is None


def test_completion_is_idempotent_and_audited(client,db):
    close(db);admin=client('admin')
    one=complete(admin);two=complete(admin)
    assert one.status_code==200 and two.json()==one.json()
    assert complete(admin,key='different-completion-key').json()['code']=='VERSION_CONFLICT'
    assert complete(admin,version=3,key='already-completed-key').json()['code']=='SEMESTER_COMPLETED'
    assert db.scalar(select(func.count(Audit.id)).where(Audit.action=='COMPLETE_SEMESTER'))==1


def test_completion_failure_is_atomic(client,db,monkeypatch):
    close(db);original=svc.audit
    def fail(db,actor,action,*args,**kwargs):
        if action=='COMPLETE_SEMESTER':raise RuntimeError('injected completion failure')
        return original(db,actor,action,*args,**kwargs)
    monkeypatch.setattr(svc,'audit',fail)
    with pytest.raises(RuntimeError,match='injected completion failure'):complete(client('admin'))
    db.expire_all();t=db.get(Semester,2)
    assert t.completed_at is None and t.version==2
    assert db.scalar(select(func.count(Audit.id)).where(Audit.action=='COMPLETE_SEMESTER'))==0


def test_completion_busy_fails_without_waiting(client,db):
    close(db);admin=client('admin')
    with SessionLocal() as holding:
        holding.scalar(select(Semester).where(Semester.id==2).with_for_update(read=True))
        started=time.monotonic();r=complete(admin)
        assert time.monotonic()-started<2
        assert r.json()['code']=='RESOURCE_BUSY'
    assert complete(admin).status_code==200


def test_cancelled_courses_and_other_professors_stay_uneditable(client,db):
    close(db);assert complete(client('admin')).status_code==200
    teacher=client('professor03')
    roster=teacher.get('/api/v1/professor/offerings/7/roster').json()
    assert not roster['can_grade'] and '已取消' in roster['grading_message']
    payload={'grades':[{'student_id':118,'grade':'A','version':0}]}
    denied=teacher.put('/api/v1/professor/offerings/7/grades',json=payload)
    assert denied.json()['code']=='GRADING_NOT_OPEN'
    assert teacher.put('/api/v1/professor/offerings/1/grades',json=payload).status_code==404


def test_grade_write_cannot_race_completion_or_reopen(client,db):
    close(db);teacher=client('professor01')
    with SessionLocal() as holding:
        holding.scalar(select(Semester).where(Semester.id==2).with_for_update())
        r=teacher.put('/api/v1/professor/offerings/1/grades',json={'grades':[{'student_id':118,'grade':'A','version':0}]})
        assert r.json()['code']=='RESOURCE_BUSY'
    assert db.scalar(select(Enrollment).where(Enrollment.offering_id==1,Enrollment.student_id==118)).grade is None


def test_manual_completion_date_applies_to_prerequisites_and_catalog(client,db):
    # The planned end date may be later than the next term; actual completion wins.
    previous=db.get(Semester,1)
    previous.end_date=utcnow().date()+timedelta(days=200)
    previous.completed_at=utcnow()-timedelta(days=1)
    db.commit()
    assert academic.is_completed(previous)
    student=client('student01')
    offers=student.get('/api/v1/catalog/offerings?semester_id=2').json()['items']
    assert next(o for o in offers if o['id']==5)['prerequisites_met']
    # The normal submission path must use the same completion rule.
    from .conftest import submit
    assert submit(student,primary=[1,2,3,5],alternates=[4,6]).status_code==200


def test_automatic_past_term_remains_gradable(client):
    result=client('professor01').get('/api/v1/professor/offerings/101/roster').json()
    assert result['can_grade'] and result['semester']['completed']
    assert result['semester']['completed_at'] is None
