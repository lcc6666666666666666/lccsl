# 学程 · 中文课程注册系统

按《2026 年软件工程课程设计题目》及确认的实现方案开发。学生选课、教授任课与成绩、教务人员维护、结束选课和可靠计费均有可运行实现。

## Docker 一键部署（新电脑推荐）

先安装并启动 Docker Desktop，使用 Linux 容器，然后双击 **Docker一键部署.cmd**。脚本自动下载镜像、安装锁定的依赖、构建 Vue 页面、初始化独立 MySQL 8.0、生成随机演示账号并启动所有服务，无需在宿主机安装 Python、Node.js 或 MySQL。

- 访问 **http://127.0.0.1:8080**。
- 初始账号位于 **`.runtime/docker/accounts/演示账号.txt`**，首次登录须修改密码。
- 再次运行保留数据库、业务数据和密码；双击 **Docker停止.cmd** 停止容器并保留数据卷。
- Linux/macOS：`bash scripts/docker_deploy.sh`；停止：`bash scripts/docker_deploy.sh --stop`。
- Docker 数据独立于原本的本机运行环境。管理员可在“课程文件导入”上传外部 JSON 目录；未导入时使用目录模拟服务。

参数、故障处理、数据保存位置及验证范围见 [Docker 部署说明](docs/Docker部署说明.md)。

## 在当前电脑使用

管理员新增功能：进入 **课程文件导入**，选择学期与 [课程目录示例.json](数据导入/课程目录示例.json)，点击 **一键导入**。也可以先下载当前学期示例、编辑后导入，或仅校验预览。支持课程、教学班、先修关系和上课时间；同批有错全部不保存，重复数据跳过。详见 [课程文件导入说明](docs/课程文件导入说明.md)。

1. 双击 **启动系统.cmd**，或在 PowerShell 中运行 `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1`。
2. 打开 **http://127.0.0.1:8000**。
3. 用本地 `.runtime/演示账号.txt` 中的账号和随机密码登录。首次登录需要自己设置新密码（至少 12 个字符）。初始密码使用后不会继续有效。
4. 双击 **停止系统.cmd** 停止前后端、模拟服务和计费工作进程。项目私有 MySQL 保留运行，数据不会清除。

当前安装使用项目私有 MySQL 8.0 实例，端口 **3307**，数据位于 `.runtime/mysql-data`。它用于在无需现有服务管理员密码的情况下完成演示和验收；电脑原有的 **3306 服务及其数据库没有改动**。源码同时支持用初始化脚本连接已有 MySQL 服务。

| 角色 | 演示账号 | 可体验内容 |
|---|---|---|
| 学生 | student01 | 新建课表、草稿、4 首选 2 备选、成绩、账单 |
| 学生 | student03 | 历史成绩为 F，不能选择要求程序设计基础的课程 |
| 教授 | professor01～professor04 | 任课选择、名单；professor01 有上一学期成绩可修改 |
| 教务 | admin | 人员维护、任课资格、开放时间、结束选课、发送任务与审计 |

选课示例：首选“程序设计基础、离散数学、计算机导论、大学英语”；备选“线性代数、软件工程导论”。演示数据另有满员的“操作系统”、两人的“人机交互”、无教授的“软件测试”；“计算机网络”与“大学英语”时间冲突。

管理员在 **学生信息 / 教授信息** 中勾选 **显示已停用人员**，可对停用人员点击 **恢复账号**。恢复后使用原密码重新登录，首次登录须改密的要求保持不变；忘记密码可在恢复后重置。原有资料、任课资格、历史成绩和账单保留，停用时已释放的选课名额与任课安排需重新选择，旧登录会话不会恢复。

学生工作台的 **选课状态** 会显示每门课的注册待确认、最终开课成功、已取消及原因、已退课、备选未入选和草稿未提交状态。课表与费用账单只计入当前有效选课，费用页说明有效、取消和实际计费门数，并跟随顶部学期选择。页面每 5 秒同步结果，管理员结束选课后无需重新登录。

教授工作台的 **任课状态** 会显示认领成功待开班、任课成功、教学班取消原因和主动退出记录。被取消的教学班保留状态记录，但不再进入有效任课统计及授课时间表。未保存的勾选不会被当成认领成功。

结束选课会冻结该学期。管理员可在“学期与选课管理”点击 **重新开启选课**，设置开放时间后确认；仅限学期尚未结束且未录入成绩时使用。现有有效选课保留，已取消教学班恢复可选，被取消的选课由学生自行重新选择。原账单标记作废，再次结束选课后重新计算；计费通知支持重试与乱序到达，不会重复产生同一学生同一学期的账单。浏览器验收及压测使用另外的 `_test` 数据库，不会关闭演示学期。

**结束选课与结课是两个步骤。** 结束选课后，正常教学班显示“已确认开班”；教学完成时，管理员在 **学期与选课 → 对应学期 → 结束学期，开放成绩录入** 确认，教授即可进入该学期的 **名单与成绩** 保存成绩，无需等待计划结束日期。学生的成绩单同步支持该学期。结课后不能重新开启选课；最终名单、原成绩和账单保留，取消的教学班不能录入成绩。未手动结课的历史学期仍在计划结束日期过去且选课已结束后自动允许录入。教授已打开的名单每 5 秒检测录入是否开放，并保留尚未保存的成绩。

