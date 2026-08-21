import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

try:
    import psycopg
except Exception:
    psycopg = None


APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DEPLOY_PLATFORM_DB", "/data/deploy-platform.sqlite3"))
DATABASE_URL = os.environ.get("DATABASE_URL")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
HOST_DATA_DIR = Path(os.environ.get("HOST_DATA_DIR", str(DATA_DIR)))
WORKSPACE_DIR = DATA_DIR / "workspaces"
HOST_WORKSPACE_DIR = HOST_DATA_DIR / "workspaces"
REGISTRY_URL = os.environ.get("REGISTRY_URL", "").rstrip("/")
IMAGE_NAMESPACE = os.environ.get("IMAGE_NAMESPACE", "deploy-platform")
REGISTRY_USERNAME = os.environ.get("REGISTRY_USERNAME", "")
REGISTRY_PASSWORD = os.environ.get("REGISTRY_PASSWORD", "")
AGENT_SHARED_TOKEN = os.environ.get("AGENT_SHARED_TOKEN", "dev-agent-token")

DEFAULT_STATE = {
    "roles": {
        "platform_admin": {
            "label": "平台管理员",
            "permissions": [
                "task.view",
                "task.create",
                "task.deploy",
                "task.export",
                "cluster.view",
                "cluster.manage",
                "template.view",
                "template.manage",
                "channel.view",
                "channel.manage",
                "secret.view",
                "secret.manage",
                "user.view",
                "user.manage",
                "rbac.view",
                "rbac.manage",
                "audit.view",
            ],
        },
        "developer": {
            "label": "开发人员",
            "permissions": ["task.view", "task.create", "task.deploy", "cluster.view", "template.view", "channel.view", "secret.view"],
        },
        "auditor": {
            "label": "审计人员",
            "permissions": ["task.view", "cluster.view", "template.view", "channel.view", "user.view", "rbac.view", "audit.view"],
        },
        "viewer": {
            "label": "只读用户",
            "permissions": ["task.view", "cluster.view", "template.view", "channel.view"],
        },
    },
    "users": [{"username": "admin", "password": "admin123", "name": "平台管理员", "role": "platform_admin"}],
    "tasks": [],
    "clusters": [],
    "buildTemplates": [],
    "notifyChannels": [],
    "secrets": [],
    "auditLogs": [],
    "executions": [],
    "agentTasks": [],
    "agentHeartbeats": [],
    "schedules": [],
}

STATE_LOCK = threading.RLock()


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_schedule_time(value):
    if not value:
        raise ValueError("请选择定时发布时间")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def merge_defaults(state):
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = value.copy() if isinstance(value, dict) else list(value)
    return state


def use_postgres():
    return bool(DATABASE_URL and psycopg)


def connect_sqlite():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


