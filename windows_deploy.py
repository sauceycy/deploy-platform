"""Windows release extension; Kubernetes and Pages keep their existing workers."""

import hashlib
import re
import shutil
import sys
import uuid
from urllib.parse import parse_qs


def parse_windows_env(content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > 65536 or '\x00' in content:
        raise ValueError('Windows .env 必须是文本，大小不超过 64 KiB，且不能包含空字符')
    values = {}
    names = set()
    for number, line in enumerate(content.lstrip('\ufeff').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError(f'Windows .env 第 {number} 行需要 KEY=VALUE 格式')
        if key.lower() in names:
            raise ValueError(f'Windows .env 第 {number} 行变量重复（Windows 不区分大小写）')
        names.add(key.lower())
        if value.startswith("'"):
            match = re.fullmatch(r"'([^']*)'\s*(?:#.*)?", value)
            if not match:
                raise ValueError(f'Windows .env 第 {number} 行单引号未闭合；不支持跨行值')
            value = match[1]
        elif value.startswith('"'):
            match = re.fullmatch(r'"((?:[^"\\]|\\.)*)"\s*(?:#.*)?', value)
            if not match:
                raise ValueError(f'Windows .env 第 {number} 行双引号未闭合；不支持跨行值')
            escapes = {'n': '\n', 'r': '\r', 't': '\t', '"': '"', '\\': '\\'}
            value = re.sub(r'\\([nrt"\\])', lambda found: escapes[found[1]], match[1])
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        if '${' in value:
            raise ValueError(f'Windows .env 第 {number} 行不支持变量展开，请填写最终值')
        values[key] = value
    return values


def dispatch(s, state, execution, task, action, artifact=None):
    targets = task.get("clusters") or []
    if not targets:
        raise ValueError("请选择 Windows Agent 服务器")
    content = task.get('windowsEnv') or ''
    env_file = {'content': content, 'variables': parse_windows_env(content)} if content.strip() else None
    names = []
    for target in targets:
        name = str(target.get("name") or "").strip()
        cluster = next((c for c in state["clusters"] if s.normalize_cluster_key(c.get("name")) == s.normalize_cluster_key(name)), None)
        if not cluster:
            raise ValueError(f"Windows 服务器未登记: {name}")
        s.require_actor_asset_access(state, execution["actor"], "task.deploy", cluster, "部署")
        heartbeat = s.cluster_heartbeat_for_name(state, name) or {}
        if heartbeat.get("kind") != "windows" or not s.cluster_agent_is_fresh(state, name):
            raise ValueError(f"Windows Agent 未在线: {name}")
        if env_file is not None and 'dotenv-v1' not in heartbeat.get('capabilities', []):
            raise ValueError(f'Windows Agent {name} 尚不支持发布配置 .env；请更新并重启 Agent，等待新心跳')
        if name in names:
            raise ValueError(f"重复的 Windows 服务器: {name}")
        names.append(name)
    for name in names:
        task_id = uuid.uuid4().hex[:12]
        payload = {
            "action": action,
            "application": s.safe_name(task.get("deploymentName") or task["name"]),
            "version": execution["id"],
        }
        if artifact:
            payload.update(artifact)
            payload["downloadPath"] = f"/api/windows-agent/artifacts/{task_id}"
        if env_file is not None:
            payload['envFile'] = env_file
        state["agentTasks"].append({
            "id": task_id, "kind": "windows", "executionId": execution["id"],
            "taskId": task["id"], "clusterName": name, "status": "pending",
            "payload": payload, "logs": [], "createdAt": s.now_text(), "updatedAt": s.now_text(),
        })
        execution.setdefault("clusterResults", {})[name] = "pending"
    execution.update(status="deploying", stage="Agent 部署", progress=86, updatedAt=s.now_text())
    execution["logs"].append({"time": s.now_text(), "message": f"Windows {action}: {', '.join(names)}"})
    original = s.find_by_id(state["tasks"], task["id"])
    if original and s.execution_is_latest_for_task(state, execution):
        original.update(status="deploying", stage="Agent 部署", progress=86)


def package_and_dispatch(s, execution_id, task, app_dir):
    packager = app_dir / "deploy" / "package_windows.py"
    if not packager.is_file():
        raise ValueError("Windows 发布需要项目提供 deploy/package_windows.py")
    s.set_execution_status(execution_id, "building", "制作 Windows 发布 ZIP", stage="打包产物", progress=40)
    code, _, _ = s.run_command_stream([sys.executable, str(packager), "--package-only"], execution_id, cwd=str(app_dir))
    if code:
        raise RuntimeError("Windows 产物打包失败")
    packages = list((app_dir / "dist").glob("python-mt5-sidecar-*.zip"))
    if len(packages) != 1:
        raise ValueError("打包目录需要恰好一个 python-mt5-sidecar-*.zip")
    if not 0 < packages[0].stat().st_size <= 256 * 1024 * 1024:
        raise ValueError("Windows ZIP 产物必须小于等于 256 MiB")
    artifact_dir = s.DATA_DIR / "windows-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    destination = artifact_dir / f"{execution_id}.zip"
    shutil.copyfile(packages[0], destination)
    with destination.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    s.ensure_execution_active(execution_id)

    def update(state):
        execution = s.find_by_id(state["executions"], execution_id)
        if not execution or execution["status"] == "cancelled":
            raise RuntimeError("发布已取消")
        dispatch(s, state, execution, task, "deploy", {"sha256": digest, "size": destination.stat().st_size})

    s.mutate_state(update)


def rollback(s, task_id, actor, config_id):
    def update(state):
        task = s.find_by_id(state["tasks"], task_id)
        if not task or s.task_deploy_rule(task) != "windows":
            raise ValueError("仅 Windows 任务支持此回滚入口")
        s.require_actor_asset_access(state, actor, "task.deploy", task, "回滚")
        config = s.deploy_config_by_id(task, config_id)
        if not s.user_can_access_deploy_config(state, s.find_user(state, actor), config):
            raise ValueError("当前用户组无权回滚该发布配置")
        if s.active_execution_for_task(state, task["id"]):
            raise ValueError("请等待当前发布结束后回滚")
        execution = s.create_execution_record(state, task, actor, "rollback", "Windows 版本回滚", config)
        dispatch(s, state, execution, s.effective_task_for_deploy_config(task, config), "rollback")
        return execution

    return s.mutate_state(update)


class WindowsRoutes:
    def windows_server(self):
        return sys.modules[self.__class__.__module__]

    def windows_identity(self, parsed, cluster, instance):
        s = self.windows_server()
        state = s.read_state()
        known = any(s.normalize_cluster_key(c.get("name")) == s.normalize_cluster_key(cluster) for c in state["clusters"])
        if not cluster or not instance or not known:
            self.send_json({"error": "Windows Agent 服务器或实例未登记"}, status=400)
            return False
        return self.require_agent_token(parsed, s.cluster_agent_token(state, cluster))

    def windows_get(self, parsed):
        if not parsed.path.startswith("/api/windows-agent/"):
            return False
        s = self.windows_server()
        query = parse_qs(parsed.query)
        cluster = query.get("cluster", [""])[0]
        instance = query.get("instanceId", [""])[0]
        if not self.windows_identity(parsed, cluster, instance):
            return True
        if parsed.path == "/api/windows-agent/tasks":
            def claim(state):
                candidates = [t for t in state["agentTasks"] if t.get("kind") == "windows" and s.agent_task_matches_cluster(t, cluster)]
                # Never reassign a running release: a slow SDK must not cause a second cutover.
                running = next((t for t in candidates if t["status"] == "running"), None)
                if running:
                    return running if running.get("assignedAgent") == instance else None
                task = next((t for t in candidates if t["status"] == "pending"), None)
                if task:
                    task.update(status="running", assignedAgent=instance, updatedAt=s.now_text())
                return task
            task, _ = s.mutate_state(claim, detect_changes=True)
            self.send_json({"task": task})
            return True
        match = re.fullmatch(r"/api/windows-agent/artifacts/([a-f0-9]{12})", parsed.path)
        if match:
            task = s.find_by_id(s.read_state()["agentTasks"], match.group(1))
            if not task or task.get("kind") != "windows" or not s.agent_task_matches_cluster(task, cluster) or task.get("assignedAgent") != instance or task["status"] != "running":
                self.send_json({"error": "产物无访问权限"}, status=403)
                return True
            path = s.DATA_DIR / "windows-artifacts" / f"{task['executionId']}.zip"
            if not path.is_file():
                self.send_json({"error": "产物不存在"}, status=404)
                return True
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as artifact:
                shutil.copyfileobj(artifact, self.wfile)
            return True
        self.send_json({"error": "not found"}, status=404)
        return True

    def windows_post(self, parsed):
        s = self.windows_server()
        rollback_match = re.fullmatch(r"/api/tasks/(\d+)/windows-rollback", parsed.path)
        if rollback_match:
            actor = self.require_session_actor()
            if actor:
                try:
                    body = self.read_json_body()
                    execution, state = rollback(s, rollback_match.group(1), actor, body.get("deployConfigId"))
                    self.send_json({"execution": s.execution_summary(execution), "state": s.client_state(state, compact=True)})
                except Exception as error:
                    self.send_json({"error": str(error)}, status=400)
            return True
        if not parsed.path.startswith("/api/windows-agent/"):
            return False
        body = self.read_json_body()
        cluster, instance = str(body.get("cluster") or ""), str(body.get("instanceId") or "")
        if not self.windows_identity(parsed, cluster, instance):
            return True
        if parsed.path == "/api/windows-agent/heartbeat":
            def heartbeat(state):
                state["agentHeartbeats"] = [h for h in state["agentHeartbeats"] if s.normalize_cluster_key(h.get("cluster")) != s.normalize_cluster_key(cluster)]
                capabilities = ['dotenv-v1'] if isinstance(body.get('capabilities'), list) and 'dotenv-v1' in body['capabilities'] else []
                state["agentHeartbeats"].append({"cluster": cluster, "instanceId": instance, "version": "windows-0.1", "kind": "windows", "time": s.now_text(), "capabilities": capabilities})
            s.mutate_state(heartbeat)
            self.send_json({"ok": True})
            return True
        match = re.fullmatch(r"/api/windows-agent/tasks/([a-f0-9]{12})/(progress|result)", parsed.path)
        if match:
            with s.STATE_LOCK:
                task = s.find_by_id(s.read_state()["agentTasks"], match.group(1))
                if not task:
                    self.send_json({"error": "任务已删除"}, status=404)
                    return True
                if not task or task.get("kind") != "windows" or not s.agent_task_matches_cluster(task, cluster) or task.get("assignedAgent") != instance:
                    self.send_json({"error": "任务无访问权限"}, status=403)
                    return True
                if match.group(2) == "result":
                    if task["status"] == "running" and body.get("logs"):
                        s.append_log(task["executionId"], str(body["logs"])[-32000:])
                    item, _ = s.update_agent_result(task["id"], body.get("status"), str(body.get("logs") or "")[-32000:], instance)
                    self.send_json({"ok": True, "status": item["status"]})
                    return True
                def progress(state):
                    item = s.find_by_id(state["agentTasks"], task["id"])
                    if item["status"] != "running":
                        return item["status"]
                    item["updatedAt"] = s.now_text()
                    message = str(body.get("message") or "")[:2000]
                    if message:
                        item["logs"].append({"time": s.now_text(), "message": message})
                        execution = s.find_by_id(state["executions"], item["executionId"])
                        if execution and execution["status"] == "deploying":
                            execution["logs"].append({"time": s.now_text(), "message": message})
                    return item["status"]
                status, _ = s.mutate_state(progress, detect_changes=True)
                self.send_json({"ok": True, "status": status})
            return True
        self.send_json({"error": "not found"}, status=404)
        return True