升级数据库（当前迁移 `0006_semester_completion`）：本机私有 MySQL 安装先停止应用，运行 `.\.venv\Scripts\python.exe scripts/migrate.py --isolated`，再构建和启动；已有外部 MySQL 使用 `scripts/migrate.py` 交互输入迁移凭据。Docker 再次运行一键部署脚本会自动迁移并保留业务数据。

## 换电脑初始化

需要 Windows、Python 3.12、MySQL 8.0 和联网下载依赖。全局 Node 无需修改：脚本使用 `.tools` 内校验 SHA-256 的 Node **22.23.3**；前后端依赖均锁定。

```powershell
cd D:\软件工程课设\代码实现
# 默认交互连接已有 MySQL：管理员密码不回显、不写入 .env。
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
# 或使用已安装的 mysqld.exe 建立项目私有 3307 实例。
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1 -Isolated
```

初始化只创建新的项目数据库、测试数据库和专用账户。遇到同名数据库或已有 `.env` 会停止初始化，不覆盖数据。指定新名称可用 `-Database course_registration_demo`。需要指定 mysqld 路径时，在执行前设置 `$env:MYSQLD_PATH`。

已初始化的项目直接启动即可。需要改接现有 MySQL 时，先备份 `.env` 与数据，再移走当前 `.env`，用**新的数据库名**初始化；不要把现有库删掉。初始化不会迁移旧演示数据。

## 开发、构建和测试

```powershell
# 开发页面 http://127.0.0.1:5173，后端 http://127.0.0.1:8000
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Development
# 构建后的页面由 FastAPI 同源提供；重建后停止再启动。
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
# 真实 MySQL 测试、覆盖率、前端类型检查
powershell -ExecutionPolicy Bypass -File .\scripts\test.ps1
# 已知依赖漏洞扫描
powershell -ExecutionPolicy Bypass -File .\scripts\audit.ps1
```

测试脚本检查数据库名称必须以 `_test` 结尾，**会清空该测试库中的业务表**，然后重新生成数据。不要同时运行单元/集成测试、浏览器验收和压测，它们共用测试库。

```powershell
# 独立验收环境：四个 API 服务，每个两个工作进程，共用测试 MySQL。
.\.venv\Scripts\python.exe .\scripts\qa_environment.py start
.\.venv\Scripts\python.exe .\scripts\load_test.py --users 500 --rounds 2
.\.venv\Scripts\python.exe .\scripts\load_test.py --users 2000 --rounds 2
# Edge 无头浏览器三角色流程，会结束测试学期；应放在压测后运行。
.\.venv\Scripts\python.exe .\scripts\browser_test.py
.\.venv\Scripts\python.exe .\scripts\live_integration_test.py
.\.venv\Scripts\python.exe .\scripts\qa_environment.py stop
```

`qa_environment.py start` 会重置测试库；验收页面为 `http://127.0.0.1:8100`，验收凭据只存在 `.runtime/qa-credentials.json`。Edge 使用本机安装版本，不接管日常浏览器配置。详细结果见 [验收记录](docs/验收记录.md)、`test-results/pytest.xml`、`test-results/coverage/index.html` 和 `test-results/browser/`。

压测默认先用 10 秒让用户进入工作台，再同步发起操作，测量目录、本人课表和草稿保存。预先创建独立会话，不把密码哈希计入压测；这不是登录洪峰或长期稳定性测试。极端新连接洪峰的未通过结果同样保留在验收记录中。

## 源码结构

```text
backend/app/        FastAPI 接口、会话、事务服务、SQLAlchemy 模型、外部适配与工作进程
backend/migrations/ Alembic 数据库迁移
frontend/src/       Vue 3 + TypeScript + Router + Pinia + Element Plus
scripts/            初始化、启停、构建、审计、浏览器与压测脚本
tests/              真实 MySQL 的功能、权限、并发、回滚和集成测试
docs/               设计说明、PDF 对照表、运维与验收记录
requirements.lock   Python 精确版本依赖
frontend/package-lock.json  前端锁文件
.env                仅本机的数据库凭据、加密及集成密钥（禁止提交）
.runtime/           本机数据库、日志、临时文件、随机演示账号（禁止提交）
```

开发接口文档为 `/api/docs`，OpenAPI JSON 为 `/api/v1/openapi.json`，健康检查为 `/api/v1/health`。Swagger 页面使用官方默认 CDN 资源，需要联网；JSON 文档和系统本身可离线使用。

业务规则、权限与事务说明见 [设计说明](docs/设计说明.md)，逐条要求见 [PDF 要求对照](docs/PDF要求对照.md)，备份恢复、外部系统接入与故障演示见 [运维说明](docs/运维说明.md)。安全结论以已执行的测试和扫描为限，不作“绝对零漏洞”承诺。
