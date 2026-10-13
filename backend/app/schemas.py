from datetime import date, datetime, timezone
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def identity_check_character(body: str) -> str:
    """GB 11643-1999 MOD 11-2; also used to generate synthetic demo fixtures."""
    if not re.fullmatch(r'[0-9]{17}', body):
        raise ValueError('身份证号码本体必须为17位数字')
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    return '10X98765432'[sum(int(digit) * weight for digit, weight in zip(body, weights)) % 11]


def validate_identity_number(value: str) -> str:
    """Validate format/date/checksum, not an online identity or address verification."""
    value = value.strip().upper()
    if not re.fullmatch(r'[1-9][0-9]{16}[0-9X]', value):
        raise ValueError('身份证号码必须为18位，前17位为数字，末位为数字或X')
    try:
        birthday = date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
    except ValueError:
        raise ValueError('身份证号码中的出生日期无效') from None
    if birthday >= date.today():
        raise ValueError('身份证号码中的出生日期必须早于今天')
    if identity_check_character(value[:17]) != value[-1]:
        raise ValueError('身份证号码校验位错误，请核对号码')
    return value

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Login(Input):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)

class PasswordChange(Input):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=False)

class ScheduleInput(Input):
    version: int = Field(ge=0)
    primary: list[int] = Field(default_factory=list, max_length=4)
    alternates: list[int] = Field(default_factory=list, max_length=2)

class VersionInput(Input):
    version: int = Field(ge=0)

class TeachingInput(Input):
    offering_ids: list[int] = Field(max_length=100)
    expected_ids: list[int] = Field(max_length=100)

class GradeItem(Input):
    student_id: int = Field(gt=0)
    grade: Literal['A','B','C','D','F','I'] | None
    version: int = Field(ge=0)

class GradesInput(Input):
    grades: list[GradeItem] = Field(min_length=1, max_length=10)

class PersonInput(Input):
    name: str = Field(min_length=1, max_length=80)
    birth_date: date
    identity_number: str | None = Field(default=None, max_length=18)
    status: str = Field(min_length=1, max_length=30)
    department: str | None = Field(default=None, max_length=80)
    graduation_date: date | None = None
    qualification_ids: list[int] = Field(default_factory=list, max_length=100)
    version: int = Field(default=0, ge=0)

    @field_validator('identity_number', mode='before')
    @classmethod
    def identity(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError('身份证号码必须以字符串填写')
        if not value.strip():
            return None  # An empty edit preserves the existing encrypted value.
        return validate_identity_number(value)

    @model_validator(mode='after')
    def dates(self):
        if self.birth_date >= date.today():
            raise ValueError('出生日期必须早于今天')
        if self.identity_number and self.identity_number[6:14] != self.birth_date.strftime('%Y%m%d'):
            raise ValueError('出生日期与身份证号码中的出生日期不一致')
        if self.graduation_date and self.graduation_date <= self.birth_date:
            raise ValueError('毕业日期必须晚于出生日期')
        return self

class PersonCreate(PersonInput):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')

    @model_validator(mode='after')
    def identity_required(self):
        if not self.identity_number:
            raise ValueError('新增人员必须填写18位身份证号码')
        return self

class SemesterInput(Input):
    version: int = Field(ge=1)
    registration_open: datetime
    registration_close: datetime

    @model_validator(mode='after')
    def dates(self):
        if not self.registration_open.tzinfo or not self.registration_close.tzinfo:
            raise ValueError('选课时间必须包含时区')
        try:
            opening = self.registration_open.astimezone(timezone.utc)
            closing = self.registration_close.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            raise ValueError('选课时间超出可表示范围，请检查日期和时区') from None
        if closing <= opening:
            raise ValueError('结束时间必须晚于开始时间')
        return self
