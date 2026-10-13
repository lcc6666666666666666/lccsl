from datetime import datetime, time, timedelta, timezone

from backend.app.models import Semester


def test_non_ascii_csrf_header_is_rejected_without_server_error(client):
    c = client()
    response = c.put('/api/v1/student/schedules/2/draft', json={'version': 0},
                     headers={b'X-CSRF-Token': b'\xff'})
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_INVALID'


def test_non_decimal_unicode_search_does_not_crash_people_list(client):
    response = client('admin').get('/api/v1/registrar/people/student', params={'q': '²'})
    assert response.status_code == 200
    # MySQL's Unicode collation may match the superscript with ordinary "2".
    # The regression is int('²') raising a server error, not its collation rules.
    assert isinstance(response.json()['items'], list)
    assert response.json()['total'] == len(response.json()['items'])


def test_invalid_content_length_is_rejected_with_standard_headers(client):
    response = client(None).post('/api/v1/auth/login', content='{}',
                                headers={b'Content-Type': b'application/json',
                                         b'Content-Length': b'\xb2'})
    assert response.status_code == 413
    assert response.headers['X-Request-ID'] == response.json()['request_id']
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_early_access_denials_have_request_ids_and_no_cache(client):
    c = client()
    requests = [
        c.get('/api/v1/student/report-card?student_id=102'),
        c.post('/api/v1/auth/logout', json={}, headers={'Origin': 'https://attacker.invalid'}),
        c.post('/api/v1/auth/logout', content='{}', headers={'Content-Type': 'text/plain'}),
    ]
    for response in requests:
        assert response.status_code in (403, 415)
        assert response.headers['X-Request-ID'] == response.json()['request_id']
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['X-Frame-Options'] == 'DENY'


def test_registration_end_date_checked_after_utc_conversion(client, db):
    term = db.get(Semester, 2)
    previous_close = term.registration_close
    close = datetime.combine(term.end_date, time(23, 30), tzinfo=timezone(-timedelta(hours=12)))
    response = client('admin').put('/api/v1/registrar/semesters/2', json={
        'version': term.version,
        'registration_open': term.registration_open.replace(tzinfo=timezone.utc).isoformat(),
        'registration_close': close.isoformat(),
    })
    assert response.status_code == 422
    assert response.json()['code'] == 'INVALID_WINDOW'
    db.expire_all()
    assert db.get(Semester, 2).registration_close == previous_close
    assert db.get(Semester, 2).version == 1
