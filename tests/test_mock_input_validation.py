import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.mocks import Event, accept_event, catalog_app
from backend.app.models import BillingReceipt


def test_mock_rejects_non_ascii_integration_key_without_server_error():
    with TestClient(catalog_app) as client:
        response = client.get('/catalog', headers={b'X-Integration-Key': b'\xff'})
    assert response.status_code == 403


@pytest.mark.parametrize('value', [True, False, 0, -1, 2147483648])
def test_mock_rejects_invalid_identity_before_recording_event(db, value):
    event = Event(event_id=str(uuid.uuid4()), kind='RegistrationUpdated',
                  payload={'student_id': value, 'semester_id': 2})
    with pytest.raises(HTTPException) as error:
        accept_event(event)
    assert error.value.status_code == 422
    assert db.scalar(select(func.count(BillingReceipt.event_id))) == 0
