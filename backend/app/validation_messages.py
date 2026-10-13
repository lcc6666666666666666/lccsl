"""Public validation errors: useful Chinese messages without submitted values."""
import re


FIELD_LABELS = {
    'username': '登录账号', 'password': '密码', 'old_password': '原密码',
    'new_password': '新密码', 'name': '姓名', 'birth_date': '出生日期',
    'identity_number': '身份证号码', 'status': '人员状态', 'department': '所属院系',
    'graduation_date': '毕业日期', 'qualification_ids': '任课资格', 'version': '数据版本',
    'primary': '首选课程', 'alternates': '备选课程', 'offering_ids': '授课教学班',
    'expected_ids': '原授课安排', 'student_id': '学生编号', 'grade': '成绩',
    'grades': '成绩列表', 'registration_open': '选课开始时间',
    'registration_close': '选课结束时间', 'semester_id': '学期编号',
    'offering_id': '教学班编号', 'user_id': '人员编号', 'kind': '人员类型',
    'event_id': '计费任务编号', 'q': '搜索内容', 'page': '页码',
    'page_size': '每页条数', 'include_inactive': '显示停用人员选项',
    'idempotency-key': '请求标识',
}

# Only known, constant model-validator messages may cross the API boundary.
# Never send an arbitrary ValueError message, its input, or its context to a user.
CUSTOM_ERRORS = {
    '身份证号码必须为18位，前17位为数字，末位为数字或X': ('identity_number', '身份证号码必须为18位，前17位为数字，末位为数字或X；请核对后重新填写'),
    '身份证号码中的出生日期无效': ('identity_number', '身份证号码中的出生日期无效，请核对第7～14位是否为真实的年月日'),
    '身份证号码中的出生日期必须早于今天': ('identity_number', '身份证号码中的出生日期必须早于今天，请核对第7～14位'),
    '身份证号码校验位错误，请核对号码': ('identity_number', '身份证号码校验位错误，请核对全部18位号码，尤其是末位数字或X'),
    '身份证号码必须以字符串填写': ('identity_number', '身份证号码必须以文字填写；接口请求应将号码放在双引号内'),
    '出生日期必须早于今天': ('birth_date', '出生日期必须早于今天，请选择实际出生日期'),
    '出生日期与身份证号码中的出生日期不一致': ('birth_date', '出生日期与身份证号码中的出生日期不一致，请核对出生日期和身份证号码第7～14位'),
    '毕业日期必须晚于出生日期': ('graduation_date', '毕业日期必须晚于出生日期，请重新选择毕业日期'),
    '新增人员必须填写18位身份证号码': ('identity_number', '新增人员必须填写18位身份证号码，请填写完整号码'),
    '选课时间必须包含时区': ('registration_open', '选课开始时间和结束时间必须包含时区，请重新选择时间；接口请求可使用+08:00或Z'),
    '选课时间超出可表示范围，请检查日期和时区': ('registration_close', '选课时间超出可表示范围，请检查开始日期、结束日期和时区'),
    '结束时间必须晚于开始时间': ('registration_close', '选课结束时间必须晚于开始时间，请调整选课时间范围'),
}


def field_detail(field, message, error_type=None):
    detail = {'field': field, 'message': message}
    if error_type:
        detail['type'] = error_type
    return detail


def _location(location):
    # extra_forbidden may contain user-supplied property names. Do not echo them.
    parts = [str(part) for part in location
             if part in ('body', 'query', 'path', 'header')
             or (isinstance(part, str) and part in FIELD_LABELS)
             or (type(part) is int and 0 <= part < 10000)]
    fields = [part for part in parts if part in FIELD_LABELS]
    field = fields[-1] if fields else None
    label = FIELD_LABELS.get(field, '请求内容')
    if 'grades' in parts and any(part.isdecimal() for part in parts):
        index = next(int(part) for part in parts if part.isdecimal())
        label = f'第{index + 1}名学生的{label}'
    elif parts and parts[-1].isdecimal():
        label += f'第{int(parts[-1]) + 1}项'
    return '.'.join(parts) or 'body', field, label


def _number(context, key):
    value = context.get(key)
    return str(value) if type(value) in (int, float) and abs(value) <= 1_000_000 else '规定'


