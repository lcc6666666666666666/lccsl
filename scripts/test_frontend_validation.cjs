/*
 * Run from any directory with the project's Node.js environment:
 *   .tools\node-v22.23.3-win-x64\node.exe scripts\test_frontend_validation.cjs
 * Uses the already installed frontend TypeScript compiler. No server or database.
 */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'frontend', 'node_modules', 'typescript'));

function loadTypescript(filename, globals = {}) {
  const sourcePath = path.join(root, 'frontend', 'src', filename);
  const compiled = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: sourcePath,
  });
  const module = { exports: {} };
  const context = vm.createContext({ exports: module.exports, module, ...globals });
  new vm.Script(compiled.outputText, { filename: sourcePath }).runInContext(context, {
    timeout: 1000,
  });
  return module.exports;
}

const { personFormError, personFormErrors } = loadTypescript('personValidation.ts');
const { errorMessage, validationIssues, ApiError } = loadTypescript('api.ts');

// Publicly used format example, not a record read from the application database.
const base = {
  username: 'audit.user',
  name: '测试人员',
  birth_date: '1949-12-31',
  identity_number: '11010519491231002X',
  status: '在籍',
  department: '',
  graduation_date: null,
  qualification_ids: [],
};
const today = '2026-10-13';

function validate(change, expected, kind = 'student', editing = false) {
  const actual = personFormError({ ...base, ...change }, kind, editing, today);
  if (expected === null) {
    assert.equal(actual, null);
  } else {
    assert.ok(typeof actual === 'string' && actual.includes(expected),
      `应包含“${expected}”，实际：${String(actual)}`);
  }
}