def connect_postgres():
    conn = psycopg.connect(DATABASE_URL)
    conn.execute("CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    return conn


def read_state():
    with STATE_LOCK:
        if use_postgres():
            with connect_postgres() as conn:
                row = conn.execute("SELECT value FROM app_state WHERE key = 'state'").fetchone()
        else:
            with connect_sqlite() as conn:
                row = conn.execute("SELECT value FROM app_state WHERE key = 'state'").fetchone()

        if not row:
            write_state(DEFAULT_STATE)
            return json.loads(json.dumps(DEFAULT_STATE, ensure_ascii=False))
        return merge_defaults(json.loads(row[0]))


def write_state(state):
    payload = json.dumps(merge_defaults(state), ensure_ascii=False, separators=(",", ":"))
    with STATE_LOCK:
        if use_postgres():
            with connect_postgres() as conn:
                conn.execute(
                    "INSERT INTO app_state (key, value) VALUES ('state', %s) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
                    (payload,),
                )
                conn.commit()
        else:
            with connect_sqlite() as conn:
                conn.execute(
                    "INSERT INTO app_state (key, value) VALUES ('state', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (payload,),
                )


def mutate_state(fn):
    state = read_state()
    result = fn(state)
    write_state(state)
    return result, state


def append_log(execution_id, message):
    def update(state):
        execution = find_by_id(state["executions"], execution_id)
        if execution:
            execution.setdefault("logs", []).append({"time": now_text(), "message": message})

    mutate_state(update)


def set_execution_status(execution_id, status, message=None, image=None):
    def update(state):
        execution = find_by_id(state["executions"], execution_id)
        if not execution:
            return
        execution["status"] = status
        execution["updatedAt"] = now_text()
        if image:
            execution["image"] = image
        if message:
            execution.setdefault("logs", []).append({"time": now_text(), "message": message})
        task = find_by_id(state["tasks"], execution["taskId"])
        if task:
            task["status"] = status
            task["lastRun"] = now_text()

    mutate_state(update)


def find_by_id(items, item_id):
    item_id = str(item_id)
    return next((item for item in items if str(item.get("id")) == item_id), None)


def safe_name(value):
    value = re.sub(r"[^a-zA-Z0-9-]+", "-", str(value).lower()).strip("-")
    return value[:63] or "app"


def builder_image(sdk):
    sdk = str(sdk).lower()
    if sdk.startswith("jdk"):
        return f"eclipse-temurin:{sdk.replace('jdk', '')}-jdk"
    if sdk.startswith("node"):
        return f"node:{sdk.replace('node', '')}"
    if sdk.startswith("go"):
        return f"golang:{sdk.replace('go', '')}"
    if sdk.startswith("python"):
        return f"python:{sdk.replace('python', '')}"
    return "alpine:3.20"


def runtime_base(task):
    sdk = str(task.get("sdk", "")).lower()
    if task.get("language") == "java" and sdk.startswith("jdk"):
        return f"eclipse-temurin:{sdk.replace('jdk', '')}-jre"
    if task.get("language") == "node" and sdk.startswith("node"):
        return f"node:{sdk.replace('node', '')}-alpine"
    if task.get("language") == "python" and sdk.startswith("python"):
        return f"python:{sdk.replace('python', '')}-slim"
    return "alpine:3.20"


def image_name(task, execution_id):
    app = safe_name(task["name"])
    tag = execution_id[:12]
    repo = f"{IMAGE_NAMESPACE}/{app}:{tag}"
    return f"{REGISTRY_URL}/{repo}" if REGISTRY_URL else repo


def run_command(args, cwd=None, input_text=None, env=None):
    process = subprocess.run(
        args,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=env,
    )
    return process.returncode, process.stdout


def secret_by_id(state, secret_id):
    if not secret_id:
        return None
    return find_by_id(state.get("secrets", []), secret_id)


def authenticated_repo_url(repo, secret):
    if not secret or secret.get("type") != "git_https_token":
        return repo
    if not repo.startswith("https://"):
        return repo
    token = secret.get("secret") or ""
    username = secret.get("username") or "x-access-token"
    if not token or "@" in repo.split("://", 1)[1].split("/", 1)[0]:
        return repo
    return repo.replace("https://", f"https://{quote(username, safe='')}:{quote(token, safe='')}@", 1)


def redact_secret_text(text, secret=None):
    if not text:
        return text
    redacted = text
    if secret:
        for value in [secret.get("secret"), quote(secret.get("secret") or "", safe=""), secret.get("username"), quote(secret.get("username") or "", safe="")]:
            if value:
                redacted = redacted.replace(value, "***")
    return re.sub(r"https://[^\s/:]+:[^\s@]+@", "https://***:***@", redacted)


def clone_environment(work_dir, secret):
    env = os.environ.copy()
    if not secret or secret.get("type") != "git_ssh_key":
        return env
    ssh_dir = work_dir / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    key_path = ssh_dir / "id_deploy"
    key_path.write_text(secret.get("secret") or "", encoding="utf-8")
    key_path.chmod(0o600)
    known_hosts = (secret.get("knownHosts") or "").strip()
    known_hosts_path = ssh_dir / "known_hosts"
    if known_hosts:
        known_hosts_path.write_text(f"{known_hosts}\n", encoding="utf-8")
        host_check = "yes"
    else:
        host_check = "accept-new"
    env["GIT_SSH_COMMAND"] = f"ssh -i {key_path} -o StrictHostKeyChecking={host_check} -o UserKnownHostsFile={known_hosts_path}"
    return env


def list_repository_branches(repo, secret_id=None):
    state = read_state()
    secret = secret_by_id(state, secret_id)
    work_dir = WORKSPACE_DIR / f"branch-check-{uuid.uuid4().hex[:8]}"
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    repo_url = authenticated_repo_url(repo, secret)
    env = clone_environment(work_dir, secret)
    code, output = run_command(["git", "ls-remote", "--heads", repo_url], env=env)
    shutil.rmtree(work_dir, ignore_errors=True)
    if code != 0:
        raise RuntimeError(redact_secret_text(output.strip(), secret) or "读取仓库分支失败")
    branches = []
    for line in output.splitlines():
        if "refs/heads/" not in line:
            continue
        branches.append(line.split("refs/heads/", 1)[1].strip())
    return sorted(set(branches))


def generate_dockerfile(task, app_dir):
    existing = app_dir / "Dockerfile"
    if existing.exists():
        return existing

    generated = app_dir / ".deploy-platform.Dockerfile"
    language = task.get("language")
    port = int(task.get("containerPort") or 8080)
    if language == "java":
        jars = sorted(app_dir.glob("target/*.jar"))
        if not jars:
            jars = sorted(app_dir.glob("**/target/*.jar"))
        if not jars:
            raise RuntimeError("未找到 Java 构建产物 target/*.jar")
        jar = jars[0].relative_to(app_dir)
        generated.write_text(
            f"FROM {runtime_base(task)}\nWORKDIR /app\nCOPY {jar} app.jar\nEXPOSE {port}\nENTRYPOINT [\"java\",\"-jar\",\"/app/app.jar\"]\n",
            encoding="utf-8",
        )
    elif language == "golang":
        generated.write_text(
            f"FROM alpine:3.20\nWORKDIR /app\nCOPY . /app\nEXPOSE {port}\nCMD [\"./app\"]\n",
            encoding="utf-8",
        )
    elif language == "node":
        generated.write_text(
            f"FROM {runtime_base(task)}\nWORKDIR /app\nCOPY . /app\nEXPOSE {port}\nCMD [\"npm\",\"start\"]\n",
            encoding="utf-8",
        )
    elif language == "python":
        generated.write_text(
            f"FROM {runtime_base(task)}\nWORKDIR /app\nCOPY . /app\nEXPOSE {port}\nCMD [\"python\",\"app.py\"]\n",
            encoding="utf-8",
        )
    else:
        raise RuntimeError(f"暂不支持自动生成 {language} 运行镜像")
    return generated


def create_manifest(task, target, image):
    app = safe_name(task["name"])
    namespace = target.get("namespace") or "default"
    replicas = int(target.get("replicas") or task.get("replicas") or 1)
    container_port = int(task.get("containerPort") or 8080)
    service_port = int(task.get("servicePort") or 80)
    health_path = task.get("healthPath") or "/"
    ingress_host = target.get("ingress") or ""
    docs = [
        f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {app}
  namespace: {namespace}
  labels:
    app: {app}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {app}
  template:
    metadata:
      labels:
        app: {app}
    spec:
      containers:
        - name: {app}
          image: {image}
          imagePullPolicy: Always
          ports:
            - containerPort: {container_port}
          readinessProbe:
            httpGet:
              path: {health_path}
              port: {container_port}
            initialDelaySeconds: 10
            periodSeconds: 10
""",
        f"""apiVersion: v1
kind: Service
metadata:
  name: {app}
  namespace: {namespace}
spec:
  type: ClusterIP
  selector:
    app: {app}
  ports:
    - name: http
      port: {service_port}
      targetPort: {container_port}
""",
    ]
    if ingress_host:
        docs.append(
            f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {app}
  namespace: {namespace}
spec:
  rules:
    - host: {ingress_host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {app}
                port:
                  number: {service_port}
"""
        )
    return "---\n".join(docs)


def dispatch_agent_tasks(execution_id, task, image):
    def update(state):
        execution = find_by_id(state["executions"], execution_id)
        if not execution:
            return
        for target in task.get("clusters", []):
            agent_task_id = uuid.uuid4().hex[:12]
            cluster_name = target.get("name")
            payload = {
                "appName": task["name"],
                "namespace": target.get("namespace") or "default",
                "image": image,
                "manifest": create_manifest(task, target, image),
                "deployment": safe_name(task["name"]),
            }
            state["agentTasks"].append(
                {
                    "id": agent_task_id,
                    "executionId": execution_id,
                    "taskId": task["id"],
                    "clusterName": cluster_name,
                    "status": "pending",
                    "payload": payload,
                    "logs": [],
                    "createdAt": now_text(),
                    "updatedAt": now_text(),
                }
            )
            execution.setdefault("clusterResults", {})[cluster_name] = "pending"
        execution["status"] = "deploying"
        execution["image"] = image
        task_ref = find_by_id(state["tasks"], task["id"])
        if task_ref:
            task_ref["status"] = "deploying"
            task_ref["lastRun"] = now_text()

    mutate_state(update)


def send_notification(task, event, message):
    channels = read_state().get("notifyChannels", [])
    target = task.get("notify", {}).get("target") or ""
    channel = next((item for item in channels if item.get("name") == target or item.get("target") == target), None)
    url = (channel or {}).get("target") or target
    if not url.startswith("http"):
        return
    payload = {"task": task.get("name"), "event": event, "message": message, "time": now_text()}
    try:
        req = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
        urlopen(req, timeout=5).read()
    except Exception:
        pass


def build_and_dispatch(execution_id):
    state = read_state()
    execution = find_by_id(state["executions"], execution_id)
    if not execution:
        return
    task = find_by_id(state["tasks"], execution["taskId"])
    if not task:
        set_execution_status(execution_id, "failed", "任务不存在")
        return

    work_dir = WORKSPACE_DIR / execution_id
    src_dir = work_dir / "src"
    try:
        set_execution_status(execution_id, "building", "开始拉取代码")
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        branch = execution.get("branch")
        if not branch:
            raise RuntimeError("未选择发布分支")
        git_secret = secret_by_id(state, task.get("gitCredentialId"))
        clone_repo = authenticated_repo_url(task["repo"], git_secret)
        clone_env = clone_environment(work_dir, git_secret)
        clone_cmd = ["git", "clone", "--depth", "1", "--branch", branch, clone_repo, str(src_dir)]
        code, output = run_command(clone_cmd, env=clone_env)
        append_log(execution_id, redact_secret_text(output, git_secret))
        if code != 0:
            raise RuntimeError("代码拉取失败")

        app_dir = (src_dir / (task.get("workdir") or ".")).resolve()
        if not app_dir.exists():
            raise RuntimeError(f"工作路径不存在: {task.get('workdir')}")

        command = task.get("buildCommand") or ""
        if command:
            set_execution_status(execution_id, "building", f"使用 {task.get('sdk')} 执行编译命令")
            docker_src_dir = HOST_WORKSPACE_DIR / execution_id / "src"
            docker_cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{docker_src_dir}:/workspace",
                "-w",
                f"/workspace/{task.get('workdir') or '.'}",
                builder_image(task.get("sdk")),
                "sh",
                "-lc",
                command,
            ]
            code, output = run_command(docker_cmd)
            append_log(execution_id, output)
            if code != 0:
                raise RuntimeError("编译命令执行失败")

        image = image_name(task, execution_id)
        dockerfile = generate_dockerfile(task, app_dir)
        set_execution_status(execution_id, "building", f"开始构建镜像 {image}")
        code, output = run_command(["docker", "build", "-t", image, "-f", str(dockerfile), "."], cwd=app_dir)
        append_log(execution_id, output)
        if code != 0:
            raise RuntimeError("Docker 镜像构建失败")

        if REGISTRY_URL:
            if REGISTRY_USERNAME and REGISTRY_PASSWORD:
                code, output = run_command(["docker", "login", REGISTRY_URL, "-u", REGISTRY_USERNAME, "--password-stdin"], input_text=REGISTRY_PASSWORD)
                append_log(execution_id, output)
                if code != 0:
                    raise RuntimeError("镜像仓库登录失败")
            set_execution_status(execution_id, "building", f"推送镜像 {image}")
            code, output = run_command(["docker", "push", image])
            append_log(execution_id, output)
            if code != 0:
                raise RuntimeError("镜像推送失败")
        else:
            append_log(execution_id, "未配置 REGISTRY_URL，镜像只保留在本机 Docker，远端集群可能无法拉取。")

        if not task.get("clusters"):
            raise RuntimeError("任务未绑定部署集群")
        dispatch_agent_tasks(execution_id, task, image)
        send_notification(task, "BUILD_SUCCESS", f"镜像已构建: {image}")
    except Exception as exc:
        set_execution_status(execution_id, "failed", str(exc))
        send_notification(task, "BUILD_FAILED", str(exc))


