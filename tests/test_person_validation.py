"""Person input boundaries and rollback, exercised against the isolated MySQL schema."""
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.app.models import User
from backend.app.schemas import PersonInput, SemesterInput, identity_check_character, validate_identity_number
from backend.app.security import decrypt_identity, encrypt_identity, identity_fingerprint


def synthetic_identity(birthday='20000101', sequence=501):
    # Synthetic address: format/checksum testing only, not a real citizen record.
    body = f'990000{birthday}{sequence:03d}'
    return body + identity_check_character(body)


def person_body(**changes):
    body = dict(username='validation_person', name='校验测试人员', birth_date='2000-01-01',
                identity_number=synthetic_identity(), status='在籍', department='计算机学院',
                graduation_date='2028-06-30', qualification_ids=[])
    body.update(changes)
    return body


def edit_body(person, **changes):
    body = {key: person[key] for key in ('name', 'birth_date', 'status', 'department',
                                        'graduation_date', 'qualification_ids', 'version')}
    body.update(changes)
    return body


def test_identity_standard_examples_and_lowercase_x():
    # Independent published GB 11643-1999 Appendix A vectors prevent a mirrored
    # generation/validation implementation from masking an incorrect checksum.
    assert validate_identity_number('11010519491231002x') == '11010519491231002X'
    assert validate_identity_number('440524188001010014') == '440524188001010014'


@pytest.mark.parametrize('identity', [
    '123', '1' * 17, '1' * 19, 'ABCDEFGHIJKLMNO123',
    '１１０１０５１９４９１２３１００２Ｘ', '110105194912310020',
    '110105200002300021', '000000200001015016',
])
def test_bad_identity_rejected_without_creating_person(client, db, identity):
    admin = client('admin')
    before = db.scalar(select(func.count(User.id)))
    response = admin.post('/api/v1/registrar/people/student', json=person_body(identity_number=identity))
    assert response.status_code == 422
    assert response.json()['request_id']
    assert identity not in str(response.json()['details'])
    db.expire_all()
    assert db.scalar(select(func.count(User.id))) == before


@pytest.mark.parametrize('identity', [None, '', '   '])
def test_new_identity_required(client, identity):
    response = client('admin').post('/api/v1/registrar/people/student', json=person_body(identity_number=identity))
    assert response.status_code == 422
    assert '必须填写18位身份证号码' in response.text


def test_birthdate_must_match_identity_and_future_dates_rejected(client):
    admin = client('admin')
    response = admin.post('/api/v1/registrar/people/student', json=person_body(birth_date='2000-01-02'))
    assert response.status_code == 422 and '不一致' in response.text
    future = synthetic_identity('29990101')
    response = admin.post('/api/v1/registrar/people/student', json=person_body(identity_number=future, birth_date='2999-01-01'))
    assert response.status_code == 422 and '早于今天' in response.text


def test_identity_case_normalization_encryption_and_duplicate_prevention(client, db):
    admin = client('admin')
    identity = next(synthetic_identity(sequence=n) for n in range(501, 600)
                    if synthetic_identity(sequence=n).endswith('X'))
    response = admin.post('/api/v1/registrar/people/student', json=person_body(identity_number=identity.lower()))
    assert response.status_code == 200, response.text
    person = response.json()['person']
    assert isinstance(person['id'], int) and person['id'] > 0
    assert person['department'] == '计算机学院'
    assert person['identity_needs_correction'] is False
    assert identity not in response.text and person['identity_masked'].endswith(identity[-4:])
    stored = db.get(User, person['id'])
    assert decrypt_identity(stored.identity_cipher) == identity
    assert stored.identity_fingerprint == identity_fingerprint(identity)
    assert identity not in stored.identity_cipher
    duplicate = admin.post('/api/v1/registrar/people/student', json=person_body(username='another_person', identity_number=identity))
    assert duplicate.status_code == 409
    db.expire_all()
    assert db.scalar(select(User.id).where(User.username == 'another_person')) is None


