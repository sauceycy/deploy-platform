import copy
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError


spec = importlib.util.spec_from_file_location("agent_check", Path(__file__).resolve().parents[1] / "windows-agent/agent_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class AgentStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.path = self.root / "config.json"
        self.config = {
            "platformUrl": "https://platform.internal", "cluster": "win-test", "instanceId": "win-01",
            "agentToken": "private-test-token", "stateDirectory": "C:/ProgramData/Agent",
            "applications": {"mt5": {"Python": "C:/Python313/python.exe", "Uv": "C:/Tools/uv.exe",
                                    "ServiceWrapper": "C:/Tools/WinSW.exe", "InstallRoot": "C:/MT5"}},
        }

    def load(self, config=None):
        self.path.write_text(json.dumps(config if config is not None else self.config), encoding="utf-8")
        return check.load_config(self.path)

    def test_valid_configuration_does_not_require_existing_sidecar(self):
        self.assertEqual(self.load()["cluster"], "win-test")

    def test_invalid_json_identifies_location_without_echoing_credentials(self):
        self.path.write_text('{"agentToken":"private-test-token","Python":"C:\\Users\\Admin"}')
        with self.assertRaises(check.CheckError) as failure:
            check.load_config(self.path)
        self.assertIn("line 1, column", str(failure.exception))
        self.assertIn("forward slashes", str(failure.exception))
        self.assertNotIn("private-test-token", str(failure.exception))

    def test_missing_and_placeholder_fields_fail_with_field_name(self):
        for key in ("platformUrl", "cluster", "stateDirectory"):
            with self.subTest(field=key):
                value = copy.deepcopy(self.config)
                value.pop(key)
                with self.assertRaisesRegex(check.CheckError, key):
                    self.load(value)
        self.config["agentToken"] = "REPLACE_WITH_TOKEN"
        with self.assertRaisesRegex(check.CheckError, "agentToken"):
            self.load()

    def test_platform_urls_and_drive_roots_are_rejected(self):
        for url in ("https://deploy.example.com", "https://name:password@host", "ftp://host", "https://host?token=secret", "https://[bad", "https://host:bad", "https://:443"):
            with self.subTest(url=url):
                config = copy.deepcopy(self.config)
                config["platformUrl"] = url
                with self.assertRaises(check.CheckError):
                    self.load(config)
        self.config["stateDirectory"] = "C:/"
        with self.assertRaisesRegex(check.CheckError, "drive root"):
            self.load()

    def test_probe_preserves_running_task_and_does_not_create_agent_database(self):
        self.config["stateDirectory"] = str(self.root / "state")
        result = check.check_runtime(self.config)
        self.assertEqual(result["runningTasks"], 0)
        database = self.root / "state/tasks.sqlite3"
        self.assertFalse(database.exists())
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE tasks (status TEXT)")
            connection.execute("INSERT INTO tasks VALUES ('running')")
        self.assertEqual(check.check_runtime(self.config)["runningTasks"], 1)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("SELECT status FROM tasks").fetchone()[0], "running")

    def test_permission_failure_names_state_directory(self):
        with patch.object(check.Path, "mkdir", side_effect=PermissionError):
            with self.assertRaisesRegex(check.CheckError, "stateDirectory is not writable"):
                check.check_runtime(self.config)

    def test_invalid_encoding_and_environment_have_clear_errors(self):
        self.path.write_bytes(b'\xff')
        with self.assertRaisesRegex(check.CheckError, "UTF-8"):
            check.load_config(self.path)
        self.config["applications"]["mt5"]["Environment"] = None
        with self.assertRaisesRegex(check.CheckError, "Environment must be a JSON object"):
            self.load()

    def test_cli_failure_reports_error_without_traceback_or_credentials(self):
        self.path.write_text('{"agentToken":"private-test-token",}')
        output = io.StringIO()
        with patch.object(check.sys, "argv", ["agent_check.py", "--config", str(self.path)]), patch("sys.stdout", output):
            self.assertEqual(check.main(), 1)
        self.assertIn("CHECK FAILED", output.getvalue())
        self.assertIn("line 1, column", output.getvalue())
        self.assertNotIn("private-test-token", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_corrupt_database_fails_without_resetting_it(self):
        self.config["stateDirectory"] = str(self.root)
        database = self.root / "tasks.sqlite3"
        database.write_bytes(b"not sqlite")
        with self.assertRaisesRegex(check.CheckError, "task database"):
            check.check_runtime(self.config)
        self.assertEqual(database.read_bytes(), b"not sqlite")

    def test_heartbeat_success_never_requests_deployment_tasks(self):
        response = io.BytesIO(b'{"ok":true}')
        with patch.object(check, "urlopen", return_value=response) as request:
            self.assertTrue(check.check_platform(self.config)["ok"])
        sent = request.call_args.args[0]
        self.assertTrue(sent.full_url.endswith("/api/windows-agent/heartbeat"))
        self.assertEqual(json.loads(sent.data)["cluster"], "win-test")
        self.assertNotIn("agentToken", json.loads(sent.data))

    def test_http_failures_explain_registration_token_proxy_and_platform_version(self):
        for code, hint in ((400, "cluster name"), (401, "agentToken"), (403, "Cloudflare"), (404, "Update the platform")):
            with self.subTest(code=code):
                error = HTTPError("https://host", code, "failure", {}, io.BytesIO(b"secret response"))
                with patch.object(check, "urlopen", side_effect=error):
                    with self.assertRaisesRegex(check.CheckError, hint) as failure:
                        check.check_platform(self.config)
                    self.assertNotIn("secret response", str(failure.exception))
                    self.assertNotIn("private-test-token", str(failure.exception))

    def test_network_failure_and_html_response_have_actionable_errors(self):
        with patch.object(check, "urlopen", side_effect=URLError("secret connection details")):
            with self.assertRaisesRegex(check.CheckError, "DNS"):
                check.check_platform(self.config)
        with patch.object(check, "urlopen", return_value=io.BytesIO(b"<html>Login</html>")):
            with self.assertRaisesRegex(check.CheckError, "non-JSON"):
                check.check_platform(self.config)


if __name__ == "__main__":
    unittest.main()