def create_execution_record(state, task, actor, branch, action="触发发布"):
    execution_id = uuid.uuid4().hex[:12]
    execution = {
        "id": execution_id,
        "taskId": task["id"],
        "taskName": task["name"],
        "branch": branch,
        "actor": actor or "system",
        "status": "queued",
        "image": "",
        "logs": [{"time": now_text(), "message": "执行已进入队列"}],
        "clusterResults": {},
        "createdAt": now_text(),
        "updatedAt": now_text(),
    }
    state["executions"].insert(0, execution)
    task["status"] = "queued"
    task["lastRun"] = now_text()
    task["lastBranch"] = branch
    state["auditLogs"].insert(
        0,
        {
            "time": now_text(),
            "actor": actor or "system",
            "action": action,
            "target": f"{task['name']} / {branch}",
            "result": "已入队",
        },
    )
    return execution


def create_execution(task_id, actor, branch):
    def update(state):
        task = find_by_id(state["tasks"], task_id)
        if not task:
            raise ValueError("任务不存在")
        if not branch:
            raise ValueError("请选择发布分支")
        return create_execution_record(state, task, actor, branch)

    execution, state = mutate_state(update)
    threading.Thread(target=build_and_dispatch, args=(execution["id"],), daemon=True).start()
    return execution, state


