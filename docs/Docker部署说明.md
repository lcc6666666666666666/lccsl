# Docker 一键部署说明

## 1. 使用方法

Windows 先安装并启动 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，等待 Linux 容器引擎就绪。脚本负责项目所需的镜像、Python/Node 依赖及数据库初始化；Docker Desktop、虚拟化与 WSL 属于系统前置环境，需要预先安装。首次构建需要联网。

在项目根目录双击 **Docker一键部署.cmd**，成功后打开 **http://127.0.0.1:8080**。第一次构建需要下载 MySQL、Node 和 Python 基础镜像，所需时间取决于网络和硬件。

初始账号文件为 **`.runtime/docker/accounts/演示账号.txt`**，包含教务管理员、教授和学生的随机初始密码。首次登录必须修改密码。账号文件只保存在本地，不打印到构建日志，也不进入 Git 仓库。

停止时双击 **Docker停止.cmd**。再次启动仍然双击部署脚本，已有业务数据和账号密码保留。

命令行也可使用：

```powershell
# 在代码实现目录执行；默认访问端口 8080
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\docker_deploy.ps1
# 仅检测 Docker/Compose，不生成密钥或启动容器
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\docker_deploy.ps1 -CheckOnly
# 首次指定其他端口；后续默认沿用
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\docker_deploy.ps1 -Port 8088
# 停止并保留数据
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\docker_deploy.ps1 -Stop
```

Linux/macOS 已有可用的 Linux Docker 引擎与 Compose 后：

```bash
bash scripts/docker_deploy.sh
bash scripts/docker_deploy.sh --port 8088
bash scripts/docker_deploy.sh --check-only
bash scripts/docker_deploy.sh --stop
```

脚本可以从其他工作目录启动，项目文件位置根据脚本所在目录确定。请保留整个项目目录；默认只监听本机回环地址。

## 2. 自动部署的内容

| 服务 | 职责 | 启动条件 |
|---|---|---|
| mysql | 独立 MySQL 8.0，保存业务数据与模拟服务数据 | 数据库健康检查通过后才初始化 |
| init | 执行 Alembic 迁移、创建最小权限账户、生成演示数据 | 数据库就绪；成功后退出 |
| api | FastAPI 接口及编译后的 Vue 页面 | 初始化完成、目录服务健康 |
| catalog | 独立课程目录模拟服务 | 初始化完成 |
| billing | 接收选课通知与最终账单的模拟服务 | 初始化完成 |
| worker | 持续发送数据库待处理计费任务，失败后重试 | 初始化完成、计费服务健康 |

构建分两阶段：Node 22 根据 `frontend/package-lock.json` 执行 `npm ci` 并构建页面；Python 3.12 根据 `requirements.docker.lock` 安装 Linux 运行依赖。最终镜像不包含 Node、宿主机虚拟环境、开发测试依赖、宿主机 `.env`、本地数据库或演示密码文件。

`requirements.docker.lock` 与 Windows 开发用 `requirements.lock` 分开维护，避免将 `pywin32` 等 Windows 包安装到 Linux 容器。修改运行依赖时应同步更新两个锁文件，并重新检查 Linux 依赖解析与镜像构建。