def validation_details(errors):
    details = []
    for item in errors:
        location, field, label = _location(item.get('loc', ()))
        kind = item.get('type', '')
        context = item.get('ctx') or {}
        raw_message = item.get('msg', '')
        custom = CUSTOM_ERRORS.get(raw_message.removeprefix('Value error, ')) if kind == 'value_error' else None
        if custom:
            field, message = custom
            location = 'body.' + field
        elif kind == 'missing':
            message = f'{label}不能为空，请填写后提交'
        elif kind == 'string_too_short':
            limit = _number(context, 'min_length')
            message = f'{label}不能为空，请填写后提交' if limit == '1' else f'{label}至少需要{limit}个字符，请补充内容'
        elif kind == 'string_too_long':
            message = f'{label}最多允许{_number(context, "max_length")}个字符，请缩短内容'
        elif kind == 'string_pattern_mismatch':
            message = ('登录账号只能使用英文字母、数字、下划线（_）、点（.）或短横线（-），不能包含中文或空格'
                       if field == 'username' else f'{label}格式不正确，请按该字段说明重新填写')
        elif kind in ('string_type', 'string_unicode'):
            message = f'{label}必须是有效文字，请重新填写'
        elif kind.startswith('date_') or kind in ('date_type',):
            message = f'{label}不是有效日期，请选择真实日期或使用YYYY-MM-DD格式（例如2004-01-01）'
        elif kind.startswith('datetime_'):
            message = f'{label}不是有效日期时间，请重新选择日期和时间，并包含时区（例如2026-10-13T09:00:00+08:00）'
        elif kind in ('int_parsing', 'int_type', 'int_from_float'):
            message = f'{label}必须是整数，不能包含小数或其他文字'
        elif kind in ('float_parsing', 'float_type', 'decimal_parsing', 'decimal_type', 'finite_number'):
            message = f'{label}必须是有限的有效数字，请检查数值格式'
        elif kind == 'greater_than_equal':
            message = f'{label}必须大于或等于{_number(context, "ge")}，请调整数值'
        elif kind == 'greater_than':
            message = f'{label}必须大于{_number(context, "gt")}，请调整数值'
        elif kind == 'less_than_equal':
            message = f'{label}必须小于或等于{_number(context, "le")}，请调整数值'
        elif kind == 'less_than':
            message = f'{label}必须小于{_number(context, "lt")}，请调整数值'
        elif kind == 'too_long':
            message = f'{label}最多允许{_number(context, "max_length")}项，请减少选择'
        elif kind == 'too_short':
            message = f'{label}至少需要{_number(context, "min_length")}项，请补充选择'
        elif kind == 'list_type':
            message = f'{label}必须以列表提交，请重新选择后再提交'
        elif kind in ('bool_parsing', 'bool_type'):
            message = f'{label}只能选择是或否；接口请求请使用true或false'
        elif kind == 'literal_error' and field == 'grade':
            message = f'{label}只能为A、B、C、D、F、I，暂不录入时请留空'
        elif kind == 'extra_forbidden':
            message = '请求包含不允许修改的字段，请刷新页面后重新填写；接口调用请移除文档未列出的字段'
        elif kind == 'json_invalid':
            message = '请求内容不是有效JSON，请检查引号、逗号和括号；页面操作请刷新后重试'
        elif kind in ('model_attributes_type', 'model_type', 'dict_type'):
            message = f'{label}结构不正确，应提交包含字段名的JSON对象；请刷新页面后重试'
        else:
            message = f'{label}格式或内容不正确，请按该字段说明重新填写；仍失败时请提供请求编号联系管理员'
        detail = field_detail(location, message)
        if detail not in details:
            details.append(detail)
    return details


def validation_message(details):
    return '请修改以下内容：' + '；'.join(detail['message'] for detail in details)


def unique_person_conflict(exc):
    """Classify MySQL's final constraint name, never its SQL or duplicate value."""
    args = getattr(getattr(exc, 'orig', None), 'args', ())
    if len(args) < 2 or args[0] != 1062 or not isinstance(args[1], str):
        return None
    match = re.search(r"for key ['`]([^'`]+)['`]\s*$", args[1])
    if not match:
        return None
    constraint = match[1].split('.')[-1]
    if constraint == 'username':
        return ('USERNAME_ALREADY_EXISTS', 'body.username', '登录账号已被使用（包括已停用账号），请更换一个账号；账号不区分大小写')
    if constraint == 'identity_fingerprint':
        return ('IDENTITY_ALREADY_EXISTS', 'body.identity_number', '身份证号码已登记（包括已停用人员），请核对号码或编辑原有人员记录')
    return None