def create_batch_executions(items, actor):
    if not isinstance(items, list) or not items:
        raise ValueError("请选择要批量发布的任务")

    def update(state):
        executions = []
        for item in items:
            task = find_by_id(state["tasks"], item.get("taskId"))
            if not task:
                raise ValueError(f"任务不存在: {item.get('taskId')}")
            branch = item.get("branch")
            if not branch:
                raise ValueError(f"{task['name']} 未选择发布分支")
            executions.append(create_execution_record(state, task, actor, branch, "批量发布"))
        return executions

    executions, state = mutate_state(update)
    for execution in executions:
        threading.Thread(target=build_and_dispatch, args=(execution["id"],), daemon=True).start()
    return executions, state


def schedule_execution(task_id, actor, branch, scheduled_at):
    run_at = parse_schedule_time(scheduled_at)
    if run_at <= datetime.now():
        raise ValueError("定时发布时间必须晚于当前时间")

    def update(state):
        task = find_by_id(state["tasks"], task_id)
        if not task:
            raise ValueError("任务不存在")
        if not branch:
            raise ValueError("请选择发布分支")
        for item in state.setdefault("schedules", []):
            if str(item.get("taskId")) == str(task["id"]) and item.get("status") == "pending":
                item["status"] = "cancelled"
                item["enabled"] = False
                item["updatedAt"] = now_text()
        schedule_id = uuid.uuid4().hex[:12]
        schedule = {
            "id": schedule_id,
            "taskId": task["id"],
            "taskName": task["name"],
            "branch": branch,
            "actor": actor or "system",
            "scheduledAt": scheduled_at,
            "status": "pending",
            "enabled": True,
            "createdAt": now_text(),
            "updatedAt": now_text(),
        }
        state.setdefault("schedules", []).insert(0, schedule)
        task["schedule"] = {
            "id": schedule_id,
            "branch": branch,
            "scheduledAt": scheduled_at,
            "status": "pending",
        }
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "创建定时发布", "target": f"{task['name']} / {branch}", "result": scheduled_at})
        return schedule

    schedule, state = mutate_state(update)
    return schedule, state


