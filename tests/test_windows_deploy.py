import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import server as s
import windows_deploy as windows


spec = importlib.util.spec_from_file_location("windows_agent", Path(__file__).resolve().parents[1] / "windows-agent/windows_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class WindowsDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = copy.deepcopy(s.DEFAULT_STATE)
        self.state["clusters"] = [{"name": "win-test", "agentToken": "win-secret", "organizationId": "default"}]
        self.state["agentHeartbeats"] = [{"cluster": "win-test", "instanceId": "win-01", "kind": "windows", "time": s.now_text()}]
        self.task = s.normalize_task_payload({
            "name": "python-mt5-http", "repo": "https://example.com/repo.git", "deployRule": "windows",
            "clusters": [{"name": "win-test"}], "organizationId": "default",
        })
        self.task.update(id=1, status="pending")
        self.state["tasks"] = [self.task]
        self.patches = [patch.object(s, "read_state", side_effect=lambda: copy.deepcopy(self.state)),
                        patch.object(s, "mutate_state", side_effect=self.mutate),
                        patch.object(s, "DATA_DIR", self.root), patch.object(s, "send_notification")]
        for item in self.patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(self.patches)])
        self.addCleanup(self.temp.cleanup)

    def mutate(self, function, **_kwargs):
        next_state = copy.deepcopy(self.state)
        result = function(next_state)
        self.state = next_state
        return copy.deepcopy(result), copy.deepcopy(next_state)

    def enqueue(self):
        record = s.create_execution_record(self.state, self.state["tasks"][0], "admin", "main")
        windows.dispatch(s, self.state, record, self.task, "deploy", {"sha256": "a" * 64, "size": 5})
        return self.state["agentTasks"][-1]

    def handler(self, token="win-secret", body=None):
        handler = s.Handler.__new__(s.Handler)
        handler.headers = {"X-Agent-Token": token}
        handler.send_json = Mock()
        handler.read_json_body = Mock(return_value=body or {})
        handler.require_session_actor = Mock(return_value="admin")
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def get(self, path, instance="win-01", token="win-secret"):
        handler = self.handler(token)
        self.assertTrue(handler.windows_get(urlparse(path + f"?cluster=win-test&instanceId={instance}")))
        return handler

    def post(self, path, instance="win-01", **fields):
        handler = self.handler(body={"cluster": "win-test", "instanceId": instance, **fields})
        self.assertTrue(handler.windows_post(urlparse(path)))
        return handler

    def test_existing_deploy_rules_and_manifest_are_unchanged(self):
        self.assertEqual(s.task_deploy_rule({}), "k8s")
        self.assertEqual(s.task_deploy_rule({"deployRule": "cloudflare_pages"}), "cf_pages")
        manifest = s.create_manifest({"name": "demo", "containerPort": 8080}, {"namespace": "default"}, "repo/demo:1")
        self.assertIn("kind: Deployment", manifest)
        self.assertIn("image: repo/demo:1", manifest)
        self.assertEqual(self.task["sdk"], "python3.13")

    def test_claim_is_idempotent_and_does_not_take_over_running_task(self):
        self.enqueue()
        first = self.get("/api/windows-agent/tasks").send_json.call_args.args[0]["task"]
        second = self.get("/api/windows-agent/tasks").send_json.call_args.args[0]["task"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["assignedAgent"], "win-01")
        other = self.get("/api/windows-agent/tasks", "win-02")
        self.assertIsNone(other.send_json.call_args.args[0]["task"])

    def test_windows_claim_does_not_consume_kubernetes_tasks(self):
        self.state["agentTasks"] = [{"id": "k8s", "clusterName": "win-test", "status": "pending"}]
        self.assertIsNone(self.get("/api/windows-agent/tasks").send_json.call_args.args[0]["task"])
        self.assertEqual(self.state["agentTasks"][0]["status"], "pending")

    def test_artifact_authentication_and_instance_binding(self):
        item = self.enqueue()
        directory = self.root / "windows-artifacts"
        directory.mkdir()
        (directory / f"{item['executionId']}.zip").write_bytes(b"hello")
        self.get("/api/windows-agent/tasks")
        path = f"/api/windows-agent/artifacts/{item['id']}"
        self.assertEqual(self.get(path).wfile.getvalue(), b"hello")
        self.assertEqual(self.get(path, token="incorrect").send_json.call_args.kwargs["status"], 401)
        self.assertEqual(self.get(path, "win-02").send_json.call_args.kwargs["status"], 403)

    def test_cancelled_task_cannot_download_or_be_reclaimed(self):
        item = self.enqueue()
        self.get("/api/windows-agent/tasks")
        self.state["agentTasks"][0]["status"] = "cancelled"
        self.assertIsNone(self.get("/api/windows-agent/tasks").send_json.call_args.args[0]["task"])
        path = f"/api/windows-agent/artifacts/{item['id']}"
        self.assertEqual(self.get(path).send_json.call_args.kwargs["status"], 403)

    def test_progress_result_and_duplicate_result(self):
        item = self.enqueue()
        self.get("/api/windows-agent/tasks")
        prefix = f"/api/windows-agent/tasks/{item['id']}"
        self.post(prefix + "/progress", message="WinSW switching")
        self.assertIn("WinSW switching", self.state["executions"][0]["logs"][-1]["message"])
        self.post(prefix + "/result", status="success", logs="Full health passed")
        self.assertEqual(self.state["executions"][0]["status"], "success")
        self.assertEqual(self.state["tasks"][0]["status"], "success")
        count = len(self.state["executions"][0]["logs"])
        self.post(prefix + "/result", status="failed", logs="Duplicate")
        self.assertEqual(self.state["executions"][0]["status"], "success")
        self.assertEqual(len(self.state["executions"][0]["logs"]), count)

    def test_other_agent_cannot_report_result(self):
        item = self.enqueue()
        self.get("/api/windows-agent/tasks")
        response = self.post(f"/api/windows-agent/tasks/{item['id']}/result", instance="win-02", status="success")
        self.assertEqual(response.send_json.call_args.kwargs["status"], 403)
        self.assertEqual(self.state["agentTasks"][0]["status"], "running")

    def test_offline_node_rejected_without_partial_rollback_record(self):
        self.state["agentHeartbeats"] = []
        with self.assertRaisesRegex(ValueError, "未在线"):
            windows.rollback(s, 1, "admin", None)
        self.assertEqual(self.state["executions"], [])
        self.assertEqual(self.state["agentTasks"], [])

    def test_rollback_enqueues_without_building_or_downloading(self):
        execution, _ = windows.rollback(s, 1, "admin", None)
        self.assertEqual(execution["status"], "deploying")
        payload = self.state["agentTasks"][0]["payload"]
        self.assertEqual(payload["action"], "rollback")
        self.assertNotIn("downloadPath", payload)

    def test_rollback_checks_permissions_and_active_execution(self):
        self.state["users"].append({"username": "viewer", "role": "viewer", "organizationIds": ["default"]})
        with self.assertRaises(ValueError):
            windows.rollback(s, 1, "viewer", None)
        self.enqueue()
        with self.assertRaisesRegex(ValueError, "等待"):
            windows.rollback(s, 1, "admin", None)

    def test_failed_cutover_records_failure(self):
        item = self.enqueue()
        self.get("/api/windows-agent/tasks")
        self.post(f"/api/windows-agent/tasks/{item['id']}/result", status="failed", logs="Rollback restored old version")
        self.assertEqual(self.state["tasks"][0]["status"], "failed")

    def test_packaging_retains_artifact_outside_disposable_workspace(self):
        app = self.root / "workspaces" / "source"
        (app / "deploy").mkdir(parents=True)
        (app / "deploy/package_windows.py").touch()
        (app / "dist").mkdir()
        (app / "dist/python-mt5-sidecar-test.zip").write_bytes(b"package")
        execution = s.create_execution_record(self.state, self.state["tasks"][0], "admin", "main")
        with patch.object(s, "run_command_stream", return_value=(0, "", 0)) as runner:
            windows.package_and_dispatch(s, execution["id"], self.task, app)
        runner.assert_called_once()
        self.assertEqual((self.root / "windows-artifacts" / f"{execution['id']}.zip").read_bytes(), b"package")
        self.assertEqual(self.state["agentTasks"][0]["payload"]["sha256"], hashlib.sha256(b"package").hexdigest())
        self.assertEqual(self.state["executions"][0]["status"], "deploying")


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def package(self, extra=None, tamper=False):
        files = {name: b"placeholder" for name in ("deploy/windows/Expand-Release.ps1", "deploy/windows/Start-HttpService.ps1", "deploy/windows/inspect_runtime.py")}
        files.update(extra or {})
        manifest = {"files": {name: {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)} for name, value in files.items()}}
        path = self.root / "release.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, value in files.items():
                archive.writestr(name, value + b"tamper" if tamper else value)
            archive.writestr("release-manifest.json", json.dumps(manifest))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_valid_manifest_is_extracted(self):
        path, digest = self.package()
        target = self.root / "verified"
        agent.verify_package(path, digest, target)
        self.assertTrue((target / "deploy/windows/Expand-Release.ps1").exists())

    def test_wrong_download_hash_rejected(self):
        path, _ = self.package()
        with self.assertRaisesRegex(ValueError, "SHA256"):
            agent.verify_package(path, "0" * 64, self.root / "verified")

    def test_manifest_tampering_rejected(self):
        path, digest = self.package(tamper=True)
        with self.assertRaisesRegex(ValueError, "checksum"):
            agent.verify_package(path, digest, self.root / "verified")

    def test_windows_unsafe_paths_rejected_before_extraction(self):
        for name in ("../outside", "C:/outside", "src/CON.py", "src/Foo.", "src/foo.py", "src\\foo.py", "src/foo?.py"):
            with self.subTest(name=name):
                extra = {name: b"x"}
                if name == "src/foo.py":
                    extra["src/FOO.py"] = b"y"
                path, digest = self.package(extra)
                with self.assertRaises(ValueError):
                    agent.verify_package(path, digest, self.root / "verified")

    def test_agent_restart_marks_incomplete_task_failed_without_replay(self):
        config = {"platformUrl": "https://example.com", "cluster": "win", "agentToken": "secret",
                  "stateDirectory": str(self.root / "state"), "applications": {"python-mt5-http": {}}}
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(config))
        first = agent.Agent(config_path)
        first.db.execute("INSERT INTO tasks VALUES ('test','{}','running','')")
        first.db.commit()
        first.db.close()
        second = agent.Agent(config_path)
        try:
            self.assertEqual(second.db.execute("SELECT status FROM tasks").fetchone()[0], "failed")
            with patch.object(second, "request", return_value={"ok": True}) as request:
                second.report_pending()
                request.assert_called_once()
                self.assertEqual(second.db.execute("SELECT status FROM tasks").fetchone()[0], "reported")
        finally:
            second.db.close()


if __name__ == "__main__":
    unittest.main()