Compose 使用健康依赖和初始化成功条件控制启动顺序，配置方式参见 [Docker 启动顺序文档](https://docs.docker.com/compose/how-tos/startup-order/)。

## 3. 初始化与重复运行规则

1. 检测 Docker 引擎和 Compose；缺少环境时明确说明安装或启动方式。
2. 首次部署生成六份随机密钥，包括数据库密码、身份信息加密密钥和服务间通信密钥。
3. 拉取镜像、安装依赖并构建前端。
4. 等待数据库可连接，取得初始化锁，执行数据库迁移。
5. 创建业务、只读目录、计费三个独立数据库账户并授予所需表级权限。
6. 仅在没有任何用户时生成演示数据。先持久化临时账号文件，再提交数据，最后发布正式账号文件；若在提交后中断，下次启动可校验并恢复账号文件。
7. 启动服务，检查 API 就绪状态，显示访问地址和账号文件路径。

再次部署不会清空数据库、重置密码或覆盖初始账号文件。已有用户但账号文件丢失时保留原密码，提示使用已有账号；不能由数据库密码哈希还原原始密码。数据库仍有数据而密钥缺失、密码失配或身份加密密钥不匹配时，脚本提示恢复原密钥，不会自动生成替代密钥。

空数据库遇到已有账号输出文件时会停止初始化，避免把旧账号文件与新数据库混用。请先检查数据卷是否丢失或 Compose 项目名称是否被修改。

## 4. 数据与权限

| 内容 | 位置或策略 |
|---|---|
| 默认数据库卷 | `course-registration_mysql_data` |
| 本地部署参数 | `.runtime/docker/compose.env`，只保存端口、路径和项目等非机密配置 |
| 数据库与应用密钥 | `.runtime/docker/secrets/` |
| 初始演示账号 | `.runtime/docker/accounts/演示账号.txt` |
| 服务日志 | Docker 日志，单文件最大 10 MB，最多保留 3 份 |
| 公开端口 | 仅 `127.0.0.1:8080`（可指定其他端口） |
| 数据库、目录、计费端口 | 仅容器网络内访问，不映射宿主机端口 |

Docker 部署使用独立数据库卷和密钥，不读取或修改现有本地部署 `.env` 与 MySQL 3307 数据。它也不会自动迁移本机已添加的数据。

宿主机密钥目录在 Windows 上限制为当前用户和 SYSTEM，在 Linux/macOS 上使用受限目录权限。密钥作为文件挂载至容器 `/run/secrets`，具体机制参见 [Compose Secrets 文档](https://docs.docker.com/compose/how-tos/use-secrets/)。应用入口读取本角色所需密钥后降权至 UID/GID 10001；数据库初始化进程在其容器内保留 root 完成一次性授权。Linux 账号输出文件按启动脚本的宿主用户编号设置所有者，避免用户无法读取。

停用脚本执行 `compose down`，不删除数据卷。**不要使用 `down -v` 清理正常业务环境，也不要删除或重新生成已有数据库对应的加密密钥。** 数据备份应同时包括数据库卷与 `.runtime/docker`，保存在受限位置；恢复时必须使用配套的原密钥。备份数据库文件前先停止所有容器，或使用 MySQL 一致性备份工具。

此配置面向本机课程演示。若改为公网部署，需要另行配置 HTTPS、允许的访问源和安全 Cookie；不能仅改成对外监听就视为完成公网部署。

## 5. 常见问题

| 提示或现象 | 处理方法 |
|---|---|
| 未检测到 Docker | 安装并启动 Docker Desktop；Linux 安装 Docker Engine 和 Compose 插件 |
| 引擎未启动或无访问权限 | 等待 Docker 就绪，用 `docker info` 检查；确认当前用户有权限 |
| 当前不是 Linux 容器引擎 | 在 Docker Desktop 切换到 Linux containers |
| 镜像或依赖下载失败 | 检查 Docker 的网络/代理配置后重试；之前生成的密钥会保留 |
| 端口已被占用 | 使用 `-Port 8088` 或 `--port 8088` 重新部署 |
| 初始化失败 | 查看 `init` 与 `mysql` 日志；不要清空数据卷来掩盖错误 |
| 密钥文件缺失或不匹配 | 从备份恢复原 `.runtime/docker/secrets`；已有数据不能配用新加密密钥 |
| 正常停止后再次启动 | 重新运行一键部署脚本，数据库与密码保持不变 |
| 计费服务暂时不可用 | 任务留在数据库，工作进程恢复连接后继续重试 |

在项目根目录查看状态及日志：

```powershell
docker compose --env-file .runtime/docker/compose.env -f compose.yaml ps -a
docker compose --env-file .runtime/docker/compose.env -f compose.yaml logs --tail 100 init api mysql catalog billing worker
```

管理员登录后可以使用“课程文件导入”，上传 JSON 文件作为外部课程目录数据源。文件原始对象及合并目录保存在 MySQL 数据卷中的 `file_catalog` 表，重启后继续有效；未导入时继续使用目录模拟服务。Docker 初始化器自动执行 `0003_file_catalog` 迁移并授予业务账户新表权限。格式及示例见 [课程文件导入说明](课程文件导入说明.md)。

## 6. 验证范围

本次已检查脚本语法、部署配置、Linux 运行依赖，并使用独立临时 MySQL 数据库验证迁移、初始数据、重复部署、提交后中断恢复和权限隔离。临时测试数据库及账户验证后删除，现有演示数据库未重置。具体结果见 [Docker 验证记录](Docker验证记录.md)。

当前开发电脑没有可用 Docker 引擎，因此不能将这些检查表述为完整容器部署成功。完整镜像构建、容器启动及持久卷重启验收仍需在安装 Docker 的环境运行本脚本验证。
