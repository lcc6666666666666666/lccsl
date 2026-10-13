# PDF 要求—实现—验收对照

依据原始英文 PDF 的页码，结合已经确认的业务解释。`tests/` 中的测试直接使用 MySQL；界面流程在 `scripts/browser_test.py` 中验证。性能、长期运行和真实外部系统边界单独记录，不能由功能测试代替。

| PDF 页码 / 要求 | 实现位置与行为 | 自动化验收 |
|---|---|---|
| 2、4：多人并发与容量 10 | 学期/账户/课表/教学班锁、数据库容量约束 | `test_race_for_last_seat`、`test_same_schedule_concurrent_*` |
| 2、13：课程、教授、院系、先修、时间查询 | `/catalog/offerings`；中文目录、搜索分页、余量 | `test_catalog_pagination_search_and_report`；学生浏览器流程 |
| 2、4：构建课表过程中满员提示 | 每 5 秒刷新余量，提交前再次取目录，事务内最终核验 | 学生页面刷新逻辑；最后名额并发测试 |
| 4：学生只能改本人课表、看本人成绩 | 本人 API 使用会话身份，禁止请求体附加他人编号 | `test_cannot_choose_another_student`、`test_role_and_object_authorization` |
| 4：教授不能修改他人任课，成绩只允许教授录入 | 服务器角色检查与教学班归属检查 | `test_role_and_object_authorization`、`test_teaching_eligibility_conflicts_and_ownership` |
| 4：只有教务维护学生信息 | 全部 `/registrar/*` 接口校验教务角色 | `test_role_and_object_authorization` |
| 4：单实例 500 / 中央库 2000、80% 两分钟内、目录 10 秒 | 可复跑的 500、2000 用户压测；目录过期缓存、7.5 秒 HTTP 总期限 | `scripts/load_test.py`、`test_catalog_timeout_total_deadline`；实际数值见验收记录 |
| 4：24×7、不超过 10% 停机 | 健康检查、持久队列、备份与恢复说明 | 短时测试不证明长期可用率，需部署后持续监控 |
| 4、5：Windows 95/98 桌面 / 旧目录数据库 | 按已确认方案改为现代 Windows 浏览器；提供只读 SQL 适配接口 | Windows Edge 浏览器验收；真实 Ingres 驱动待真实环境配置 |
| 7：结束操作有选课进行时拒绝 | MySQL 学期排他 NOWAIT 锁 | `test_close_rejects_in_progress_immediately`、`test_close_blocks_new_submission_and_rolls_back` |
| 7：没有教授取消、按备选补位、少于 3 人取消 | 先取消无教授班，单轮补位，再取消不足 3 人班 | `test_close_cancellation_promotion_billing_idempotency`、`test_alternates_check_prerequisites_conflicts_capacity_order_single_pass` |
| 7：学费和最终课表通知计费；故障持续重发 | 唯一账单 + 同事务 outbox，工作进程租约和持续退避 | `test_outbox_retry_restart_and_duplicate_delivery`、`test_invoice_unique_across_different_events_and_payload_mismatch`、`test_worker_continues_retrying_with_capped_interval` |
| 7：失败系统不变，成功关闭注册 | 一个 MySQL 事务，失败回滚；关闭后禁止选课任课 | `test_close_failure_is_all_or_nothing`、关闭成功测试、浏览器最终课表流程 |
| 8：登录成功/错误、取消/重试 | 中文登录页、通用错误、会话、限流与退出 | `test_auth_csrf_origin_validation_and_logout`、`test_login_throttle_and_expiry`、浏览器重新登录 |
| 9～10：教授新增、生成唯一编号、查询、修改、删除 | 教务教授表单；姓名、生日、身份号、状态、院系、资格；数据库生成编号 | `test_registrar_crud_encrypted_identity[professor]` |
| 9～10：教授不存在、取消删除、不成功无变化 | 不存在报错；停用前确认；事务与版本校验 | `test_deactivation_releases_open_keeps_history`、CRUD 版本冲突测试；共享确认组件 |
| 11～12：学生新增、编号、查询、修改、删除 | 教务学生表单；姓名、生日、身份号、状态、毕业日期 | `test_registrar_crud_encrypted_identity[student]`；浏览器创建/修改/停用 |
| 11～12：学生不存在、取消删除、不成功无变化 | 不存在报错、取消不发送写请求、失败回滚 | `test_deactivation_releases_open_keeps_history`；浏览器取消停用 |
| 13：创建 4 首选 2 备选、修改与删除 | 首次严格 4+2；调整按确认规则 1～4/0～2；删除释放座位 | `test_draft_submit_update_delete_atomic`、`test_invalid_submissions` |
| 14：先修、开放状态、时间、人数检查 | 提交前校验并在事务内检查最终容量 | `test_prerequisite_grades`、`test_invalid_submissions`、`test_adjacent_times_and_nonoverlapping_weeks_are_valid` |
| 14：仅保存为 selected | 草稿 JSON 与正式 Enrollment 分离，不占新座位 | `test_draft_submit_update_delete_atomic`；浏览器草稿保存并刷新 |
| 14：失败后换课/保存/取消、不存在课表、删除取消 | 失败保留原课表；前端可继续调整；删除无课表返回错误；确认框取消无写入 | 选课原子性测试；浏览器冲突重试与取消删除 |
| 14：目录不可用终止、关闭选课后禁止操作 | 缓存过期故障返回 503；检查学期状态和开放时间 | `test_catalog_unavailable_aborts_student_and_professor`、`test_catalog_cache_expiry_outage_and_recovery`、`test_registration_dates_and_readonly_after_close` |
| 15：选课用例前置登录、失败不改变原数据 | 统一身份依赖；同一业务事务 | 权限测试、选课失败回滚测试 |
| 16～17：有资格的课程、已有任课、选择/取消、冲突指出教学班 | 资格列表、任课原集合校验、时间检查及冲突教学班代码 | `test_teaching_eligibility_conflicts_and_ownership`、`test_concurrent_professor_claim`；教授浏览器流程 |
| 16：无课程、目录故障、选课关闭 | 空列表提示、503 提示、关闭学期只读 | 教授资格与空历史列表测试、目录故障测试、关闭测试 |
| 18：上一学期名单、A/B/C/D/F/I、空白、修改、无任课 | 本人已结束教学班名单、批量成绩与版本校验、无任课空态 | `test_grades_allowed_null_version_and_atomicity`、教授浏览器录入流程 |
| 19：本人上一学期成绩、无成绩提示、只读 | 默认最近结束学期；可切换；空数据提示 | `test_catalog_pagination_search_and_report`、`test_cannot_choose_another_student`；学生成绩界面 |