def cancel_schedule(schedule_id, actor):
    def update(state):
        schedule = find_by_id(state.setdefault("schedules", []), schedule_id)
        if not schedule:
            raise ValueError("定时发布不存在")
        if schedule.get("status") != "pending":
            raise ValueError("只能取消待执行的定时发布")
        schedule["status"] = "cancelled"
        schedule["enabled"] = False
        schedule["updatedAt"] = now_text()
        task = find_by_id(state["tasks"], schedule["taskId"])
        if task and task.get("schedule", {}).get("id") == schedule_id:
            task["schedule"]["status"] = "cancelled"
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "取消定时发布", "target": schedule.get("taskName"), "result": "成功"})
        return schedule

    schedule, state = mutate_state(update)
    return schedule, state


def trigger_due_schedules():
    due_execution_ids = []

    def update(state):
        now = datetime.now()
        for schedule in state.setdefault("schedules", []):
            if schedule.get("status") != "pending" or not schedule.get("enabled", True):
                continue
            try:
                if parse_schedule_time(schedule.get("scheduledAt")) > now:
                    continue
                task = find_by_id(state["tasks"], schedule["taskId"])
                if not task:
                    schedule["status"] = "failed"
                    schedule["error"] = "任务不存在"
                    schedule["updatedAt"] = now_text()
                    continue
                execution = create_execution_record(state, task, schedule.get("actor") or "scheduler", schedule.get("branch"), "定时发布")
                schedule["status"] = "triggered"
                schedule["executionId"] = execution["id"]
                schedule["triggeredAt"] = now_text()
                schedule["updatedAt"] = now_text()
                if task.get("schedule", {}).get("id") == schedule["id"]:
                    task["schedule"]["status"] = "triggered"
                    task["schedule"]["executionId"] = execution["id"]
                due_execution_ids.append(execution["id"])
            except Exception as exc:
                schedule["status"] = "failed"
                schedule["error"] = str(exc)
                schedule["updatedAt"] = now_text()
        return None

    mutate_state(update)
    for execution_id in due_execution_ids:
        threading.Thread(target=build_and_dispatch, args=(execution_id,), daemon=True).start()


