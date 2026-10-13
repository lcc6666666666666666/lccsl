"""Container entry point: load file secrets, then drop privilege before serving."""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys

from cryptography.fernet import Fernet
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ROLES = {"bootstrap", "api", "catalog", "billing", "worker"}
ACCOUNTS = {"api": "course_app", "worker": "course_app", "catalog": "course_catalog", "billing": "course_billing"}
PASSWORD_FILES = {"api": "app_db_password", "worker": "app_db_password", "catalog": "catalog_db_password", "billing": "billing_db_password"}


class DeploymentError(RuntimeError):
    """An actionable message that contains no credentials or connection URL."""


def database_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", value) or value.lower() in {
        "mysql", "sys", "information_schema", "performance_schema", "test"
    }:
        raise DeploymentError("DB_NAME 必须是 1～48 位字母、数字或下划线，以字母开头，且不能使用系统数据库名。")
    return value


def read_secret(name: str, directory: Path = Path("/run/secrets")) -> str:
    try:
        value = (directory / name).read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError):
        raise DeploymentError(f"无法读取部署密钥文件 {name}；请检查 .runtime/docker/secrets 文件和挂载权限。") from None
    if not value or len(value) > 4096 or "\x00" in value:
        raise DeploymentError(f"部署密钥文件 {name} 为空或格式不正确，请恢复初始化脚本生成的原文件。")
    return value


def database_url(username: str, password: str, name: str | None) -> str:
    # URL.create escapes @, :, %, / and other valid password characters safely.
    return URL.create("mysql+pymysql", username=username, password=password, host="mysql", port=3306,
                      database=name, query={"charset": "utf8mb4"}).render_as_string(hide_password=False)


def configure_environment(role: str, secret_directory: Path = Path("/run/secrets")) -> None:
    if role not in ROLES:
        raise DeploymentError("未知容器角色；支持 bootstrap、api、catalog、billing、worker。")
    if (ROOT / ".env").exists():
        raise DeploymentError("容器中不能存在宿主机 .env；请使用项目 Dockerfile 和 .dockerignore 重新构建镜像。")
    if os.environ.get("DB_HOST", "mysql") != "mysql" or os.environ.get("DB_PORT", "3306") != "3306":
        raise DeploymentError("Docker 部署只允许连接 Compose 内的 mysql:3306，不允许连接宿主机数据库。")
    name = database_name(os.environ.get("DB_NAME", "course_registration"))
    encryption_key = read_secret("encryption_key", secret_directory)
    try:
        Fernet(encryption_key.encode("ascii"))
    except (ValueError, UnicodeError):
        raise DeploymentError("encryption_key 不是有效的 Fernet 密钥，请恢复原密钥文件，不要为已有数据库重新生成密钥。") from None
    integration_key = read_secret("integration_key", secret_directory)
    if len(integration_key) < 32:
        raise DeploymentError("integration_key 至少需要 32 个字符，请检查部署密钥文件。")
    os.environ.update({"DB_NAME": name, "ENCRYPTION_KEY": encryption_key, "INTEGRATION_KEY": integration_key,
                       "CATALOG_MODE": "http", "CATALOG_URL": "http://catalog:8001/catalog",
                       "BILLING_URL": "http://billing:8002/events", "DATABASE_SSL_CA": "",
                       "CATALOG_DATABASE_URL": "", "MOCK_STATE_DIR": "/tmp/mock-state"})
    for variable in ("MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD", "MYSQL_PWD"):
        os.environ.pop(variable, None)
    if role == "bootstrap":
        password = read_secret("mysql_root_password", secret_directory)
        username = "root"
    else:
        password = read_secret(PASSWORD_FILES[role], secret_directory)
        username = ACCOUNTS[role]
    os.environ["DATABASE_URL"] = database_url(username, password, name)


def role_command(role: str) -> list[str]:
    if role == "worker":
        return [sys.executable, "-m", "backend.app.worker"]
    targets = {"api": ("backend.app.main:app", "8000"), "catalog": ("backend.app.mocks:catalog_app", "8001"),
               "billing": ("backend.app.mocks:billing_app", "8002")}
    if role not in targets:
        raise DeploymentError("该容器角色没有独立服务启动命令。")
    target, port = targets[role]
    command = [sys.executable, "-m", "uvicorn", target, "--host", "0.0.0.0", "--port", port,
               "--no-proxy-headers", "--no-access-log"]
    if role == "api":
        command.extend(["--workers", "2"])
    return command


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    try:
        if len(arguments) != 1:
            raise DeploymentError("请为容器指定一个运行角色。")
        role = arguments[0]
        configure_environment(role)
        os.chdir(ROOT)
        if role == "bootstrap":
            from scripts.docker_init import main as initialize
            return initialize()
        # File-backed Compose secrets preserve host mode 0600. Read them as root,
        # then run all long-lived services as the image's dedicated unprivileged UID.
        if not hasattr(os, "geteuid"):
            raise DeploymentError("该入口仅用于 Linux Docker 容器，请在宿主机运行一键 Docker 部署脚本。")
        if os.geteuid() == 0:
            os.setgroups([])
            os.setgid(10001)
            os.setuid(10001)
        elif os.geteuid() != 10001:
            raise DeploymentError("应用容器必须使用镜像专用的 UID 10001 运行。")
        command = role_command(role)
        os.execvpe(command[0], command, os.environ.copy())
    except DeploymentError as exc:
        print(f"Docker 启动失败：{exc}", file=sys.stderr)
    except Exception as exc:
        # Never render driver exceptions: they may embed connection arguments.
        print(f"Docker 启动失败（{type(exc).__name__}）；请检查密钥挂载、容器权限和服务配置。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