补充安全与可靠性测试覆盖身份号码加密/脱敏、密码哈希与会话撤销、初始密码强制更新、CSRF/Origin、过期会话、参数白名单、幂等键内容冲突、成绩批量回滚、重复事件和唯一正式账单。功能测试不等同于安全认证；验收记录注明实际扫描范围。

## 2026-10-13 补充核对

- 人员身份号码按中文系统约定增加 18 位格式、日期、校验码、生日一致性和大小写归一化检查。原题字段为 Social Security Number，18 位规则是本地化约定。新增与修改、旧数据保留、查重及失败回滚见 `tests/test_person_validation.py`。
- 第 14 页草稿允许保留满员或先修不足的选课意向，正式提交仍须通过校验；前端已修正，并由浏览器分别验证保存和提交。已正式注册而保留的教学班不重新检查先修，退课后再次选择必须检查，见 `tests/test_requirement_gaps.py`。
- 特殊字符搜索、CSRF 及异常请求头、带时区时间窗口和错误响应头增加回归测试，见 `tests/test_request_boundaries.py`、`tests/test_mock_input_validation.py`。
- 已增加管理员外部 JSON 文件导入，覆盖课程、教学班、先修关系与时间结构，支持整批校验、事务回滚及重复导入跳过（`tests/test_catalog_import.py`）。文件原始对象和合并目录持久保存，注册查询只读文件目录快照。真实 Ingres 未联调，不能将本地文件导入表述为真实 Ingres 对接验收。
- 本次问题明细、108 项后端用例、22 项前端校验和浏览器/真实进程故障验证结果见 [需求核对与修复记录](需求核对与修复记录-2026-10-13.md)。
