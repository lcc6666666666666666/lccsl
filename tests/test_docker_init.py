"""Container setup checks without DB/server access: pytest --noconftest this_file."""
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import docker_entrypoint as entry
from scripts import docker_init as init


@pytest.mark.parametrize("name", ["mysql", "MYSQL", "information_schema", "performance_schema", "sys", "test", "9db", "a-b", "a`;DROP", "a" * 49, ""])
def test_reject_unsafe_database_name(name):
    with pytest.raises(entry.DeploymentError):
        entry.database_name(name)


def test_url_escapes_password_without_modification():
    password = "arbitrary@:/%?chars'特殊"
    url = make_url(entry.database_url("course_app", password, "course_registration"))
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "course_app", password, "mysql", 3306, "course_registration")


def secrets_for(tmp_path, role):
    directory = tmp_path / "secrets"
    directory.mkdir()
    entries = {"encryption_key": Fernet.generate_key().decode(), "integration_key": "i" * 48}
    entries[entry.PASSWORD_FILES.get(role, "mysql_root_password")] = "random@password:example"
    for key, value in entries.items():
        (directory / key).write_text(value + "\n", encoding="utf-8")
    return directory, entries


@pytest.mark.parametrize("role,username", [("api", "course_app"), ("worker", "course_app"), ("catalog", "course_catalog"), ("billing", "course_billing"), ("bootstrap", "root")])
def test_role_loads_only_its_secret_and_no_host_env(monkeypatch, tmp_path, role, username):
    directory, entries = secrets_for(tmp_path, role)
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    monkeypatch.setattr(os, "environ", {"DB_NAME": "course_registration", "MYSQL_ROOT_PASSWORD": "host-do-not-use"})
    entry.configure_environment(role, directory)
    url = make_url(os.environ["DATABASE_URL"])
    assert url.username == username
    assert url.password == "random@password:example"
    assert os.environ["ENCRYPTION_KEY"] == entries["encryption_key"]
    assert os.environ["CATALOG_MODE"] == "http"
    assert os.environ["CATALOG_DATABASE_URL"] == ""
    assert "MYSQL_ROOT_PASSWORD" not in os.environ
    if role != "bootstrap":
        assert not (directory / "mysql_root_password").exists()


@pytest.mark.parametrize("host,port", [("127.0.0.1", "3306"), ("mysql", "3307"), ("localhost", "3307")])
def test_host_database_is_never_selected(monkeypatch, tmp_path, host, port):
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    monkeypatch.setattr(os, "environ", {"DB_HOST": host, "DB_PORT": port})
    with pytest.raises(entry.DeploymentError, match="不允许连接宿主机"):
        entry.configure_environment("api", tmp_path)