const cases = [
  ['合法18位号码与大写X', () => validate({}, null)],
  ['拒绝15位号码', () => validate({ identity_number: base.identity_number.slice(0, 15) }, '18位')],
  ['拒绝全角数字', () => validate({ identity_number: '１'.repeat(18) }, '18位')],
  ['拒绝错误校验码', () => validate({ identity_number: base.identity_number.slice(0, 17) + '0' }, '校验码')],
  ['拒绝出生日期不一致', () => validate({ birth_date: '1950-01-01' }, '不一致')],
  ['拒绝身份证中不存在的日期', () => validate({ identity_number: '110105200402300010' }, '出生日期无效')],
  ['新增人员身份证必填', () => validate({ identity_number: '' }, '请输入18位')],
  ['编辑人员空身份证表示保留', () => validate({ identity_number: '' }, null, 'student', true)],
  ['姓名不能只有空白', () => validate({ name: '   ' }, '姓名')],
  ['状态不能只有空白', () => validate({ status: '   ' }, '人员状态')],
  ['教授院系必填', () => validate({}, '院系', 'professor')],
  ['接受教授完整资料', () => validate({ department: '软件工程' }, null, 'professor')],
  ['拒绝未来出生日期', () => validate({ birth_date: '2026-10-14' }, '早于今天')],
  ['毕业日期必须晚于生日', () => validate({ graduation_date: base.birth_date }, '晚于出生日期')],
  ['拒绝不存在的毕业日期', () => validate({ graduation_date: '2026-02-30' }, '有效日期')],
  ['拒绝过短账号', () => validate({ username: 'a' }, '3～64')],
  ['接受账号允许的字符', () => validate({ username: 'abc_._12-' }, null)],
  ['接受小写x校验位', () => validate({ identity_number: base.identity_number.toLowerCase() }, null)],
  ['显示中文字段和服务端验证原因', () => assert.equal(errorMessage({
    code: 'VALIDATION_ERROR', message: '输入内容不符合要求',
    details: [{ field: 'body.identity_number', message: 'Value error, 身份证号码校验码不正确' }],
  }), '身份证号码：身份证号码校验码不正确')],
  ['翻译字段长度错误', () => assert.equal(errorMessage({
    code: 'VALIDATION_ERROR',
    details: [{ field: 'body.username', message: 'String should have at least 3 characters' }],
  }), '账号：至少需要3个字符')],
  ['保留业务错误消息', () => assert.equal(errorMessage({
    code: 'DOMAIN_ERROR', message: '名额已满', details: { offering_id: 1 },
  }), '名额已满')],
  ['畸形字段错误安全回退', () => assert.equal(errorMessage({
    code: 'VALIDATION_ERROR', details: [{ field: 7, message: {} }],
  }), '操作失败，请刷新页面后重试；仍有问题时请联系管理员并提供请求编号')],
  ['收集全部必填字段而非只报告第一项', () => {
    const errors = personFormErrors({ ...base, username: '', name: '', birth_date: null, identity_number: '', status: '', department: '' }, 'professor', false, today);
    assert.equal(errors.length, 6);
    assert.equal(new Set(errors.map(item => item.field)).size, 6);
    for (const field of ['username', 'name', 'birth_date', 'identity_number', 'status', 'department']) assert.ok(errors.some(item => item.field === field));
  }],
  ['日期选择器清空不会抛出异常', () => {
    const errors = personFormErrors({ ...base, birth_date: null, graduation_date: null }, 'student', false, today);
    assert.equal(errors.length, 1);
    assert.equal(errors[0].field, 'birth_date');
    assert.match(errors[0].message, /必填/);
  }],
  ['多个独立错误一次性显示且每字段仅一条', () => {
    const errors = personFormErrors({ ...base, username: '坏账号', name: ' ', birth_date: '2030-01-01', identity_number: '1', status: ' ' }, 'student', false, today);
    assert.equal(errors.length, 5);
    assert.equal(new Set(errors.map(item => item.field)).size, 5);
  }],
  ['关联日期错误归到身份证字段', () => {
    const errors = personFormErrors({ ...base, birth_date: '1950-01-01' }, 'student', false, today);
    assert.equal(errors[0].field, 'identity_number');
    assert.match(errors[0].message, /第7～14位/);
  }],
  ['身份证长度提示包含当前长度且不回显号码', () => {
    const input = base.identity_number.slice(0, 15);
    const errors = personFormErrors({ ...base, identity_number: input }, 'student', false, today);
    assert.match(errors[0].message, /当前为15位/);
    assert.ok(!errors[0].message.includes(input));
  }],
  ['身份证格式说明区分全角与半角', () => validate({ identity_number: '１'.repeat(18) }, '半角')],
  ['姓名长度采用Unicode字符数量', () => {
    validate({ name: '𠮷'.repeat(80) }, null);
    validate({ name: '𠮷'.repeat(81) }, '当前为81个');
  }],
  ['账号允许纯数字学号', () => validate({ username: '11230521' }, null)],
  ['账号非法字符说明具体范围', () => validate({ username: '学生 01' }, '不能包含中文、空格')],
  ['任课资格数量错误指出实际门数', () => validate({ department: '计算机学院', qualification_ids: Array.from({ length: 101 }, (_, i) => i + 1) }, '当前选择101门', 'professor')],
  ['学生不能携带隐藏任课资格', () => validate({ qualification_ids: [1] }, '学生不能设置任课资格')],
  ['年份零不作为有效生日', () => validate({ birth_date: '0000-01-01' }, '有效日期')],
  ['任课资格异常结构有明确提示', () => validate({ qualification_ids: null }, '格式无效')],
  ['六项服务器错误不截断为四项', () => {
    const result = { code: 'VALIDATION_ERROR', details: ['username', 'name', 'status', 'birth_date', 'identity_number', 'department'].map(field => ({ field: `body.${field}`, message: 'Field required', type: 'missing' })) };
    assert.equal(validationIssues(result).length, 6);
    assert.ok(errorMessage(result).includes('所属院系：此项必填'));
  }],
  ['旧Pydantic错误定位到列表具体项', () => {
    const issues = validationIssues({ details: [{ loc: ['body', 'grades', 2, 'grade'], msg: "Input should be 'A', 'B', 'C', 'D', 'F' or 'I'", type: 'literal_error' }] });
    assert.equal(issues[0].field, 'grades.2.grade');
    assert.equal(issues[0].label, '成绩 · 第3项 · 成绩');
    assert.match(issues[0].message, /A、B、C、D、F、I/);
  }],
  ['去除查询参数前缀并翻译数值范围', () => {
    const issues = validationIssues({ details: [{ field: 'query.page_size', message: 'Input should be less than or equal to 100', type: 'less_than_equal' }] });
    assert.equal(issues[0].field, 'page_size');
    assert.equal(issues[0].label, '每页条数');
    assert.equal(issues[0].message, '必须小于或等于100');
  }],
  ['保留未知字段名称便于定位', () => {
    const issues = validationIssues({ details: [{ field: 'body.custom_field.0', message: 'Field required' }] });
    assert.equal(issues[0].label, 'custom_field · 第1项');
    assert.equal(issues[0].field, 'custom_field.0');
  }],
  ['跨字段模型错误显示为表单错误', () => {
    const issues = validationIssues({ details: [{ field: 'body', message: 'Value error, 结束时间必须晚于开始时间' }] });
    assert.equal(issues[0].field, '');
    assert.equal(issues[0].label, '表单');
    assert.equal(issues[0].message, '结束时间必须晚于开始时间');
  }],
  ['域错误也能定位重复账号字段', () => {
    const error = new ApiError('DUPLICATE', '账号重复', 409, { field: 'username', message: '此账号已被使用，请更换账号' }, 'request-example');
    assert.equal(validationIssues(error)[0].field, 'username');
    assert.equal(errorMessage(error), '账号：此账号已被使用，请更换账号');
    assert.equal(error.requestId, 'request-example');
  }],
  ['翻译日期解析错误而非笼统格式错误', () => assert.match(errorMessage({ details: [{ field: 'body.birth_date', message: 'Input should be a valid date or datetime, input is too short', type: 'date_from_datetime_parsing' }] }), /出生日期：请填写有效日期.*年-月-日/)],
  ['翻译整数错误', () => assert.match(errorMessage({ details: [{ field: 'path.student_id', message: 'Input should be a valid integer, unable to parse string as an integer', type: 'int_parsing' }] }), /学生编号：必须填写整数/)],
  ['翻译列表数量限制', () => assert.match(errorMessage({ details: [{ field: 'body.primary', message: 'List should have at most 4 items after validation, not 5', type: 'too_long' }] }), /首选课程：最多允许4项/)],
  ['翻译账号正则限制为人能读懂的说明', () => assert.match(errorMessage({ details: [{ field: 'body.username', message: "String should match pattern '^[A-Za-z0-9_.-]+$'", type: 'string_pattern_mismatch' }] }), /账号：只能使用英文字母、数字/)],
  ['不泄露Pydantic原始input或未知元数据', () => {
    const message = errorMessage({ details: [{ field: 'body.identity_number', message: 'String should have at most 18 characters', input: base.identity_number, secret: 'hidden-value', type: 'string_too_long' }] });
    assert.ok(!message.includes(base.identity_number));
    assert.ok(!message.includes('hidden-value'));
    assert.equal(message, '身份证号码：最多允许18个字符');
  }],
  ['重复字段与原因去重但保留不同原因', () => {
    const issues = validationIssues({ details: [{ field: 'name', message: '姓名不能为空' }, { field: 'name', message: '姓名不能为空' }, { field: 'name', message: '姓名请核对' }] });
    assert.equal(issues.length, 2);
  }],
  ['未知英文校验给出修正和排查办法', () => assert.match(errorMessage({ details: [{ field: 'name', message: 'Unknown constraint', type: 'custom' }] }), /姓名：.*联系管理员.*请求编号/)],
  ['业务详情仅包含元数据时不覆盖原因', () => assert.equal(errorMessage({ code: 'TIME_CONFLICT', message: '两门课程上课时间冲突，请调整课程', details: { first: 1, second: 2 } }), '两门课程上课时间冲突，请调整课程')],
  ['不把HTML字段错误解释为可执行内容', () => {
    const message = errorMessage({ details: [{ field: 'name', message: '<img src=x onerror=alert(1)>姓名无效' }] });
    assert.equal(message, '姓名：<img src=x onerror=alert(1)>姓名无效'); // Rendered by Vue text interpolation, never v-html.
  }],
  ['网络错误说明恢复后先核对结果', async () => {
    const client = loadTypescript('api.ts', { fetch: async () => { throw new Error('offline'); } });
    await assert.rejects(client.api('/registrar/people/student', 'POST', {}), error => error.code === 'NETWORK_ERROR' && /先刷新页面核对操作结果/.test(error.message));
  }],
  ['成功状态但响应不是JSON也不能误报成功', async () => {
    const client = loadTypescript('api.ts', { fetch: async () => ({ ok: true, status: 200, json: async () => { throw new Error('not JSON'); }, headers: { get: () => 'request-example' } }) });
    await assert.rejects(client.api('/registrar/people/student', 'POST', {}), error => error.code === 'INVALID_RESPONSE' && error.requestId === 'request-example');
  }],
  ['兼容默认FastAPI错误响应并保留请求编号', async () => {
    const client = loadTypescript('api.ts', { fetch: async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ loc: ['body', 'username'], type: 'string_too_short', msg: 'String should have at least 3 characters' }] }), headers: { get: () => 'request-example' } }) });
    await assert.rejects(client.api('/registrar/people/student', 'POST', {}), error => error.message === '账号：至少需要3个字符' && error.requestId === 'request-example');
  }],
  ['服务器故障在可见消息中显示请求编号', async () => {
    const client = loadTypescript('api.ts', { fetch: async () => ({ ok: false, status: 503, json: async () => ({ code: 'DATABASE_UNAVAILABLE', message: '数据库暂不可用，请稍后重试', request_id: 'database-request-example' }), headers: { get: () => 'fallback-request-example' } }) });
    await assert.rejects(client.api('/registrar/people/student', 'POST', {}), error => error.message.includes('请求编号：database-request-example') && error.requestId === 'database-request-example');
  }],
  ['标准字段校验错误不拼接排查编号', async () => {
    const client = loadTypescript('api.ts', { fetch: async () => ({ ok: false, status: 422, json: async () => ({ code: 'VALIDATION_ERROR', details: [{ field: 'username', message: '账号已经存在' }], request_id: 'request-example' }), headers: { get: () => 'request-example' } }) });
    await assert.rejects(client.api('/registrar/people/student', 'POST', {}), error => error.message === '账号：账号已经存在' && error.requestId === 'request-example');
  }],
];

(async () => {
  let passed = 0;
  for (const [name, run] of cases) {
    try {
      await run();
      passed += 1;
    } catch (error) {
      console.error(`失败：${name}`);
      throw error;
    }
  }
  console.log(`前端校验：${passed}/${cases.length} 项通过，0 项失败。`);
})().catch(error => { console.error(error); process.exitCode = 1; });
