from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import String, Text, Integer, Boolean, Date, DateTime, ForeignKey, JSON, Numeric, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.mysql import DATETIME
from .db import Base, utcnow

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80))
    birth_date: Mapped[date | None] = mapped_column(Date)
    identity_cipher: Mapped[str | None] = mapped_column(Text)
    identity_fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(30), default='在籍')
    department: Mapped[str | None] = mapped_column(String(80))
    graduation_date: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (CheckConstraint("role IN ('student','professor','registrar')"),)

class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class LoginThrottle(Base):
    __tablename__ = 'login_throttles'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    window_start: Mapped[datetime] = mapped_column(DateTime)

class Semester(Base):
    __tablename__ = 'semesters'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    registration_open: Mapped[datetime] = mapped_column(DateTime)
    registration_close: Mapped[datetime] = mapped_column(DateTime)
    state: Mapped[str] = mapped_column(String(16), default='OPEN')
    version: Mapped[int] = mapped_column(default=1)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    close_summary: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (CheckConstraint("state IN ('OPEN','CLOSED')"), CheckConstraint('start_date <= end_date'))

class Course(Base):
    __tablename__ = 'courses'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(80))
    credits: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    description: Mapped[str] = mapped_column(Text, default='')

class Prerequisite(Base):
    __tablename__ = 'prerequisites'
    course_id: Mapped[int] = mapped_column(ForeignKey('courses.id'), primary_key=True)
    prerequisite_id: Mapped[int] = mapped_column(ForeignKey('courses.id'), primary_key=True)

class Qualification(Base):
    __tablename__ = 'qualifications'
    professor_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey('courses.id'), primary_key=True)

class Offering(Base):
    __tablename__ = 'offerings'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey('semesters.id'), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey('courses.id'), index=True)
    professor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), index=True)
    capacity: Mapped[int] = mapped_column(default=10)
    enrolled_count: Mapped[int] = mapped_column(default=0)
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    state: Mapped[str] = mapped_column(String(16), default='OPEN')
    cancellation_reason: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (CheckConstraint('capacity = 10'), CheckConstraint('enrolled_count >= 0 AND enrolled_count <= capacity'),
                     CheckConstraint('fee >= 0'), CheckConstraint("state IN ('OPEN','CLOSED','CANCELLED')"))

class Meeting(Base):
    __tablename__ = 'meetings'
    id: Mapped[int] = mapped_column(primary_key=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey('offerings.id'), index=True)
    weekday: Mapped[int]
    start_minute: Mapped[int]
    end_minute: Mapped[int]
    week_start: Mapped[int] = mapped_column(default=1)
    week_end: Mapped[int] = mapped_column(default=16)
    room: Mapped[str] = mapped_column(String(60))
    __table_args__ = (CheckConstraint('weekday BETWEEN 1 AND 7'), CheckConstraint('start_minute >= 0 AND end_minute <= 1440 AND start_minute < end_minute'), CheckConstraint('week_start > 0 AND week_end >= week_start'))

class Schedule(Base):
    __tablename__ = 'schedules'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    semester_id: Mapped[int] = mapped_column(ForeignKey('semesters.id'))
    version: Mapped[int] = mapped_column(default=0)
    first_submitted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    draft: Mapped[dict | None] = mapped_column(JSON)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint('student_id', 'semester_id'),)

class Enrollment(Base):
    __tablename__ = 'enrollments'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey('offerings.id'), index=True)
    position: Mapped[int] = mapped_column(default=0)
    state: Mapped[str] = mapped_column(String(16), default='ENROLLED')
    status_reason: Mapped[str | None] = mapped_column(String(255))
    grade: Mapped[str | None] = mapped_column(String(1))
    grade_version: Mapped[int] = mapped_column(default=0)
    __table_args__ = (UniqueConstraint('student_id', 'offering_id'),
                     CheckConstraint("state IN ('ENROLLED','DROPPED','CANCELLED')"),
                     CheckConstraint("grade IS NULL OR grade IN ('A','B','C','D','F','I')"))

class TeachingAssignment(Base):
    __tablename__ = 'teaching_assignments'
    professor_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey('offerings.id'), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default='ASSIGNED')
    reason: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (CheckConstraint("state IN ('ASSIGNED','WITHDRAWN')"),)

class Alternate(Base):
    __tablename__ = 'alternates'
    schedule_id: Mapped[int] = mapped_column(ForeignKey('schedules.id'), primary_key=True)
    offering_id: Mapped[int] = mapped_column(ForeignKey('offerings.id'), primary_key=True)
    position: Mapped[int]
    __table_args__ = (UniqueConstraint('schedule_id', 'position'),)

class Invoice(Base):
    __tablename__ = 'invoices'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    semester_id: Mapped[int] = mapped_column(ForeignKey('semesters.id'))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    lines: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default='ACTIVE', server_default='ACTIVE')
    revision: Mapped[int] = mapped_column(default=0, server_default='0')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint('student_id', 'semester_id'), CheckConstraint('total >= 0'))

class Outbox(Base):
    __tablename__ = 'outbox'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default='PENDING')
    attempts: Mapped[int] = mapped_column(default=0)
    # DATETIME(0) rounds fractional seconds on MySQL. Floor an immediately due
    # task so creation near a second boundary cannot accidentally schedule it later.
    next_attempt: Mapped[datetime] = mapped_column(DateTime, default=lambda:utcnow().replace(microsecond=0))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index('ix_outbox_due', 'state', 'next_attempt'),)

class Idempotency(Base):
    __tablename__ = 'idempotency'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    operation: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

class CatalogCache(Base):
    __tablename__ = 'catalog_cache'
    id: Mapped[int] = mapped_column(primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

class LegacyCatalog(Base):
    __tablename__ = 'legacy_catalog'
    id: Mapped[int] = mapped_column(primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)

class FileCatalog(Base):
    """Durable, read-only adapter snapshot from an administrator supplied file."""
    __tablename__ = 'file_catalog'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict] = mapped_column(JSON)
    document: Mapped[dict] = mapped_column(JSON)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    imported_by: Mapped[int] = mapped_column(ForeignKey('users.id'))

class BillingReceipt(Base):
    __tablename__ = 'billing_receipts'
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class BillingInvoice(Base):
    __tablename__ = 'billing_invoices'
    student_id: Mapped[int] = mapped_column(primary_key=True)
    semester_id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default='ACTIVE', server_default='ACTIVE')
    revision: Mapped[int] = mapped_column(default=0, server_default='0')