def test_image_with_host_env_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(entry, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("DATABASE_URL=never-read-this", encoding="utf-8")
    with pytest.raises(entry.DeploymentError, match="宿主机 .env"):
        entry.configure_environment("api", tmp_path)


@pytest.mark.parametrize("value", ["", "\x00bad", "a" * 4097])
def test_malformed_secret_is_not_echoed(tmp_path, value):
    (tmp_path / "app_db_password").write_text(value, encoding="utf-8")
    with pytest.raises(entry.DeploymentError, match="为空或格式不正确") as caught:
        entry.read_secret("app_db_password", tmp_path)
    if value:
        assert value not in str(caught.value)


def test_exact_minimum_table_grants():
    statements = init.grant_statements("course_registration", ["users", "schedules", "legacy_catalog", "billing_receipts", "billing_invoices"])
    assert len(statements) == 5
    app = [line for line in statements if "course_app" in line]
    catalog = [line for line in statements if "course_catalog" in line]
    billing = [line for line in statements if "course_billing" in line]
    assert len(app) == 2 and all("legacy_catalog" not in line and "billing_" not in line for line in app)
    assert len(catalog) == 1 and catalog[0].startswith("GRANT SELECT ON ")
    assert len(billing) == 2 and all(line.startswith("GRANT SELECT, INSERT, UPDATE ON ") for line in billing)
    assert not any("ALL" in line or "WITH GRANT" in line or ".*" in line for line in statements)


def test_file_publication_never_overwrites_existing_file(tmp_path):
    path = tmp_path / "accounts.txt"
    init.private_write_once(path, "initial-private-content")
    with pytest.raises(FileExistsError):
        init.private_write_once(path, "new-content")
    assert path.read_text(encoding="utf-8") == "initial-private-content"
    assert list(tmp_path.iterdir()) == [path]
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_service_command_has_no_secrets_and_no_proxy_header_trust():
    for role in ("api", "catalog", "billing", "worker"):
        command = entry.role_command(role)
        assert "DATABASE_URL" not in " ".join(command)
        assert "password" not in " ".join(command)
        if role != "worker":
            assert "--no-proxy-headers" in command
    assert entry.role_command("api")[-2:] == ["--workers", "2"]


def test_unexpected_error_does_not_print_connection_string(monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError("mysql+pymysql://root:secret-at-risk@mysql")
    monkeypatch.setattr(entry, "configure_environment", fail)
    assert entry.main(["api"]) == 1
    output = capsys.readouterr().err
    assert "RuntimeError" in output and "secret-at-risk" not in output and "mysql+pymysql" not in output


CREDENTIALS = [{"username": "admin", "password": "random-example-password", "name": "教务管理员", "role": "registrar"}]


class FakeTransaction:
    is_active = True
    committed = False
    rolled_back = False

    def __init__(self, directory):
        self.directory = directory

    def commit(self):
        # The real seed's inner commit must not become durable before this file.
        assert json.loads((self.directory / init.PENDING_FILENAME).read_text(encoding="utf-8")) == CREDENTIALS
        assert not (self.directory / init.FINAL_FILENAME).exists()
        self.committed, self.is_active = True, False

    def rollback(self):
        self.rolled_back, self.is_active = True, False


def mock_seed_environment(monkeypatch, tmp_path, users=()):
    transaction = FakeTransaction(tmp_path)
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def begin(self): return transaction
    class FakeSession:
        def __init__(self, **kwargs):
            assert kwargs["join_transaction_mode"] == "create_savepoint"
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def scalars(self, query): return list(users)
        def close(self): pass
    modules = {name: ModuleType(name) for name in ("backend.app.models", "backend.app.seed", "backend.app.security")}
    modules["backend.app.models"].User = SimpleNamespace(id="id")
    modules["backend.app.seed"].seed_demo = lambda db: CREDENTIALS
    modules["backend.app.security"].password_hasher = SimpleNamespace(verify=lambda password, stored: password == stored)
    modules["backend.app.security"].decrypt_identity = lambda cipher: "verified"
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(init, "Session", FakeSession)
    monkeypatch.setattr(init, "select", lambda obj: SimpleNamespace(order_by=lambda field: None))
    return SimpleNamespace(connect=lambda: Connection()), transaction


def test_seed_durable_credentials_before_commit(monkeypatch, tmp_path):
    engine, transaction = mock_seed_environment(monkeypatch, tmp_path)
    assert init.seed_safely(engine, tmp_path)
    assert transaction.committed and not transaction.rolled_back
    assert "random-example-password" in (tmp_path / init.FINAL_FILENAME).read_text(encoding="utf-8")
    assert not (tmp_path / init.PENDING_FILENAME).exists()


def test_failure_to_save_credentials_rolls_back_seed(monkeypatch, tmp_path):
    engine, transaction = mock_seed_environment(monkeypatch, tmp_path)
    def fail(*args): raise PermissionError("read only")
    monkeypatch.setattr(init, "private_write_once", fail)
    with pytest.raises(PermissionError):
        init.seed_safely(engine, tmp_path)
    assert transaction.rolled_back and not transaction.committed
    assert not (tmp_path / init.FINAL_FILENAME).exists()


def test_interrupt_after_commit_recovers_pending_credentials(monkeypatch, tmp_path):
    engine, transaction = mock_seed_environment(monkeypatch, tmp_path)
    original_write = init.private_write_once
    def fail_final(path, content):
        if path.name == init.FINAL_FILENAME:
            raise PermissionError("write interrupted")
        return original_write(path, content)
    monkeypatch.setattr(init, "private_write_once", fail_final)
    with pytest.raises(PermissionError):
        init.seed_safely(engine, tmp_path)
    assert transaction.committed and (tmp_path / init.PENDING_FILENAME).is_file()
    monkeypatch.setattr(init, "private_write_once", original_write)
    users = [SimpleNamespace(id=1, username="admin", password_hash=CREDENTIALS[0]["password"], identity_cipher="cipher")]
    recovered_engine, recovered_transaction = mock_seed_environment(monkeypatch, tmp_path, users)
    assert not init.seed_safely(recovered_engine, tmp_path)
    assert (tmp_path / init.FINAL_FILENAME).is_file()
    assert not (tmp_path / init.PENDING_FILENAME).exists()
    assert not recovered_transaction.committed


def test_existing_database_without_credentials_is_never_reset(monkeypatch, tmp_path, capsys):
    users = [SimpleNamespace(id=1, username="admin", password_hash="changed", identity_cipher="cipher")]
    engine, transaction = mock_seed_environment(monkeypatch, tmp_path, users)
    assert not init.seed_safely(engine, tmp_path)
    assert "请使用已有账号登录" in capsys.readouterr().out
    assert not transaction.committed and transaction.rolled_back
    assert not (tmp_path / init.FINAL_FILENAME).exists()


def test_repeat_deploy_preserves_changed_password_and_account_file(monkeypatch, tmp_path):
    users = [SimpleNamespace(id=1, username="admin", password_hash="already-changed", identity_cipher="cipher")]
    engine, transaction = mock_seed_environment(monkeypatch, tmp_path, users)
    original = "Original initial password record; actual password changed later."
    (tmp_path / init.FINAL_FILENAME).write_text(original, encoding="utf-8")
    assert not init.seed_safely(engine, tmp_path)
    assert (tmp_path / init.FINAL_FILENAME).read_text(encoding="utf-8") == original
    assert not transaction.committed


def test_empty_database_with_old_account_file_requires_recovery(monkeypatch, tmp_path):
    engine, transaction = mock_seed_environment(monkeypatch, tmp_path)
    (tmp_path / init.FINAL_FILENAME).write_text("old-data", encoding="utf-8")
    with pytest.raises(entry.DeploymentError, match="数据库为空"):
        init.seed_safely(engine, tmp_path)
    assert not transaction.committed


def test_private_output_belongs_to_host_user(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_OUTPUT_UID", "1000")
    monkeypatch.setenv("BOOTSTRAP_OUTPUT_GID", "1001")
    calls = []
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: calls.append((uid, gid)), raising=False)
    init.private_write_once(tmp_path / "private.txt", "private-content")
    assert calls == [(1000, 1001)]


@pytest.mark.parametrize("value", ["-1", "1000;id", "０", "4294967295", "", "1.5"])
def test_invalid_output_owner_rejected_before_writing(monkeypatch, tmp_path, value):
    monkeypatch.setenv("BOOTSTRAP_OUTPUT_UID", value)
    with pytest.raises(entry.DeploymentError, match="有效的非负 Linux"):
        init.private_write_once(tmp_path / "private.txt", "must-not-write")
    assert list(tmp_path.iterdir()) == []
