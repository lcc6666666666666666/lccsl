"""Failures identify the affected selection and a safe next action."""
import pytest
from sqlalchemy import delete

from backend.app.catalog import validate_catalog_document
from backend.app.errors import DomainError
from backend.app.models import Qualification
from .conftest import submit


def test_first_submission_error_includes_current_counts_and_draft_option(client):
    response = submit(client(), [1, 2, 3], [5])
    assert response.json()['code'] == 'INITIAL_SELECTION_COUNT'
    message = response.json()['message']
    assert '当前为3个首选、1个备选' in message
    assert '4个首选和2个备选' in message and '保存草稿' in message


def test_full_offering_error_includes_course_section_and_seats(client):
    result = submit(client(), [1, 2, 3, 11], [5, 6]).json()
    assert result['code'] == 'COURSE_FULL'
    for text in ['操作系统', 'CUR-11', '已选10人', '容量10人', '剩余0个名额', '更换首选']:
        assert text in result['message']
    assert result['details']['available'] == 0


def test_prerequisite_error_identifies_missing_course_without_other_students(client):
    result = submit(client('student03'), [2, 3, 4, 7], [5, 6]).json()
    assert result['code'] == 'PREREQUISITE_NOT_MET'
    for text in ['数据结构', 'CUR-07', '程序设计基础', 'CS101', 'A、B、C 或 D', '保存草稿']:
        assert text in result['message']
    assert result['details']['missing_course_ids'] == [1]
    assert 'student01' not in result['message'] and '林晓' not in result['message']


def test_schedule_conflict_identifies_both_courses_and_retains_schedule(client):
    student = client()
    original = submit(student).json()
    result = submit(student, [1, 2, 4, 9], [5, 6], version=original['version']).json()
    assert result['code'] == 'SCHEDULE_CONFLICT'
    for text in ['大学英语', 'CUR-04', '计算机网络', 'CUR-09', '更换', '正式课表未改变']:
        assert text in result['message']
    assert student.get('/api/v1/student/schedules/2').json()['primary'] == original['primary']


def test_version_conflict_explains_unsaved_change_and_recovery(client):
    student = client()
    assert submit(student).status_code == 200
    result = submit(student, [1], [], version=0).json()
    assert result['code'] == 'VERSION_CONFLICT'
    assert '本次修改未保存' in result['message']
    assert '最新数据' in result['message'] and '刷新' in result['message']


def test_teaching_qualification_error_identifies_course_and_administrator(client, db):
    db.execute(delete(Qualification).where(Qualification.professor_id == 11, Qualification.course_id == 10))
    db.commit()
    result = client('professor01').put('/api/v1/professor/teaching/2', json={
        'offering_ids':[1, 2, 3, 10], 'expected_ids':[1, 2, 3],
    }).json()
    assert result['code'] == 'NOT_QUALIFIED'
    assert '软件测试' in result['message'] and 'CUR-10' in result['message']
    assert '教务管理员' in result['message']


@pytest.mark.parametrize('document, fragments', [
    ({'courses':[{'id':1, 'name':' '}], 'offerings':[]}, ['课程列表第1条', '名称', '不能为空']),
    ({'courses':[{'id':1}, {'id':1}], 'offerings':[]}, ['课程列表第2条', '编号', '重复']),
    ({'courses':[], 'offerings':[{'id':1, 'fee':'6.123'}]}, ['教学班列表第1条', '费用', '2位小数']),
    ({'courses':[], 'offerings':[{'id':True}]}, ['教学班列表第1条', '编号', '整数']),
])
def test_bad_catalog_errors_identify_location_field_and_rule(document, fragments):
    with pytest.raises(DomainError) as error:
        validate_catalog_document(document)
    assert error.value.code == 'CATALOG_INVALID'
    for fragment in fragments:
        assert fragment in error.value.message
    assert '目录维护人员' in error.value.message


def test_login_failures_offer_help_without_revealing_account_existence(client):
    anonymous = client(None)
    failures = [anonymous.post('/api/v1/auth/login', json={
        'username':username, 'password':'Wrong-password-for-message-test',
    }) for username in ['student01', 'unknown_message_test_user']]
    assert all(response.status_code == 401 for response in failures)
    assert failures[0].json()['message'] == failures[1].json()['message']
    assert '大小写' in failures[0].json()['message']
    assert 'Wrong-password' not in failures[0].text
