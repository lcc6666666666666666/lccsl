"""One-shot, repeatable initialization of the dedicated Compose MySQL database."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from scripts.docker_entrypoint import ROOT, DeploymentError, database_name, database_url, read_secret

EXTERNAL_TABLES = {"legacy_catalog", "billing_receipts", "billing_invoices"}
DATABASE_ACCOUNTS = {"app": ("course_app", "app_db_password"), "catalog": ("course_catalog", "catalog_db_password"),
                     "billing": ("course_billing", "billing_db_password")}
FINAL_FILENAME = "演示账号.txt"
PENDING_FILENAME = ".bootstrap-credentials.pending.json"


def output_owner() -> tuple[int, int]:
    values = []
    for variable in ("BOOTSTRAP_OUTPUT_UID", "BOOTSTRAP_OUTPUT_GID"):
        value = os.environ.get(variable, "0")
        if not re.fullmatch(r"0|[1-9][0-9]{0,9}", value) or int(value) > 4294967294:
            raise DeploymentError(f"{variable} 必须是有效的非负 Linux 用户或组编号。")
        values.append(int(value))
    return values[0], values[1]


def private_write_once(path: Path, content: str) -> None:
    """Publish a complete 0600 file atomically, refusing to replace any old file."""
    uid, gid = output_owner()
    fd, temporary = tempfile.mkstemp(prefix=".bootstrap-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            os.chmod(temporary_path, 0o600)
            if hasattr(os, "fchown"):
                # The init process is root; the persisted file must remain readable
                # by the user who owns the Linux host deployment directory.
                os.fchown(stream.fileno(), uid, gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # link(2) publishes without the overwrite race of an existence check+rename.
        os.link(temporary_path, path)
        sync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def sync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_output(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, probe = tempfile.mkstemp(prefix=".write-check-", dir=directory)
    os.close(fd)
    Path(probe).unlink()


def account_document(credentials: list[dict]) -> str:
    roles = {"registrar": "教务管理员", "professor": "教授", "student": "学生"}
    lines = ["课程注册系统 Docker 演示账号", "首次登录需要修改初始密码。请妥善保存本文件，不要上传 Git 仓库。",
             "以下数据为系统生成的演示数据；重复部署不会重置密码或覆盖本文件。", ""]
    for entry in credentials:
        lines.append(f"{roles.get(entry['role'], entry['role'])}  {entry['name']}  账号：{entry['username']}  初始密码：{entry['password']}")
    return "\n".join(lines) + "\n"


def grant_statements(name: str, table_names: list[str]) -> list[str]:
    name = database_name(name)
    for table in table_names:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise DeploymentError("数据模型存在不受支持的表名，初始化已停止。")
    if not EXTERNAL_TABLES.issubset(table_names):
        raise DeploymentError("数据模型缺少外部目录或计费表，请使用完整项目镜像。")
    app, catalog, billing = (DATABASE_ACCOUNTS[role][0] for role in ("app", "catalog", "billing"))
    for username in (app, catalog, billing):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", username):
            raise DeploymentError("容器数据库账号名称不符合要求，初始化已停止。")
    statements = [f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{name}`.`{table}` TO '{app}'@'%'"
                  for table in sorted(set(table_names) - EXTERNAL_TABLES)]
    statements.append(f"GRANT SELECT ON `{name}`.`legacy_catalog` TO '{catalog}'@'%'")
    statements.extend(f"GRANT SELECT, INSERT, UPDATE ON `{name}`.`{table}` TO '{billing}'@'%'"
                      for table in ("billing_receipts", "billing_invoices"))
    return statements


def wait_mysql(engine, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise DeploymentError("120 秒内未能连接容器 MySQL，请检查 mysql 服务健康状态和原 root 密钥文件。") from None
            time.sleep(min(2, max(0, deadline - time.monotonic())))


def make_engine(url: str):
    return create_engine(url, hide_parameters=True, pool_pre_ping=True,
                         connect_args={"connect_timeout": 5, "read_timeout": 30, "write_timeout": 30})


def provision_accounts(connection, name: str, table_names: list[str], directory: Path) -> None:
    statements = grant_statements(name, table_names)
    accounts = tuple(DATABASE_ACCOUNTS.values())
    for username, filename in accounts:
        password = read_secret(filename, directory)
        connection.execute(text(f"CREATE USER IF NOT EXISTS '{username}'@'%' IDENTIFIED BY :password"), {"password": password})
        # IF NOT EXISTS intentionally never changes an existing account password.
        verifier = make_engine(database_url(username, password, None))
        try:
            with verifier.connect() as probe:
                probe.exec_driver_sql("SELECT 1")
        except OperationalError:
            raise DeploymentError(f"数据库账号 {username} 与部署密钥不一致；请恢复原 {filename} 文件。脚本不会修改旧账号密码。") from None
        finally:
            verifier.dispose()
    # These accounts belong only to this dedicated Compose MySQL instance.
    # Reset grants on redeployment so removed tables/old broad privileges cannot linger.
    for username, _ in accounts:
        connection.exec_driver_sql(f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM '{username}'@'%%'")
    for statement in statements:
        connection.execute(text(statement))


def recover_pending(db, directory: Path, users) -> None:
    from backend.app.security import password_hasher
    pending = directory / PENDING_FILENAME
    if not pending.is_file():
        raise DeploymentError("数据库已初始化，但演示账号文件缺失。请恢复原演示账号文件，或用已有管理员账号重置所需密码；脚本不会重置数据库。")
    try:
        credentials = json.loads(pending.read_text(encoding="utf-8"))
        if not isinstance(credentials, list) or len(credentials) != len(users):
            raise ValueError("shape")
        by_name = {user.username: user for user in users}
        if len({entry["username"] for entry in credentials}) != len(by_name):
            raise ValueError("duplicates")
        for entry in credentials:
            user = by_name[entry["username"]]
            if not password_hasher.verify(entry["password"], user.password_hash):
                raise ValueError("password")
    except Exception:
        raise DeploymentError("上次初始化的暂存账号文件与数据库不匹配。请恢复原账号文件或使用已有管理员账号；不会重置密码。") from None
    private_write_once(directory / FINAL_FILENAME, account_document(credentials))
    pending.unlink()
    sync_directory(directory)


def seed_safely(engine, directory: Path) -> bool:
    from backend.app.models import User
    from backend.app.seed import seed_demo
    pending, final = directory / PENDING_FILENAME, directory / FINAL_FILENAME
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            # seed_demo.commit releases a savepoint, not the outer transaction.
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                users = list(db.scalars(select(User).order_by(User.id)))
                if users:
                    from backend.app.security import decrypt_identity
                    try:
                        for user in users:
                            if user.identity_cipher:
                                decrypt_identity(user.identity_cipher)
                    except Exception:
                        raise DeploymentError("当前 encryption_key 无法解密已有身份信息，请恢复原密钥文件；初始化不会替换原密钥或改写身份数据。") from None
                    if not final.is_file() and pending.is_file():
                        recover_pending(db, directory, users)
                    elif not final.is_file():
                        print("数据库已有用户，但本机没有初始演示账号文件；保留现有密码，请使用已有账号登录，或由已有管理员重置所需账号密码。")
                    elif pending.exists():
                        # Final file was already published before an interrupted cleanup.
                        pending.unlink()
                    db.close()
                    transaction.rollback()
                    return False
                if final.exists():
                    raise DeploymentError("数据库为空，但输出目录已有演示账号文件。请确认是否误换数据库卷；为防覆盖旧凭据，初始化已停止。")
                if pending.exists():
                    # The durable pending record preceded a rolled-back seed transaction.
                    # No user exists, so no password was ever activated.
                    pending.unlink()
                credentials = seed_demo(db)
                if not credentials:
                    raise DeploymentError("初始化期间用户数据发生变化，请重试。")
                private_write_once(pending, json.dumps(credentials, ensure_ascii=False, indent=2) + "\n")
            transaction.commit()
        except BaseException:
            if transaction.is_active:
                transaction.rollback()
            raise
    # Pending survives any interruption after commit and can recover the next run.
    private_write_once(final, account_document(credentials))
    pending.unlink()
    sync_directory(directory)
    return True


def initialize(secret_directory: Path = Path("/run/secrets"), output_directory: Path = Path("/bootstrap-output")) -> bool:
    name = database_name(os.environ.get("DB_NAME", "course_registration"))
    prepare_output(output_directory)
    password = read_secret("mysql_root_password", secret_directory)
    root_engine = make_engine(database_url("root", password, None))
    wait_mysql(root_engine)
    lock_name = "course-bootstrap-" + hashlib.sha256(name.encode()).hexdigest()[:40]
    try:
        with root_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as administration:
            acquired = administration.scalar(text("SELECT GET_LOCK(:name, 30)"), {"name": lock_name})
            if acquired != 1:
                raise DeploymentError("另一个初始化进程正在运行；请等待其完成后重试。")
            try:
                administration.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
                os.environ["DATABASE_URL"] = database_url("root", password, name)
                from alembic import command
                from alembic.config import Config
                from backend.app.db import Base, engine
                from backend.app import models  # noqa: F401: registers the complete schema
                command.upgrade(Config(str(ROOT / "backend" / "alembic.ini")), "head")
                provision_accounts(administration, name, list(Base.metadata.tables), secret_directory)
                return seed_safely(engine, output_directory)
            finally:
                administration.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock_name})
    finally:
        root_engine.dispose()


def main() -> int:
    try:
        created = initialize()
        print("Docker 初始化完成：已生成数据库、最小权限账号和演示数据，演示密码已保存到宿主机输出目录。" if created
              else "Docker 初始化检查完成：数据库结构已更新，保留现有业务数据、密码和演示账号文件。")
        return 0
    except DeploymentError as exc:
        print(f"Docker 初始化失败：{exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Docker 初始化失败（{type(exc).__name__}）。请检查容器数据库、密钥及输出目录权限；不会自动清空数据或重置密码。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