def test_edit_preserves_identity_and_blocks_mismatching_birthdate_atomically(client, db):
    admin = client('admin')
    person = admin.post('/api/v1/registrar/people/student', json=person_body()).json()['person']
    path = f"/api/v1/registrar/people/{person['id']}"
    original_cipher = db.get(User, person['id']).identity_cipher
    for empty in (None, ''):
        response = admin.put(path, json=edit_body(person, name='已更新姓名', identity_number=empty))
        assert response.status_code == 200, response.text
        person = response.json()
        db.expire_all()
        assert db.get(User, person['id']).identity_cipher == original_cipher
    rejected = admin.put(path, json=edit_body(person, name='不应保存', birth_date='2000-01-02'))
    assert rejected.status_code == 422 and rejected.json()['code'] == 'IDENTITY_BIRTH_DATE_MISMATCH'
    db.expire_all()
    stored = db.get(User, person['id'])
    assert stored.name == '已更新姓名' and stored.birth_date == date(2000, 1, 1)
    assert stored.version == person['version'] and stored.identity_cipher == original_cipher


def test_legacy_identity_remains_editable_but_flagged(client, db):
    user = db.get(User, 101)
    user.identity_cipher = encrypt_identity('DEMO-LEGACY-101')
    user.identity_fingerprint = identity_fingerprint('DEMO-LEGACY-101')
    db.commit()
    admin = client('admin')
    person = admin.get('/api/v1/registrar/people/student?q=101').json()['items'][0]
    assert person['identity_needs_correction'] is True
    changed = admin.put('/api/v1/registrar/people/101', json=edit_body(person, name='保留旧示例号码', identity_number=None))
    assert changed.status_code == 200 and changed.json()['identity_needs_correction'] is True
    db.expire_all()
    assert decrypt_identity(db.get(User, 101).identity_cipher) == 'DEMO-LEGACY-101'
    corrected = admin.put('/api/v1/registrar/people/101', json=edit_body(changed.json(), identity_number=synthetic_identity('20040101')))
    assert corrected.status_code == 200 and corrected.json()['identity_needs_correction'] is False


def test_legacy_lowercase_fingerprint_cannot_bypass_duplicate_check(client, db):
    identity = next(synthetic_identity(sequence=n) for n in range(501, 600)
                    if synthetic_identity(sequence=n).endswith('X'))
    old = db.get(User, 101)
    old.birth_date = date(2000, 1, 1)
    old.identity_cipher = encrypt_identity(identity.lower())
    old.identity_fingerprint = identity_fingerprint(identity.lower())
    db.commit()
    response = client('admin').post('/api/v1/registrar/people/student', json=person_body(identity_number=identity))
    assert response.status_code == 409 and response.json()['code'] == 'IDENTITY_ALREADY_EXISTS'
    db.expire_all()
    assert db.scalar(select(User.id).where(User.username == 'validation_person')) is None


def test_role_specific_fields_are_checked_and_failures_roll_back(client, db):
    admin = client('admin')
    before = db.scalar(select(func.count(User.id)))
    responses = [
        admin.post('/api/v1/registrar/people/professor', json=person_body(department='   ')),
        admin.post('/api/v1/registrar/people/professor', json=person_body(qualification_ids=[9999])),
        admin.post('/api/v1/registrar/people/professor', json=person_body(qualification_ids=[1, 1])),
        admin.post('/api/v1/registrar/people/student', json=person_body(qualification_ids=[1])),
    ]
    assert [r.status_code for r in responses] == [422] * 4
    assert [r.json()['code'] for r in responses] == [
        'DEPARTMENT_REQUIRED', 'INVALID_QUALIFICATION', 'DUPLICATE_QUALIFICATION', 'INVALID_QUALIFICATION']
    db.expire_all()
    assert db.scalar(select(func.count(User.id))) == before


def test_blank_name_status_and_invalid_graduation_date_are_rejected():
    for changes in ({'name': '   '}, {'status': '\t'}, {'graduation_date': '1999-01-01'}):
        data = person_body(**changes)
        data.pop('username')
        with pytest.raises(ValidationError):
            PersonInput.model_validate(data)


def test_timezone_conversion_overflow_is_a_validation_error():
    for opening, closing in [
        ('0001-01-01T00:00:00+14:00', '2026-01-01T00:00:00Z'),
        ('2026-01-01T00:00:00Z', '9999-12-31T23:59:59-14:00'),
    ]:
        with pytest.raises(ValidationError, match='超出可表示范围'):
            SemesterInput(version=1, registration_open=opening, registration_close=closing)
