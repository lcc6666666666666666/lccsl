import secrets
from datetime import timedelta, date
from decimal import Decimal
from sqlalchemy import select
from .db import utcnow
from .models import User, Semester, Course, Offering, Meeting, Prerequisite, Qualification, Schedule, Enrollment, Alternate, LegacyCatalog
from .security import password_hasher, encrypt_identity, identity_fingerprint

COURSES=[('CS101','程序设计基础'),('MA101','离散数学'),('CS103','计算机导论'),('EN101','大学英语'),
         ('MA102','线性代数'),('SE101','软件工程导论'),('CS201','数据结构'),('CS202','数据库原理'),
         ('CS203','计算机网络'),('SE201','软件测试'),('CS204','操作系统'),('SE202','人机交互')]
TIMES=[(1,480,580),(2,480,580),(3,480,580),(4,480,580),(5,480,580),(1,600,700),
       (2,600,700),(3,600,700),(4,480,580),(5,600,700),(1,840,940),(2,840,940)]

def seed_demo(db, test_password=None):
    if db.scalar(select(User.id).limit(1)):
        return None
    now=utcnow()
    credentials=[]
    test_hash=password_hasher.hash(test_password) if test_password else None
    def user(uid,username,name,role):
        password=test_password or secrets.token_urlsafe(18)
        identity=f'DEMO-{uid:06d}'
        db.add(User(id=uid,username=username,name=name,role=role,password_hash=test_hash or password_hasher.hash(password),
            birth_date=date(1980 if role!='student' else 2004,1,1),identity_cipher=encrypt_identity(identity),
            identity_fingerprint=identity_fingerprint(identity),status='在职' if role!='student' else '在籍',
            department='计算机学院' if role=='professor' else None,graduation_date=date(now.year+2,6,30) if role=='student' else None,
            active=True,must_change_password=not bool(test_password)))
        credentials.append({'username':username,'password':password,'role':role,'name':name})
    user(1,'admin','教务管理员','registrar')
    for i,name in enumerate(['陈明','李华','王宁','周悦'],11):
        user(i,f'professor{i-10:02}',name,'professor')
    for i in range(101,121):
        user(i,f'student{i-100:02}', '林晓' if i==101 else f'学生{i-100:02}', 'student')
    db.flush()
    db.add_all([
        Semester(id=1,code='PREVIOUS',name='上一学期 · 成绩归档',start_date=now.date()-timedelta(days=220),end_date=now.date()-timedelta(days=60),registration_open=now-timedelta(days=250),registration_close=now-timedelta(days=230),state='CLOSED',closed_at=now-timedelta(days=230)),
        Semester(id=2,code='CURRENT',name='本学期 · 课程注册',start_date=now.date()+timedelta(days=7),end_date=now.date()+timedelta(days=160),registration_open=now-timedelta(days=3),registration_close=now+timedelta(days=14),state='OPEN')])
    for cid,(code,name) in enumerate(COURSES,1):
        db.add(Course(id=cid,code=code,name=name,department='通识教育中心' if code.startswith(('MA','EN')) else '计算机学院',credits=Decimal('3.0'),description=f'{name}课程。每班最多10人，至少3人开班。'))
    db.flush()
    db.add_all([Prerequisite(course_id=7,prerequisite_id=1),Prerequisite(course_id=8,prerequisite_id=1)])
    for pid in range(11,15):
        for cid in range(1,13):
            db.add(Qualification(professor_id=pid,course_id=cid))
    for oid in range(1,13):
        db.add(Offering(id=oid,code=f'CUR-{oid:02}',semester_id=2,course_id=oid,professor_id=None if oid==10 else 11+(oid-1)//3,fee=Decimal('600.00'),capacity=10,enrolled_count=0,state='OPEN'))
    for oid in range(101,105):
        db.add(Offering(id=oid,code=f'PRE-{oid-100:02}',semester_id=1,course_id=oid-100,professor_id=11,fee=Decimal('600.00'),capacity=10,enrolled_count=10,state='CLOSED'))
    db.flush()
    for oid,(day,start,end) in enumerate(TIMES,1):
        db.add(Meeting(offering_id=oid,weekday=day,start_minute=start,end_minute=end,week_start=1,week_end=16,room=f'教学楼 A{oid:03}'))
    for oid in range(101,105):
        db.add(Meeting(offering_id=oid,weekday=oid-100,start_minute=480,end_minute=580,room='历史教学班'))
        for uid in range(101,111):
            db.add(Enrollment(student_id=uid,offering_id=oid,state='ENROLLED',position=oid-101,grade='F' if uid==103 else ['A','B','C','D'][oid-101],grade_version=1))
    def enrolled(uid,ids):
        row=Schedule(student_id=uid,semester_id=2,version=1,first_submitted_at=now-timedelta(hours=2,minutes=uid),deleted=False)
        db.add(row)
        db.flush()
        for position,oid in enumerate(ids):
            db.add(Enrollment(student_id=uid,offering_id=oid,state='ENROLLED',position=position))
            db.get(Offering,oid).enrolled_count += 1
        for position,oid in enumerate([5,6]):
            db.add(Alternate(schedule_id=row.id,offering_id=oid,position=position))
    for uid in range(104,114):
        enrolled(uid,[11])
    for uid in [116,117]:
        enrolled(uid,[12])
    for uid in [118,119,120]:
        enrolled(uid,[1,2,3,4])
    db.flush()
    document={'revision':1,'courses':[{'id':c.id,'code':c.code,'name':c.name,'department':c.department,'description':c.description,'credits':str(c.credits)} for c in db.scalars(select(Course).order_by(Course.id))],
              'offerings':[{'id':o.id,'code':o.code,'semester_id':o.semester_id,'course_id':o.course_id,'fee':str(o.fee)} for o in db.scalars(select(Offering).order_by(Offering.id))]}
    db.add(LegacyCatalog(id=1,document=document))
    db.commit()
    return credentials