def scheduler_loop():
    while True:
        try:
            trigger_due_schedules()
        except Exception as exc:
            print(f"schedule loop error: {exc}", flush=True)
        time.sleep(15)


def mark_agent_task_running(agent_task):
    def update(state):
        item = find_by_id(state["agentTasks"], agent_task["id"])
        if item and item["status"] == "pending":
            item["status"] = "running"
            item["updatedAt"] = now_text()
        execution = find_by_id(state["executions"], agent_task["executionId"])
        if execution:
            execution.setdefault("clusterResults", {})[agent_task["clusterName"]] = "running"

    mutate_state(update)


def update_agent_result(agent_task_id, status, logs):
    def update(state):
        item = find_by_id(state["agentTasks"], agent_task_id)
        if not item:
            return None
        item["status"] = status
        item["updatedAt"] = now_text()
        if logs:
            item.setdefault("logs", []).append({"time": now_text(), "message": logs})
        execution = find_by_id(state["executions"], item["executionId"])
        if execution:
            execution.setdefault("clusterResults", {})[item["clusterName"]] = status
            statuses = list(execution["clusterResults"].values())
            if statuses and all(value == "success" for value in statuses):
                execution["status"] = "success"
                task = find_by_id(state["tasks"], execution["taskId"])
                if task:
                    task["status"] = "success"
            elif any(value == "failed" for value in statuses):
                execution["status"] = "partial" if any(value == "success" for value in statuses) else "failed"
                task = find_by_id(state["tasks"], execution["taskId"])
                if task:
                    task["status"] = execution["status"]
        return item

    item, state = mutate_state(update)
    return item, state


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "public, max-age=60")
        super().end_headers()

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"status": "ok", "database": "postgres" if use_postgres() else "sqlite"})
            return
        if parsed.path == "/api/state":
            self.send_json(read_state())
            return
        if parsed.path == "/api/agent/tasks":
            query = parse_qs(parsed.query)
            cluster = query.get("cluster", [""])[0]
            token = query.get("token", [""])[0] or self.headers.get("X-Agent-Token", "")
            if AGENT_SHARED_TOKEN and token != AGENT_SHARED_TOKEN:
                self.send_json({"error": "unauthorized"}, status=401)
                return
            state = read_state()
            task = next((item for item in state["agentTasks"] if item.get("clusterName") == cluster and item.get("status") == "pending"), None)
            if not task:
                self.send_json({"task": None})
                return
            mark_agent_task_running(task)
            self.send_json({"task": task})
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks/batch-run":
            body = self.read_json_body()
            try:
                executions, state = create_batch_executions(body.get("items"), body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"executions": executions, "state": state})
            return
        match = re.match(r"^/api/tasks/([^/]+)/run$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                execution, state = create_execution(match.group(1), body.get("actor"), body.get("branch"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"execution": execution, "state": state})
            return
        match = re.match(r"^/api/tasks/([^/]+)/schedule$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                schedule, state = schedule_execution(match.group(1), body.get("actor"), body.get("branch"), body.get("scheduledAt"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"schedule": schedule, "state": state})
            return
        match = re.match(r"^/api/schedules/([^/]+)/cancel$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                schedule, state = cancel_schedule(match.group(1), body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"schedule": schedule, "state": state})
            return
        if parsed.path == "/api/repositories/branches":
            body = self.read_json_body()
            try:
                branches = list_repository_branches(body.get("repo") or "", body.get("gitCredentialId"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"branches": branches})
            return
        match = re.match(r"^/api/agent/tasks/([^/]+)/result$", parsed.path)
        if match:
            body = self.read_json_body()
            item, state = update_agent_result(match.group(1), body.get("status") or "failed", body.get("logs") or "")
            self.send_json({"task": item, "state": state})
            return
        if parsed.path == "/api/agent/heartbeat":
            body = self.read_json_body()

            def update(state):
                cluster = body.get("cluster")
                state["agentHeartbeats"] = [item for item in state["agentHeartbeats"] if item.get("cluster") != cluster]
                state["agentHeartbeats"].append({"cluster": cluster, "time": now_text(), "version": body.get("version", "dev")})

            mutate_state(update)
            self.send_json({"ok": True})
            return
        self.send_error(404)

    def do_PUT(self):
        if self.path != "/api/state":
            self.send_error(404)
            return
        try:
            state = self.read_json_body()
            write_state(state)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json({"ok": True})

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    read_state()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "80"))
    db_kind = "postgres" if use_postgres() else f"sqlite={DB_PATH}"
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Deploy Platform listening on :{port}, {db_kind}", flush=True)
    server.serve_forever()
