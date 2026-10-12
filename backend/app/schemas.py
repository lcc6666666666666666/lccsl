from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    identity_number: str | None = Field(default=None, min_length=3, max_length=40)
    status: str = Field(min_length=1, max_length=30)
    department: str | None = Field(default=None, max_length=80)
    graduation_date: date | None = None
    qualification_ids: list[int] = Field(default_factory=list, max_length=100)
    version: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def dates(self):
        if self.birth_date >= date.today():
            raise ValueError('出生日期必须早于今天')
        if self.graduation_date and self.graduation_date <= self.birth_date:
            raise ValueError('毕业日期必须晚于出生日期')
        return self

class PersonCreate(PersonInput):
    username: str = Field(min_length=3, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')

class SemesterInput(Input):
    version: int = Field(ge=1)
    registration_open: datetime
    registration_close: datetime

    @model_validator(mode='after')
    def dates(self):
        if not self.registration_open.tzinfo or not self.registration_close.tzinfo:
            raise ValueError('选课时间必须包含时区')
        if self.registration_close <= self.registration_open:
            raise ValueError('结束时间必须晚于开始时间')
        return self
