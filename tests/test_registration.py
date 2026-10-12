from datetime import timedelta
import pytest
from sqlalchemy import select,func
from backend.app.models import Offering,Schedule,Enrollment,Semester,Outbox,Meeting
from backend.app.db import utcnow
from .conftest import submit

def test_catalog_pagination_search_and_report(client):
    c=client()
    result=c.get('/api/v1/catalog/offerings',params={'semester_id':2,'page_size':5})
    assert result.status_code==200,result.text
    assert result.json()['total']==12 and len(result.json()['items'])==5
    result=c.get('/api/v1/catalog/offerings',params={'semester_id':2,'q':'程序'})
    assert [o['id'] for o in result.json()['items']]==[1]
    report=c.get('/api/v1/student/report-card').json()
    assert report['semester']['id']==1
    assert [e['grade'] for e in report['items']]==['A','B','C','D']

def test_draft_submit_update_delete_atomic(client,db):
    c=client()
    r=c.put('/api/v1/student/schedules/2/draft',json={'version':0,'primary':[1],'alternates':[]})
    assert r.status_code==200,r.text
    assert r.json()['primary']==[] and r.json()['draft']['primary']==[1]
    assert db.get(Offering,1).enrolled_count==3
    r=submit(c,version=1,key='stable-request-001')
    assert r.status_code==200,r.text
    assert r.json()['primary']==[1,2,3,4]
    assert submit(c,version=1,key='stable-request-001').json()==r.json()
    assert submit(c,primary=[2,3,4,5],version=1,key='stable-request-001').json()['code']=='IDEMPOTENCY_CONFLICT'
    r=c.put('/api/v1/student/schedules/2/draft',json={'version':2,'primary':[6],'alternates':[5]})
    assert r.json()['primary']==[1,2,3,4]
    assert db.get(Offering,6).enrolled_count==0
    # Failed replacement retains all official enrollments and the draft.
    failed=submit(c,[11],[5],version=3)
    assert failed.json()['code']=='COURSE_FULL'
    state=c.get('/api/v1/student/schedules/2').json()
    assert state['primary']==[1,2,3,4] and state['version']==3
    r=submit(c,[5],[],version=3)
    assert r.status_code==200,r.text
    assert r.json()['primary']==[5]
    assert submit(c,[6],[],version=3).json()['code']=='VERSION_CONFLICT'
    r=c.request('DELETE','/api/v1/student/schedules/2',json={'version':4})
    assert r.status_code==200 and r.json()['primary']==[] and not r.json()['exists']
    assert submit(c,[1],[],version=5).status_code==200
    db.expire_all()
    assert db.get(Offering,5).enrolled_count==0
    assert db.scalar(select(func.count(Outbox.id)))==4

@pytest.mark.parametrize('primary,alternates,code',[
    ([1,2,3],[5,6],'INITIAL_SELECTION_COUNT'),
    ([1,2,3,4],[5],'INITIAL_SELECTION_COUNT'),
    ([1,2,3,4],[4,6],'DUPLICATE_OFFERING'),
    ([1,2,4,9],[5,6],'SCHEDULE_CONFLICT'),
    ([1,2,3,11],[5,6],'COURSE_FULL'),
    ([1,2,3,999],[5,6],'INVALID_OFFERING'),
    ([1,2,3,101],[5,6],'INVALID_OFFERING'),
    ([],[],'PRIMARY_REQUIRED'),
])
def test_invalid_submissions(client,primary,alternates,code):
    response=submit(client(),primary,alternates)
    assert response.json()['code']==code,response.text

@pytest.mark.parametrize('grade,allowed',[('A',True),('B',True),('C',True),('D',True),('F',False),('I',False),(None,False)])
def test_prerequisite_grades(client,db,grade,allowed):
    e=db.scalar(select(Enrollment).where(Enrollment.student_id==101,Enrollment.offering_id==101))
    e.grade=grade
    db.commit()
    r=submit(client(),[2,3,4,7],[5,6])
    assert (r.status_code==200)==allowed,r.text
    if not allowed:
        assert r.json()['code']=='PREREQUISITE_NOT_MET'

def test_duplicate_course_different_offerings(client,db):
    db.add(Offering(id=50,code='CUR-01-B',semester_id=2,course_id=1,professor_id=11,fee=600))
    db.commit()
    assert submit(client(),[1,2,3,4],[50,6]).json()['code']=='DUPLICATE_COURSE'

def test_registration_dates_and_readonly_after_close(client,db):
    db.get(Semester,2).registration_close=utcnow()-timedelta(seconds=1)
    db.commit()
    assert submit(client()).json()['code']=='REGISTRATION_NOT_OPEN'
    db.get(Semester,2).state='CLOSED'
    db.commit()
    assert submit(client('student02')).json()['code']=='REGISTRATION_CLOSED'

def test_adjacent_times_and_nonoverlapping_weeks_are_valid(client,db):
    m=db.scalar(select(Meeting).where(Meeting.offering_id==9))
    m.start_minute=580
    m.end_minute=680
    db.commit()
    assert submit(client(),[1,2,4,9],[5,6]).status_code==200
    m.start_minute=480
    m.end_minute=580
    m.week_start=17
    m.week_end=20
    db.commit()
    assert submit(client('student02'),[1,2,4,9],[5,6]).status_code==200
