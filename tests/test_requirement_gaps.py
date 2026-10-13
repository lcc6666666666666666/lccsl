"""Regression cases found by comparing the implementation to the original PDF."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.app.catalog import SQLCatalogAdapter, clear_memory_cache, ensure_catalog
from backend.app.db import utcnow
from backend.app.errors import DomainError
from backend.app.models import CatalogCache, Course, Enrollment, Offering
from .conftest import submit


def test_grade_correction_does_not_block_unrelated_add_drop_but_reentry_is_checked(client, db):
    """PDF p. 14: prerequisites are checked for courses not already enrolled in."""
    student = client()
    response = submit(student, [2, 3, 4, 7], [5, 6])
    assert response.status_code == 200, response.text
    old_grade = db.scalar(select(Enrollment).where(
        Enrollment.student_id == 101, Enrollment.offering_id == 101))
    old_grade.grade = 'F'
    db.commit()

    # Retain data structures, drop introduction, and add linear algebra.
    response = submit(student, [2, 4, 7, 5], [3, 6], version=1)
    assert response.status_code == 200, response.text
    assert response.json()['primary'] == [2, 4, 7, 5]

    # Once dropped, data structures becomes a new selection and must be checked.
    response = submit(student, [2, 4, 5], [3, 6], version=2)
    assert response.status_code == 200, response.text
    response = submit(student, [2, 4, 7, 5], [3, 6], version=3)
    assert response.status_code == 409
    assert response.json()['code'] == 'PREREQUISITE_NOT_MET'
    assert student.get('/api/v1/student/schedules/2').json()['primary'] == [2, 4, 5]
    db.expire_all()
    assert db.get(Offering, 7).enrolled_count == 0


@pytest.mark.parametrize('document', [
    {'courses': [{'id': True, 'name': '错误课程'}], 'offerings': []},
    {'courses': [{'id': 1, 'name': '先更新'}, {'id': 1, 'name': '重复覆盖'}], 'offerings': []},
    {'courses': [{'id': 1, 'code': 'CS101'}, {'id': 2, 'code': 'cs101'}], 'offerings': []},
    {'courses': [{'id': 1, 'credits': 'NaN'}], 'offerings': []},
    {'courses': [{'id': 1, 'credits': '3.14'}], 'offerings': []},
    {'courses': [{'id': 1, 'name': '先更新'}, None], 'offerings': []},
    {'courses': [], 'offerings': [{'id': 1, 'fee': '-0.01'}]},
    {'courses': [], 'offerings': [{'id': 1, 'fee': '600.001'}]},
    {'courses': [], 'offerings': [{'id': 1, 'fee': 'Infinity'}]},
    {'courses': [], 'offerings': [{'id': 1, 'semester_id': False}]},
    {'courses': [], 'offerings': [{'id': 1}, {'id': 1}]},
    {'courses': [], 'offerings': ['invalid-row']},
])
def test_malformed_catalog_is_rejected_without_partial_refresh(db, monkeypatch, document):
    original_name = db.get(Course, 1).name
    cache = db.get(CatalogCache, 1)
    cache.expires_at = utcnow() - timedelta(seconds=1)
    original_document = cache.document
    db.commit()
    clear_memory_cache()
    monkeypatch.setattr(SQLCatalogAdapter, 'read', lambda self: document)

    with pytest.raises(DomainError) as error:
        ensure_catalog()
    assert error.value.code == 'CATALOG_INVALID'
    db.expire_all()
    assert db.get(Course, 1).name == original_name
    assert db.get(CatalogCache, 1).document == original_document
    assert db.get(CatalogCache, 1).expires_at < utcnow()


def test_optional_catalog_description_can_be_cleared(db, monkeypatch):
    db.get(CatalogCache, 1).expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    clear_memory_cache()
    monkeypatch.setattr(SQLCatalogAdapter, 'read', lambda self: {
        'courses': [{'id': 1, 'description': '', 'credits': '3.0'}],
        'offerings': [{'id': 1, 'fee': '600.00'}],
    })
    ensure_catalog()
    db.expire_all()
    assert db.get(Course, 1).description == ''
