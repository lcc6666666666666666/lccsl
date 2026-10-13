"""Detailed errors identify the fix without leaking identity/password/SQL values."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from pymysql.err import IntegrityError as MySQLIntegrityError
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from backend.app import api
from backend.app.main import integrity_error
from backend.app.validation_messages import validation_details, unique_person_conflict
from tests.test_person_validation import person_body, synthetic_identity, edit_body


@pytest.mark.parametrize('changes,field,clue', [
    ({'username': 'ab'}, 'username', '至少需要3个字符'),
    ({'username': '测试 账号'}, 'username', '不能包含中文或空格'),
    ({'name': ' '}, 'name', '姓名不能为空'),
    ({'status': ' '}, 'status', '人员状态不能为空'),
    ({'department': '院' * 81}, 'department', '最多允许80个字符'),
    ({'birth_date': '2000-02-30'}, 'birth_date', '有效日期'),
    ({'birth_date': '2000-01-02'}, 'birth_date', '身份证号码中的出生日期不一致'),
    ({'graduation_date': '1999-01-01'}, 'graduation_date', '毕业日期必须晚于出生日期'),
    ({'identity_number': None}, 'identity_number', '必须填写18位身份证号码'),
    ({'identity_number': '110105194912310020'}, 'identity_number', '校验位错误'),
])
def test_person_validation_identifies_field_and_correction(client, changes, field, clue):
    body = person_body(**changes)
    response = client('admin').post('/api/v1/registrar/people/student', json=body)
    assert response.status_code == 422
    payload = response.json()
    assert payload['code'] == 'VALIDATION_ERROR'
    assert clue in payload['message']  # Also works with old clients displaying only message.
    assert any(item['field'] == f'body.{field}' and clue in item['message'] for item in payload['details'])
    assert body.get('identity_number') not in response.text if body.get('identity_number') else True
    assert payload['request_id']
    assert 'Value error' not in response.text and 'String should' not in response.text


def test_multiple_errors_are_all_visible_with_no_raw_input(client):
    response = client('admin').post('/api/v1/registrar/people/student', json=person_body(
        username='ab', name='', status='', identity_number='private-invalid-identity',
        qualification_ids=['private-invalid-course'],
    ))
    assert response.status_code == 422
    payload = response.json()
    fields = {item['field'] for item in payload['details']}
    assert {'body.username', 'body.name', 'body.status', 'body.identity_number', 'body.qualification_ids.0'} <= fields
    assert all(item['message'] in payload['message'] for item in payload['details'])
    assert 'private-invalid' not in response.text
    assert all(set(item) == {'field', 'message'} for item in payload['details'])


def test_missing_fields_and_unknown_fields_are_explained_without_echo(client):
    response = client('admin').post('/api/v1/registrar/people/student', json={'private-property': 'private-value'})
    assert response.status_code == 422
    assert '姓名不能为空' in response.text and '登录账号不能为空' in response.text
    assert '不允许修改的字段' in response.text
    assert 'private-property' not in response.text and 'private-value' not in response.text


def test_existing_username_and_identity_return_field_errors(client):
    admin = client('admin')
    person = admin.post('/api/v1/registrar/people/student', json=person_body()).json()['person']
    duplicate_username = admin.post('/api/v1/registrar/people/student', json=person_body(
        username='VALIDATION_PERSON', identity_number=synthetic_identity(sequence=502)))
    assert duplicate_username.status_code == 409
    assert duplicate_username.json()['code'] == 'USERNAME_ALREADY_EXISTS'
    assert duplicate_username.json()['details'][0]['field'] == 'body.username'
    duplicate_identity = admin.post('/api/v1/registrar/people/student', json=person_body(username='other_person'))
    assert duplicate_identity.status_code == 409
    assert duplicate_identity.json()['details'][0]['field'] == 'body.identity_number'
    mismatch = admin.put(f"/api/v1/registrar/people/{person['id']}", json=edit_body(person, birth_date='2000-01-02'))
    assert mismatch.status_code == 422
    assert mismatch.json()['details'][0]['field'] == 'body.birth_date'


@pytest.mark.parametrize('duplicate', ['username', 'identity'])
def test_concurrent_person_unique_conflict_still_identifies_field(client, monkeypatch, duplicate):
    admins = [client('admin'), client('admin')]
    barrier = Barrier(2)
    if duplicate == 'username':
        original = api.password_hasher.hash
        def hash_after_both_prechecks(value):
            barrier.wait(timeout=15)
            return original(value)
        monkeypatch.setattr(api, 'password_hasher', SimpleNamespace(hash=hash_after_both_prechecks))
        bodies = [person_body(identity_number=synthetic_identity(sequence=501 + i)) for i in range(2)]
        expected_field, expected_code = 'body.username', 'USERNAME_ALREADY_EXISTS'
    else:
        original = api.encrypt_identity
        def encrypt_after_both_prechecks(value):
            barrier.wait(timeout=15)
            return original(value)
        monkeypatch.setattr(api, 'encrypt_identity', encrypt_after_both_prechecks)
        bodies = [person_body(username=f'concurrent_person_{i}') for i in range(2)]
        expected_field, expected_code = 'body.identity_number', 'IDENTITY_ALREADY_EXISTS'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(admin.post, '/api/v1/registrar/people/student', json=body)
                   for admin, body in zip(admins, bodies)]
        responses = [future.result(timeout=30) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()['code'] == expected_code
    assert conflict.json()['details'][0]['field'] == expected_field
    assert all(body['identity_number'] not in conflict.text for body in bodies)
    assert 'INSERT' not in conflict.text and 'UPDATE' not in conflict.text


def test_validation_translation_covers_lists_numbers_dates_and_unknown_errors():
    cases = [
        ({'loc': ('body', 'grades', 1, 'grade'), 'type': 'literal_error'}, '第2名学生的成绩只能为A'),
        ({'loc': ('body', 'primary'), 'type': 'too_long', 'ctx': {'max_length': 4}}, '最多允许4项'),
        ({'loc': ('query', 'page'), 'type': 'greater_than_equal', 'ctx': {'ge': 1}}, '页码必须大于或等于1'),
        ({'loc': ('query', 'include_inactive'), 'type': 'bool_parsing'}, '只能选择是或否'),
        ({'loc': ('body', 'registration_close'), 'type': 'datetime_parsing'}, '有效日期时间'),
        ({'loc': ('body',), 'type': 'json_invalid'}, '不是有效JSON'),
        ({'loc': ('body', 'new_password'), 'type': 'value_error', 'msg': 'Value error, private-secret', 'ctx': {'error': 'private-secret'}, 'input': 'private-secret'}, '新密码格式或内容不正确'),
    ]
    for item, clue in cases:
        details = validation_details([item])
        assert clue in details[0]['message']
        assert 'private-secret' not in str(details)


@pytest.mark.parametrize('constraint,field', [('username', 'body.username'), ('users.identity_fingerprint', 'body.identity_number')])
def test_unique_constraint_handler_never_leaks_raw_database_error(constraint, field):
    exc = IntegrityError('INSERT secret SQL', {'password': 'private-secret'},
                         MySQLIntegrityError(1062, f"Duplicate entry 'private-secret' for key '{constraint}'"))
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/v1/registrar/people/student', 'headers': []})
    request.state.request_id = 'error-test'
    response = asyncio.run(integrity_error(request, exc))
    payload = json.loads(response.body)
    assert response.status_code == 409 and payload['details'][0]['field'] == field
    assert b'private-secret' not in response.body and b'INSERT' not in response.body
    assert response.headers['X-Request-ID'] == 'error-test'
    unknown = IntegrityError('SQL', {}, MySQLIntegrityError(1062, "Duplicate entry 'value for key username' for key 'other_key'"))
    assert unique_person_conflict(unknown) is None
