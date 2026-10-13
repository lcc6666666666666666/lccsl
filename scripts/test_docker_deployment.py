"""Docker deployment checks without starting Docker or connecting to MySQL.

Run: python scripts/test_docker_deployment.py
Compose checks require Docker Compose (or DOCKER_COMPOSE_BINARY pointing to the
official standalone binary). Missing Compose is reported as skipped, not passed.
Building and starting actual containers remains a separate integration check.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import docker_healthcheck as health


class ComposeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone = os.environ.get("DOCKER_COMPOSE_BINARY")
        downloaded = ROOT / ".runtime/docker-validation/docker-compose-windows-x86_64.exe"
        if standalone:
            cls.command = [standalone]
        elif os.name == "nt" and downloaded.is_file():
            cls.command = [str(downloaded)]
        elif shutil.which("docker"):
            cls.command = ["docker", "compose"]
        elif shutil.which("docker-compose"):
            cls.command = ["docker-compose"]
        else:
            raise unittest.SkipTest("Docker Compose 未安装，跳过真实 Compose 配置解析；不代表容器验证通过。")
        cls.temporary = tempfile.TemporaryDirectory(prefix="docker-config-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.environment_file = cls.directory / "compose.env"
        cls.environment_file.write_text("APP_PORT=18080\n", encoding="utf-8")
        environment = os.environ.copy()
        environment["DOCKER_STATE_DIR"] = cls.directory.as_posix()
        environment.pop("APP_PORT", None)
        result = subprocess.run([*cls.command, "--env-file", str(cls.environment_file),
                                 "-p", "course-deployment-validation", "-f", str(ROOT / "compose.yaml"),
                                 "config", "--format", "json"], cwd=ROOT, env=environment,
                                text=True, encoding="utf-8", capture_output=True, timeout=30)
        if result.returncode:
            raise AssertionError("官方 Compose 配置解析失败：" + result.stderr)
        cls.config = json.loads(result.stdout)
        cls.services = cls.config["services"]

    def test_all_required_services_and_persistent_database_are_present(self):
        self.assertEqual(set(self.services), {"mysql", "init", "api", "catalog", "billing", "worker"})
        mysql = self.services["mysql"]
        self.assertEqual(mysql["image"], "mysql:8.0")
        self.assertTrue(any(v["type"] == "volume" and v["source"] == "mysql_data"
                            and v["target"] == "/var/lib/mysql" for v in mysql["volumes"]))
        self.assertIn("healthcheck", mysql)

    def test_only_api_is_published_and_only_on_loopback_with_configured_port(self):
        published = {name: service.get("ports", []) for name, service in self.services.items()
                     if service.get("ports")}
        self.assertEqual(set(published), {"api"})
        self.assertEqual(len(published["api"]), 1)
        binding = published["api"][0]
        self.assertEqual((binding["host_ip"], str(binding["published"]), binding["target"]),
                         ("127.0.0.1", "18080", 8000))
        self.assertEqual(self.services["api"]["environment"]["ALLOWED_ORIGINS"],
                         "http://127.0.0.1:18080,http://localhost:18080")

    def test_application_services_cannot_receive_database_root_secret(self):
        for role in ["api", "catalog", "billing", "worker"]:
            with self.subTest(role=role):
                service = self.services[role]
                supplied = {value["source"] for value in service.get("secrets", [])}
                self.assertNotIn("mysql_root_password", supplied)
                self.assertEqual(supplied, {"encryption_key", "integration_key", {
                    "api": "app_db_password", "worker": "app_db_password",
                    "catalog": "catalog_db_password", "billing": "billing_db_password"}[role]})
                self.assertNotIn("DATABASE_URL", service["environment"])
                self.assertFalse(any("PASSWORD" in name or name in {"ENCRYPTION_KEY", "INTEGRATION_KEY"}
                                     for name in service["environment"]))

    def test_application_services_wait_for_successful_initialization(self):
        self.assertEqual(self.services["init"]["depends_on"]["mysql"]["condition"], "service_healthy")
        self.assertEqual(self.services["init"]["restart"], "no")
        for role in ["api", "catalog", "billing", "worker"]:
            with self.subTest(role=role):
                self.assertEqual(self.services[role]["depends_on"]["init"]["condition"],
                                 "service_completed_successfully")
        self.assertEqual(self.services["api"]["depends_on"]["catalog"]["condition"], "service_healthy")
        self.assertEqual(self.services["worker"]["depends_on"]["billing"]["condition"], "service_healthy")

    def test_applications_have_read_only_root_and_cannot_mount_host_source_or_docker_socket(self):
        for role in ["api", "catalog", "billing", "worker"]:
            with self.subTest(role=role):
                service = self.services[role]
                self.assertTrue(service["read_only"])
                self.assertIn("no-new-privileges:true", service["security_opt"])
                self.assertFalse(service.get("privileged", False))
                self.assertFalse(service.get("volumes", []))
                self.assertNotIn("host", [service.get("network_mode"), service.get("pid")])
                self.assertEqual(service["restart"], "unless-stopped")
                self.assertIn("max-size", service["logging"]["options"])

    def test_initializer_output_mount_does_not_expose_entire_workspace(self):
        mounts = self.services["init"].get("volumes", [])
        self.assertEqual(len(mounts), 1)
        self.assertEqual((mounts[0]["type"], mounts[0]["target"]), ("bind", "/bootstrap-output"))
        self.assertTrue(mounts[0]["source"].replace("\\", "/").endswith("/accounts"))


class FakeResponse(io.BytesIO):
    def __init__(self, body=b'{}', status=200):
        super().__init__(body)
        self.status = status


class HealthcheckTests(unittest.TestCase):
    def test_api_health_probe_is_read_only_and_bounded(self):
        with patch.object(sys, "argv", ["healthcheck", "api"]), \
                patch.object(health, "urlopen", return_value=FakeResponse()) as open_request:
            self.assertEqual(health.main(), 0)
        request = open_request.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/api/v1/health")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(open_request.call_args.kwargs["timeout"], 5)

    def test_catalog_probe_authenticates_using_secret_without_logging_it(self):
        with patch.object(sys, "argv", ["healthcheck", "catalog"]), \
                patch.object(health.Path, "read_text", return_value="integration-test-only\n"), \
                patch.object(health, "urlopen", return_value=FakeResponse()) as open_request, \
                patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(health.main(), 0)
            self.assertEqual(output.getvalue() + errors.getvalue(), "")
        request = open_request.call_args.args[0]
        self.assertEqual(request.get_header("X-integration-key"), "integration-test-only")

    def test_failed_http_or_invalid_payload_is_unhealthy(self):
        for body, status in [(b'{}', 503), (b'[]', 200), (b'<html>error</html>', 200)]:
            with self.subTest(status=status, body=body), \
                    patch.object(sys, "argv", ["healthcheck", "api"]), \
                    patch.object(health, "urlopen", return_value=FakeResponse(body, status)):
                self.assertEqual(health.main(), 1)

    def test_connection_failure_does_not_expose_response_or_connection_details(self):
        with patch.object(sys, "argv", ["healthcheck", "api"]), \
                patch.object(health, "urlopen", side_effect=URLError("private-diagnostic")), \
                patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(health.main(), 1)
            self.assertEqual(output.getvalue() + errors.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
