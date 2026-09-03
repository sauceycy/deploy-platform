import base64
import atexit
import copy
import hashlib
import hmac
import json
import os
import queue
import re
import signal
import shutil
import sqlite3
import subprocess
import threading
import time
import socket
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape as xml_escape

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
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
RESET_ADMIN_PASSWORD = os.environ.get("RESET_ADMIN_PASSWORD", "false").lower() == "true"
AGENT_SHARED_TOKEN = os.environ.get("AGENT_SHARED_TOKEN", "dev-agent-token")
SESSION_SECRET = os.environ.get("SESSION_SECRET") or AGENT_SHARED_TOKEN or "deploy-platform-session"
AGENT_TASK_RETRY_SECONDS = int(os.environ.get("AGENT_TASK_RETRY_SECONDS", "300"))
AGENT_TASK_TAKEOVER_SECONDS = int(os.environ.get("AGENT_TASK_TAKEOVER_SECONDS", "45"))
WAITING_DEPLOY_RECOVERY_SECONDS = int(os.environ.get("WAITING_DEPLOY_RECOVERY_SECONDS", "45"))
AUTO_CREATE_NAMESPACE = os.environ.get("AUTO_CREATE_NAMESPACE", "false").lower() == "true"
ACTIVE_STATUSES = {"queued", "building", "deploying", "running"}
CLEAN_WORKSPACE_AFTER_BUILD = os.environ.get("CLEAN_WORKSPACE_AFTER_BUILD", "true").lower() != "false"
CLEAN_LOCAL_IMAGE_AFTER_BUILD = os.environ.get("CLEAN_LOCAL_IMAGE_AFTER_BUILD", "true").lower() != "false"
DOCKER_PRUNE_AFTER_BUILD = os.environ.get("DOCKER_PRUNE_AFTER_BUILD", "false").lower() == "true"
CLIENT_LOG_TAIL = int(os.environ.get("CLIENT_LOG_TAIL", "30"))
MAX_CONCURRENT_EXECUTIONS = max(1, int(os.environ.get("MAX_CONCURRENT_EXECUTIONS", "1")))
MAVEN_DOWNLOAD_THREADS = max(1, int(os.environ.get("MAVEN_DOWNLOAD_THREADS", "8")))
BUILD_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_EXECUTIONS, thread_name_prefix="deploy-build")

DEFAULT_STATE = {
    "revision": 0,
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
                "org.view",
                "org.manage",
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
            "permissions": ["task.view", "cluster.view", "template.view", "channel.view", "user.view", "org.view", "rbac.view", "audit.view"],
        },
        "viewer": {
            "label": "只读用户",
            "permissions": ["task.view", "cluster.view", "template.view", "channel.view"],
        },
    },
    "organizations": [{"id": "default", "name": "default", "description": "默认用户组", "permissions": [], "globalAccess": False}],
    "users": [{"username": "admin", "password": "admin123", "name": "平台管理员", "role": "platform_admin", "globalAccess": True, "organizationIds": ["default"]}],
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
    "platformSettings": {
        "registrySecretId": "",
        "imageNamespace": IMAGE_NAMESPACE,
    },
}

STATE_LOCK = threading.RLock()
WS_CLIENTS = set()
WS_LOCK = threading.RLock()
STATE_CACHE = None
STATE_WRITE_LOCK = threading.RLock()
STATE_WRITE_EVENT = threading.Event()
STATE_WRITE_STOP = threading.Event()
PENDING_STATE_PAYLOAD = None
PENDING_STATE_REVISION = 0
STATE_WRITE_ERROR = ""


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    minutes, rest = divmod(seconds, 60)
    if minutes:
        return f"{minutes}分{rest}秒"
    return f"{rest}秒"


def parse_schedule_time(value):
    if not value:
        raise ValueError("请选择定时发布时间")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def parse_time_text(value):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def merge_defaults(state):
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = copy.deepcopy(value)
    normalize_roles_state(state)
    normalize_group_state(state)
    return state


def default_user_passwords():
    return {user.get("username"): user.get("password") for user in DEFAULT_STATE.get("users", [])}


def seeded_admin_user():
    user = copy.deepcopy(DEFAULT_STATE["users"][0])
    if ADMIN_PASSWORD:
        user["password"] = ADMIN_PASSWORD
    return user


def normalize_roles_state(state):
    roles = state.get("roles")
    if not isinstance(roles, dict):
        state["roles"] = copy.deepcopy(DEFAULT_STATE["roles"])
        return
    for key, default_role in DEFAULT_STATE["roles"].items():
        role = roles.setdefault(key, copy.deepcopy(default_role))
        role["label"] = role.get("label") or default_role["label"]
        if not isinstance(role.get("permissions"), list):
            role["permissions"] = []
        if key == "platform_admin":
            role["permissions"] = list(dict.fromkeys([*role["permissions"], *default_role["permissions"]]))
    if "auditor" in roles:
        roles["auditor"]["permissions"] = list(dict.fromkeys([*roles["auditor"].get("permissions", []), "org.view"]))


def safe_group_id(value):
    value = re.sub(r"[^a-z0-9_-]+", "-", str(value or "default").strip().lower()).strip("-")
    return value or "default"


def normalize_group_state(state):
    groups = state.setdefault("organizations", [])
    if not isinstance(groups, list):
        state["organizations"] = groups = []
    if not groups:
        groups.append({"id": "default", "name": "default", "description": "默认用户组", "permissions": [], "globalAccess": False})
    for group in groups:
        group["id"] = group.get("id") or safe_group_id(group.get("name"))
        if str(group.get("id")) == "default":
            group["name"] = "default"
        group["name"] = group.get("name") or group["id"]
        if not isinstance(group.get("permissions"), list):
            group["permissions"] = []
        group["globalAccess"] = False if str(group.get("id")) == "default" else bool(group.get("globalAccess"))
    if not any(str(group.get("id")) == "default" for group in groups):
        groups.insert(0, {"id": "default", "name": "default", "description": "默认用户组", "permissions": [], "globalAccess": False})
    users = state.setdefault("users", [])
    if not isinstance(users, list):
        state["users"] = users = []
    if not users:
        users.append(seeded_admin_user())
    if not any(user.get("role") == "platform_admin" for user in users):
        admin = next((user for user in users if user.get("username") == "admin"), None)
        if admin:
            admin["role"] = "platform_admin"
            admin["globalAccess"] = True
        else:
            users.insert(0, seeded_admin_user())
    for user in users:
        if not isinstance(user.get("organizationIds"), list) or not user.get("organizationIds"):
            user["organizationIds"] = ["default"]
        user["globalAccess"] = bool(user.get("globalAccess") or user.get("role") == "platform_admin")
        if RESET_ADMIN_PASSWORD and ADMIN_PASSWORD and user.get("username") == "admin":
            user["password"] = ADMIN_PASSWORD
    for key in ("tasks", "clusters", "secrets"):
        for item in state.setdefault(key, []):
            item["organizationId"] = item.get("organizationId") or "default"


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


def read_raw_state():
    if use_postgres():
        with connect_postgres() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = 'state'").fetchone()
    else:
        with connect_sqlite() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = 'state'").fetchone()
    if not row:
        return None
    return merge_defaults(json.loads(row[0]))


def persist_state_payload(payload):
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


def state_payload(state):
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def enqueue_state_write(payload, revision):
    global PENDING_STATE_PAYLOAD, PENDING_STATE_REVISION
    with STATE_WRITE_LOCK:
        PENDING_STATE_PAYLOAD = payload
        PENDING_STATE_REVISION = int(revision or 0)
        STATE_WRITE_EVENT.set()


def state_writer_loop():
    global PENDING_STATE_PAYLOAD, PENDING_STATE_REVISION, STATE_WRITE_ERROR
    while not STATE_WRITE_STOP.is_set():
        STATE_WRITE_EVENT.wait(1)
        while True:
            with STATE_WRITE_LOCK:
                payload = PENDING_STATE_PAYLOAD
                revision = PENDING_STATE_REVISION
                PENDING_STATE_PAYLOAD = None
                PENDING_STATE_REVISION = 0
                STATE_WRITE_EVENT.clear()
            if not payload:
                break
            try:
                persist_state_payload(payload)
                STATE_WRITE_ERROR = ""
            except Exception as exc:
                STATE_WRITE_ERROR = str(exc)
                with STATE_WRITE_LOCK:
                    if not PENDING_STATE_PAYLOAD or revision >= PENDING_STATE_REVISION:
                        PENDING_STATE_PAYLOAD = payload
                        PENDING_STATE_REVISION = revision
                        STATE_WRITE_EVENT.set()
                print(f"Async state write failed: {exc}", flush=True)
                time.sleep(2)
                break


def flush_pending_state_write(timeout=5):
    del timeout
    global PENDING_STATE_PAYLOAD, PENDING_STATE_REVISION
    with STATE_WRITE_LOCK:
        payload = PENDING_STATE_PAYLOAD
        PENDING_STATE_PAYLOAD = None
        PENDING_STATE_REVISION = 0
        STATE_WRITE_EVENT.clear()
    if not payload:
        return True
    persist_state_payload(payload)
    return True


def default_state_copy():
    state = json.loads(json.dumps(DEFAULT_STATE, ensure_ascii=False))
    if ADMIN_PASSWORD:
        for user in state.get("users", []):
            if user.get("username") == "admin":
                user["password"] = ADMIN_PASSWORD
    return merge_defaults(state)


def preserve_existing_user_passwords(next_state, current_state=None):
    current_state = current_state if current_state is not None else read_raw_state()
    if not current_state:
        return next_state
    current_passwords = {user.get("username"): user.get("password") for user in current_state.get("users", [])}
    default_passwords = default_user_passwords()
    for user in next_state.get("users", []):
        username = user.get("username")
        if RESET_ADMIN_PASSWORD and ADMIN_PASSWORD and username == "admin":
            user["password"] = ADMIN_PASSWORD
            continue
        incoming_password = user.get("password")
        current_password = current_passwords.get(username)
        default_password = default_passwords.get(username)
        if current_password and not incoming_password:
            user["password"] = current_password
            continue
        if current_password and default_password and incoming_password == default_password and current_password != incoming_password:
            user["password"] = current_password
    return next_state


def preserve_sensitive_values(next_state, current_state=None):
    current_state = current_state if current_state is not None else read_raw_state()
    if not current_state:
        return next_state
    for key in ("secrets", "notifyChannels"):
        current_items = {str(item.get("id")): item for item in current_state.get(key, [])}
        for item in next_state.get(key, []):
            current = current_items.get(str(item.get("id")))
            item.pop("hasSecret", None)
            if current and not item.get("secret") and current.get("secret"):
                item["secret"] = current["secret"]
    return next_state


def preserve_runtime_fields(next_state, current_state=None):
    current_state = current_state if current_state is not None else read_raw_state()
    if not current_state:
        return next_state
    current_tasks = {str(item.get("id")): item for item in current_state.get("tasks", [])}
    runtime_task_fields = ("status", "stage", "progress", "lastRun", "lastBranch", "lastActor", "alerts", "schedule")
    for task in next_state.get("tasks", []):
        current = current_tasks.get(str(task.get("id")))
        if not current:
            continue
        for field in runtime_task_fields:
            if field in current:
                task[field] = copy.deepcopy(current[field])

    # Executions, Agent work and schedules are server-owned runtime data. Browser
    # configuration saves only carry compact snapshots and must never replace them.
    for key in ("executions", "agentTasks", "agentHeartbeats", "schedules"):
        next_state[key] = copy.deepcopy(current_state.get(key, []))
    return next_state


def compact_log_entry(log):
    lines = [line.strip() for line in str((log or {}).get("message") or "").splitlines() if line.strip()]
    message = lines[-1] if lines else str((log or {}).get("message") or "")
    return {"time": (log or {}).get("time") or "", "message": message[:1200]}


def compact_error_logs(logs):
    errors = []
    seen = set()
    pattern = re.compile(r"\[ERROR\]|\berror\b|fatal:|\bfailed\b|\bfailure\b|build failure|not found|blocked mirror|could not|forbidden|denied|拒绝|失败|异常", re.I)
    for log in reversed(logs):
        for line in reversed(str((log or {}).get("message") or "").splitlines()):
            text = line.strip()
            if not text or text in seen or not pattern.search(text):
                continue
            seen.add(text)
            errors.append({"time": (log or {}).get("time") or "", "message": text[:1200]})
            if len(errors) >= 5:
                return list(reversed(errors))
    return list(reversed(errors))


def execution_summary(execution, compact=False):
    logs = execution.get("logs") if isinstance(execution.get("logs"), list) else []
    item = {key: copy.deepcopy(value) for key, value in execution.items() if key != "logs"}
    item["logCount"] = len(logs)
    if logs:
        item["latestLog"] = compact_log_entry(logs[-1]) if compact else copy.deepcopy(logs[-1])
    if compact:
        item["errorSummary"] = compact_error_logs(logs)
        item["logs"] = []
    else:
        item["logs"] = copy.deepcopy(logs[-CLIENT_LOG_TAIL:])
    return item


def agent_task_summary(agent_task, compact=False):
    logs = agent_task.get("logs") if isinstance(agent_task.get("logs"), list) else []
    item = {key: copy.deepcopy(value) for key, value in agent_task.items() if key not in {"logs", "payload"}}
    item["logCount"] = len(logs)
    if not compact:
        item["logs"] = copy.deepcopy(logs[-5:])
    return item


def client_state(state, compact=False):
    data = {}
    for key, value in state.items():
        if key in {"executions", "agentTasks", "secrets", "notifyChannels", "users", "auditLogs"}:
            continue
        data[key] = copy.deepcopy(value)

    data["users"] = []
    for user in state.get("users", []):
        item = {key: copy.deepcopy(value) for key, value in user.items() if key != "password"}
        data["users"].append(item)

    data["secrets"] = []
    for secret in state.get("secrets", []):
        item = {key: copy.deepcopy(value) for key, value in secret.items() if key != "secret"}
        item["secret"] = ""
        item["hasSecret"] = bool(secret.get("secret"))
        data["secrets"].append(item)

    data["notifyChannels"] = []
    for channel in state.get("notifyChannels", []):
        item = {key: copy.deepcopy(value) for key, value in channel.items() if key != "secret"}
        item["secret"] = ""
        item["hasSecret"] = bool(channel.get("secret"))
        data["notifyChannels"].append(item)

    executions = state.get("executions", [])
    if compact:
        executions = executions[:50]
    data["executions"] = [execution_summary(execution, compact=compact) for execution in executions]

    agent_tasks = state.get("agentTasks", [])
    if compact:
        agent_tasks = agent_tasks[:50]
    data["agentTasks"] = [agent_task_summary(agent_task, compact=compact) for agent_task in agent_tasks]
    if compact:
        data["auditLogs"] = copy.deepcopy(state.get("auditLogs", [])[:500])
    else:
        data["auditLogs"] = copy.deepcopy(state.get("auditLogs", []))
    data["compact"] = bool(compact)
    return data


def read_state():
    global STATE_CACHE
    with STATE_LOCK:
        if STATE_CACHE is None:
            state = read_raw_state()
            if not state:
                state = default_state_copy()
                persist_state_payload(state_payload(state))
            STATE_CACHE = merge_defaults(state)
        return copy.deepcopy(STATE_CACHE)


def write_state(state, current_state=None, broadcast=True):
    global STATE_CACHE
    current_state = current_state if current_state is not None else read_state()
    state = merge_defaults(state)
    state = preserve_existing_user_passwords(state, current_state)
    state = preserve_sensitive_values(state, current_state)
    state = preserve_runtime_fields(state, current_state)
    current_revision = int((current_state or {}).get("revision") or 0)
    incoming_revision = int(state.get("revision") or 0)
    state["revision"] = max(current_revision, incoming_revision) + 1
    payload = state_payload(state)
    with STATE_LOCK:
        STATE_CACHE = copy.deepcopy(state)
    enqueue_state_write(payload, state.get("revision"))
    if broadcast:
        broadcast_state(state)


def websocket_accept_key(key):
    digest = hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def websocket_frame(payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    length = len(data)
    if length < 126:
        header = bytes([0x81, length])
    elif length < 65536:
        header = bytes([0x81, 126, (length >> 8) & 0xFF, length & 0xFF])
    else:
        header = bytes(
            [
                0x81,
                127,
                (length >> 56) & 0xFF,
                (length >> 48) & 0xFF,
                (length >> 40) & 0xFF,
                (length >> 32) & 0xFF,
                (length >> 24) & 0xFF,
                (length >> 16) & 0xFF,
                (length >> 8) & 0xFF,
                length & 0xFF,
            ]
        )
    return header + data


def read_exact(conn, length):
    data = bytearray()
    while len(data) < length:
        chunk = conn.recv(length - len(data))
        if not chunk:
            raise ConnectionError("websocket closed")
        data.extend(chunk)
    return bytes(data)


def broadcast_ws(payload):
    frame = websocket_frame(payload)
    with WS_LOCK:
        clients = list(WS_CLIENTS)
    stale = []
    for client in clients:
        try:
            client.connection.sendall(frame)
        except Exception:
            stale.append(client)
    if stale:
        with WS_LOCK:
            for client in stale:
                WS_CLIENTS.discard(client)


def broadcast_state(state):
    if not WS_CLIENTS:
        return
    broadcast_ws({"type": "state", "state": client_state(state, compact=True)})


def mutate_state(fn, detect_changes=False, broadcast=True):
    with STATE_LOCK:
        state = read_state()
        previous = canonical_json(state) if detect_changes else None
        result = fn(state)
        if detect_changes and canonical_json(state) == previous:
            return result, state
        write_state(state, current_state=state, broadcast=broadcast)
        return result, state


def append_log(execution_id, message):
    def update(state):
        execution = find_by_id(state["executions"], execution_id)
        if execution:
            log_entry = {"time": now_text(), "message": message}
            execution.setdefault("logs", []).append(log_entry)
            return execution, log_entry
        return None, None

    result, _ = mutate_state(update, broadcast=False)
    execution, log_entry = result or (None, None)
    if execution and log_entry:
        broadcast_ws(
            {
                "type": "execution_log",
                "executionId": execution.get("id"),
                "taskId": execution.get("taskId"),
                "log": log_entry,
                "logCount": len(execution.get("logs") or []),
            }
        )


def set_execution_status(execution_id, status, message=None, image=None, stage=None, progress=None):
    def update(state):
        execution = find_by_id(state["executions"], execution_id)
        if not execution:
            return
        current_status = execution.get("status")
        if current_status == "cancelled" and status != "cancelled":
            return
        if current_status in {"success", "partial", "failed"} and status != current_status:
            return
        execution["status"] = status
        execution["updatedAt"] = now_text()
        if stage:
            execution["stage"] = stage
        if progress is not None:
            execution["progress"] = max(0, min(100, int(progress)))
        if image:
            execution["image"] = image
        if message:
            execution.setdefault("logs", []).append({"time": now_text(), "message": message})
        task = find_by_id(state["tasks"], execution["taskId"])
        if task and execution_is_latest_for_task(state, execution):
            task["status"] = status
            task["lastRun"] = now_text()
            if stage:
                task["stage"] = stage
            if progress is not None:
                task["progress"] = max(0, min(100, int(progress)))

    mutate_state(update)


def find_by_id(items, item_id):
    item_id = str(item_id)
    return next((item for item in items if str(item.get("id")) == item_id), None)


def latest_execution_for_task(state, task_id):
    return next((item for item in state.get("executions", []) if str(item.get("taskId")) == str(task_id)), None)


def execution_is_latest_for_task(state, execution):
    latest = latest_execution_for_task(state, execution.get("taskId"))
    return bool(latest and str(latest.get("id")) == str(execution.get("id")))


def active_execution_for_task(state, task_id):
    latest = latest_execution_for_task(state, task_id)
    if latest and is_active_status(latest.get("status")):
        return latest
    return None


def find_user(state, username):
    return next((user for user in state.get("users", []) if user.get("username") == (username or "system")), None)


def public_user(user):
    return {
        "username": user.get("username"),
        "name": user.get("name") or user.get("username"),
        "role": user.get("role") or "viewer",
        "globalAccess": bool(user.get("globalAccess")),
        "organizationIds": user_org_ids(user),
    }


def base64url_encode(data):
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def base64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def sign_session_payload(payload):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64url_encode(data)
    signature = hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return f"{body}.{base64url_encode(signature)}"


def verify_session_token(token):
    token = str(token or "").strip()
    if "." not in token:
        raise ValueError("登录状态已失效")
    body, signature = token.rsplit(".", 1)
    expected = base64url_encode(hmac.new(SESSION_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise ValueError("登录状态签名无效，请重新登录")
    payload = json.loads(base64url_decode(body).decode("utf-8"))
    username = str(payload.get("username") or "").strip()
    if not username:
        raise ValueError("登录状态已失效")
    return username


def issue_session_token(user):
    return sign_session_payload({"username": user.get("username"), "iat": int(time.time())})


def authenticate_user(username, password):
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise ValueError("请输入账号和密码")
    state = read_state()
    user = find_user(state, username)
    if not user:
        raise ValueError("账号不存在")
    if str(user.get("password") or "") != password:
        if username == "admin" and password == "admin123":
            raise ValueError("默认 admin 密码已不是 admin123，请使用当前密码；如需强制恢复可设置 ADMIN_PASSWORD=新密码 和 RESET_ADMIN_PASSWORD=true 后重启")
        raise ValueError("账号或密码不正确")
    public = public_user(user)
    public["token"] = issue_session_token(user)
    return public, state


def session_user(token=None):
    username = verify_session_token(token)
    if not username:
        raise ValueError("登录状态已失效")
    state = read_state()
    user = find_user(state, username)
    if not user:
        raise ValueError("用户不存在，请重新登录")
    public = public_user(user)
    public["token"] = issue_session_token(user)
    return public, state


def user_groups(state, user):
    ids = [str(item) for item in (user or {}).get("organizationIds", ["default"])]
    return [group for group in state.get("organizations", []) if str(group.get("id")) in ids]


def user_has_global_access(state, user):
    return bool((user or {}).get("globalAccess") or (user or {}).get("role") == "platform_admin" or any(group.get("globalAccess") for group in user_groups(state, user)))


def user_org_ids(user):
    ids = (user or {}).get("organizationIds")
    return [str(item) for item in ids] if isinstance(ids, list) and ids else ["default"]


def asset_org_id(asset):
    return str((asset or {}).get("organizationId") or "default")


def user_can_access_asset(state, user, asset):
    return user_has_global_access(state, user) or asset_org_id(asset) in user_org_ids(user)


def user_permissions(state, user):
    role = state.get("roles", {}).get((user or {}).get("role"), {})
    permissions = list(role.get("permissions") or [])
    for group in user_groups(state, user):
        permissions.extend(group.get("permissions") or [])
    return set(permissions)


def user_has_permission(state, user, permission):
    return permission in user_permissions(state, user)


def user_can_access_org(state, user, org_id):
    return user_has_global_access(state, user) or str(org_id or "default") in user_org_ids(user)


def user_can_access_user(state, actor_user, target_user):
    if user_has_global_access(state, actor_user):
        return True
    actor_groups = set(user_org_ids(actor_user))
    target_groups = set(user_org_ids(target_user))
    return bool(actor_groups.intersection(target_groups))


def require_actor_asset_access(state, actor, permission, asset, action):
    user = find_user(state, actor)
    if not user:
        raise ValueError("操作用户不存在")
    if not user_has_permission(state, user, permission):
        raise ValueError(f"当前用户没有{action}权限")
    if not user_can_access_asset(state, user, asset):
        raise ValueError(f"当前用户组无权{action}该资产")
    return user


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_asset_state_changes(current_state, next_state, actor):
    if not actor or actor == "system":
        return
    actor_user = find_user(current_state, actor)
    if not actor_user:
        raise ValueError("操作用户不存在")

    asset_permissions = {
        "tasks": "task.create",
        "clusters": "cluster.manage",
        "secrets": "secret.manage",
    }
    for key, permission in asset_permissions.items():
        current_items = {str(item.get("id")): item for item in current_state.get(key, [])}
        next_items = {str(item.get("id")): item for item in next_state.get(key, [])}

        for item_id, next_item in next_items.items():
            current_item = current_items.get(item_id)
            if current_item and canonical_json(current_item) == canonical_json(next_item):
                continue
            if not user_has_permission(current_state, actor_user, permission):
                raise ValueError("当前用户没有保存资产权限")
            if current_item and not user_can_access_asset(current_state, actor_user, current_item):
                raise ValueError("当前用户组无权修改该资产")
            if not user_can_access_asset(current_state, actor_user, next_item):
                raise ValueError("当前用户组无权保存资产到目标用户组")

        for item_id, current_item in current_items.items():
            if item_id in next_items:
                continue
            if not user_has_permission(current_state, actor_user, permission):
                raise ValueError("当前用户没有删除资产权限")
            if not user_can_access_asset(current_state, actor_user, current_item):
                raise ValueError("当前用户组无权删除该资产")


def validate_user_state_changes(current_state, next_state, actor):
    if not actor or actor == "system":
        return
    actor_user = find_user(current_state, actor)
    if not actor_user:
        raise ValueError("操作用户不存在")

    current_users = {str(item.get("username")): item for item in current_state.get("users", [])}
    next_users = {str(item.get("username")): item for item in next_state.get("users", [])}

    for username, next_user in next_users.items():
        current_user = current_users.get(username)
        if current_user and canonical_json(current_user) == canonical_json(next_user):
            continue
        if not user_has_permission(current_state, actor_user, "user.manage"):
            raise ValueError("当前用户没有管理用户权限")
        if current_user and not user_can_access_user(current_state, actor_user, current_user):
            raise ValueError("当前用户组无权修改该用户")
        if not user_can_access_user(current_state, actor_user, next_user):
            raise ValueError("当前用户组无权保存该用户到目标用户组")
        if not user_has_global_access(current_state, actor_user) and next_user.get("globalAccess"):
            raise ValueError("当前用户组无权授予全局组权限")
        if not user_has_global_access(current_state, actor_user):
            allowed_groups = set(user_org_ids(actor_user))
            if not set(user_org_ids(next_user)).issubset(allowed_groups):
                raise ValueError("当前用户组无权分配目标用户组")

    for username, current_user in current_users.items():
        if username in next_users:
            continue
        if not user_has_permission(current_state, actor_user, "user.manage"):
            raise ValueError("当前用户没有删除用户权限")
        if not user_can_access_user(current_state, actor_user, current_user):
            raise ValueError("当前用户组无权删除该用户")


def validate_group_state_changes(current_state, next_state, actor):
    if not actor or actor == "system":
        return
    actor_user = find_user(current_state, actor)
    if not actor_user:
        raise ValueError("操作用户不存在")

    current_groups = {str(item.get("id")): item for item in current_state.get("organizations", [])}
    next_groups = {str(item.get("id")): item for item in next_state.get("organizations", [])}

    if "default" not in next_groups:
        raise ValueError("default 用户组不能删除")

    for group_id, next_group in next_groups.items():
        current_group = current_groups.get(group_id)
        if current_group and canonical_json(current_group) == canonical_json(next_group):
            continue
        if not user_has_permission(current_state, actor_user, "org.manage"):
            raise ValueError("当前用户没有管理用户组权限")
        if not current_group and not user_has_global_access(current_state, actor_user):
            raise ValueError("只有全局组用户可以创建新用户组")
        if current_group and not user_can_access_org(current_state, actor_user, group_id):
            raise ValueError("当前用户组无权修改该用户组")
        if group_id == "default" and next_group.get("globalAccess"):
            raise ValueError("default 用户组不能设置为全局组")
        if current_group and bool(current_group.get("globalAccess")) != bool(next_group.get("globalAccess")) and not user_has_global_access(current_state, actor_user):
            raise ValueError("只有全局组用户可以修改全局组开关")

    for group_id, current_group in current_groups.items():
        if group_id in next_groups:
            continue
        if group_id == "default":
            raise ValueError("default 用户组不能删除")
        if not user_has_permission(current_state, actor_user, "org.manage") or not user_has_global_access(current_state, actor_user):
            raise ValueError("只有全局组用户可以删除用户组")


def validate_cluster_names(next_state):
    seen = {}
    for cluster in next_state.get("clusters", []):
        name = str(cluster.get("name") or "").strip()
        if not name:
            raise ValueError("集群名称不能为空")
        key = name.lower()
        if key in seen:
            raise ValueError(f"集群名称重复: {name}。每个集群名称必须唯一，并与对应 Agent 的 CLUSTER_NAME 一一对应。")
        seen[key] = True


def validate_state_update(next_state, actor, current_state=None):
    validate_cluster_names(next_state)
    if not actor or actor == "system":
        return
    current_state = current_state if current_state is not None else read_state()
    validate_group_state_changes(current_state, next_state, actor)
    validate_user_state_changes(current_state, next_state, actor)
    validate_asset_state_changes(current_state, next_state, actor)
    actor_user = find_user(current_state, actor)
    if canonical_json(current_state.get("platformSettings", {})) != canonical_json(next_state.get("platformSettings", {})):
        if not user_has_permission(current_state, actor_user, "secret.manage"):
            raise ValueError("当前用户没有保存镜像仓库配置权限")
        if not user_has_global_access(current_state, actor_user):
            raise ValueError("只有全局组用户可以修改平台镜像仓库配置")


def is_active_status(status):
    return status in ACTIVE_STATUSES


def is_execution_cancelled(execution_id):
    state = read_state()
    execution = find_by_id(state["executions"], execution_id)
    return bool(execution and execution.get("status") == "cancelled")


def ensure_execution_active(execution_id):
    if is_execution_cancelled(execution_id):
        raise RuntimeError("发布已取消")


def safe_name(value):
    value = re.sub(r"[^a-zA-Z0-9-]+", "-", str(value).lower()).strip("-")
    return value[:63] or "app"


def builder_image(sdk):
    sdk = str(sdk).lower()
    if sdk.startswith("jdk"):
        return f"maven:3-eclipse-temurin-{sdk.replace('jdk', '')}"
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


def redact_url_credentials(value):
    return re.sub(r"://[^/@]+@", "://***@", str(value or ""))


def write_maven_settings(task, src_dir):
    repo_url = str(task.get("mavenRepoUrl") or "").strip()
    if task.get("language") != "java" or not repo_url:
        return None
    if not re.match(r"^https?://", repo_url, flags=re.IGNORECASE):
        raise RuntimeError("Maven 私库地址必须以 http:// 或 https:// 开头")

    mirror_of = str(task.get("mavenMirrorOf") or "maven-public").strip() or "maven-public"
    settings_dir = src_dir / ".deploy"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_file = settings_dir / "maven-settings.xml"
    settings_file.write_text(
        f"""<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.0.0 https://maven.apache.org/xsd/settings-1.0.0.xsd">
  <mirrors>
    <mirror>
      <id>deploy-platform-private-repo</id>
      <name>Deploy Platform Private Maven Repository</name>
      <url>{xml_escape(repo_url)}</url>
      <mirrorOf>{xml_escape(mirror_of)}</mirrorOf>
    </mirror>
  </mirrors>
</settings>
""",
        encoding="utf-8",
    )
    return "/workspace/.deploy/maven-settings.xml"


def apply_maven_settings_to_command(command, settings_path):
    if not settings_path or re.search(r"(^|\s)(-s|--settings)(\s|=)", command):
        return command, True
    match = re.match(r"^(\s*)((?:\./)?mvnw|mvn)(\s|$)", command)
    if not match:
        return command, False
    prefix, binary = match.group(1), match.group(2)
    rest = command[match.end(2) :]
    return f"{prefix}{binary} -s {settings_path}{rest}", True


def apply_maven_fast_options(command):
    match = re.match(r"^(\s*)((?:\./)?mvnw|mvn)(\s|$)", str(command or ""))
    if not match:
        return command, []
    prefix, binary = match.group(1), match.group(2)
    rest = command[match.end(2) :]
    options = []
    if not re.search(r"(^|\s)-B(\s|$)", command):
        options.append("-B")
    if not re.search(r"(^|\s)-ntp(\s|$)", command):
        options.append("-ntp")
    if "maven.artifact.threads" not in command:
        options.append(f"-Dmaven.artifact.threads={MAVEN_DOWNLOAD_THREADS}")
    if not options:
        return command, []
    return f"{prefix}{binary} {' '.join(options)}{rest}", options


def parse_key_value_env(value, label="环境变量", key_pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", key_hint="KEY"):
    env = {}
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise RuntimeError(f"{label}第 {line_number} 行格式错误，应为 KEY=VALUE")
        key, item_value = line.split("=", 1)
        key = key.strip()
        if not re.match(key_pattern, key):
            raise RuntimeError(f"{label}名不合法: {key}，请使用 {key_hint}=VALUE")
        env[key] = item_value
    return env


def parse_build_env(value):
    return parse_key_value_env(value, "构建环境变量", key_hint="SENTRY_AUTH_TOKEN")


def parse_runtime_env(value):
    return parse_key_value_env(
        value,
        "运行环境变量",
        key_pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$",
        key_hint="SPRING_PROFILES_ACTIVE 或 spring.profiles.active",
    )


def docker_env_args(env):
    args = []
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    return args


def build_cache_config(task):
    language = str(task.get("language") or "").lower()
    sdk = str(task.get("sdk") or "").lower()
    cache_dir = DATA_DIR / "cache"
    host_cache_dir = HOST_DATA_DIR / "cache"
    mounts = []
    env = {}
    labels = []

    def add_cache(name, container_path, label):
        (cache_dir / name).mkdir(parents=True, exist_ok=True)
        mounts.extend(["-v", f"{host_cache_dir / name}:{container_path}"])
        labels.append(label)

    if language == "java" or sdk.startswith("jdk"):
        add_cache("maven", "/root/.m2", "Maven")
    if language == "node" or sdk.startswith("node"):
        add_cache("npm", "/root/.npm", "npm")
        add_cache("corepack", "/root/.cache/corepack", "Corepack")
        add_cache("pnpm-store", "/root/.pnpm-store", "pnpm")
        env["npm_config_cache"] = "/root/.npm"
        env["COREPACK_HOME"] = "/root/.cache/corepack"
        env["npm_config_store_dir"] = "/root/.pnpm-store"
    if language == "golang" or sdk.startswith("go"):
        add_cache("go-mod", "/go/pkg/mod", "Go modules")
        add_cache("go-build", "/root/.cache/go-build", "Go build")
        env["GOMODCACHE"] = "/go/pkg/mod"
        env["GOCACHE"] = "/root/.cache/go-build"
    if language == "python" or sdk.startswith("python"):
        add_cache("pip", "/root/.cache/pip", "pip")
        env["PIP_CACHE_DIR"] = "/root/.cache/pip"

    return mounts, env, list(dict.fromkeys(labels))


def task_deploy_rule(task):
    value = str(task.get("deployRule") or "k8s").strip().lower()
    if value in {"pages", "cf", "cf_pages", "cloudflare_pages"}:
        return "cf_pages"
    return "k8s"


def task_app_type(task):
    if task_deploy_rule(task) == "cf_pages":
        return "frontend"
    return "frontend" if str(task.get("appType") or "").strip().lower() == "frontend" else "backend"


def default_pages_deploy_command(package_manager):
    return "pnpm run deploy" if package_manager == "pnpm" else "npm run deploy"


def pages_package_manager(task):
    value = str(task.get("pagesPackageManager") or "npm").strip().lower()
    return "pnpm" if value == "pnpm" else "npm"


def pages_deploy_command(task):
    package_manager = pages_package_manager(task)
    command = str(task.get("pagesDeployCommand") or "").strip() or default_pages_deploy_command(package_manager)
    if package_manager == "pnpm" and "corepack" not in command and re.search(r"(^|[;&|]\s*)pnpm(\s|$)", command):
        return f"corepack enable && {command}"
    return command


def cloudflare_pages_env(state, task):
    env = {}
    account_id_secret_id = str(task.get("cloudflareAccountIdSecretId") or "").strip()
    api_token_secret_id = str(task.get("cloudflareApiTokenSecretId") or "").strip()
    if account_id_secret_id:
        account_id_secret = secret_by_id(state, account_id_secret_id)
        if not account_id_secret:
            raise RuntimeError("Cloudflare Account ID 秘钥不存在")
        if account_id_secret.get("type") != "cloudflare_account_id":
            raise RuntimeError("Cloudflare Account ID 秘钥类型不正确")
        if not account_id_secret.get("secret"):
            raise RuntimeError("Cloudflare Account ID 秘钥内容不能为空")
        env["CLOUDFLARE_ACCOUNT_ID"] = str(account_id_secret.get("secret") or "")
    if api_token_secret_id:
        api_token_secret = secret_by_id(state, api_token_secret_id)
        if not api_token_secret:
            raise RuntimeError("Cloudflare API Token 秘钥不存在")
        if api_token_secret.get("type") != "cloudflare_api_token":
            raise RuntimeError("Cloudflare API Token 秘钥类型不正确")
        if not api_token_secret.get("secret"):
            raise RuntimeError("Cloudflare API Token 秘钥内容不能为空")
        env["CLOUDFLARE_API_TOKEN"] = str(api_token_secret.get("secret") or "")
    return env


def is_node_task(task):
    return str(task.get("language") or "").lower() == "node" or str(task.get("sdk") or "").lower().startswith("node")


def sdk_command_for_task(task, command):
    if is_node_task(task) and "corepack" not in command:
        return f"corepack enable && {command}"
    return command


def run_sdk_command(execution_id, task, command, src_dir, build_env):
    cache_mounts, cache_env, cache_labels = build_cache_config(task)
    effective_build_env = {**cache_env, **build_env}
    if cache_labels:
        append_log(execution_id, f"已启用构建缓存: {', '.join(cache_labels)}")
    effective_command = sdk_command_for_task(task, command)
    if effective_command != command:
        append_log(execution_id, "已为 Node 构建启用 Corepack，支持 package.json 脚本中调用 pnpm/yarn。")
    docker_src_dir = HOST_WORKSPACE_DIR / execution_id / "src"
    container_name = f"deploy-build-{safe_name(execution_id)}"
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "-v",
        f"{docker_src_dir}:/workspace",
        *cache_mounts,
        "-w",
        f"/workspace/{task.get('workdir') or '.'}",
        *docker_env_args(effective_build_env),
        builder_image(task.get("sdk")),
        "sh",
        "-lc",
        effective_command,
    ]

    def remove_build_container():
        run_command(["docker", "rm", "-f", container_name])

    return run_command_stream(docker_cmd, execution_id, on_cancel=remove_build_container)


def registry_config(state):
    settings = state.get("platformSettings") if isinstance(state.get("platformSettings"), dict) else {}
    registry_secret_id = str(settings.get("registrySecretId") or "").strip()
    registry_secret = secret_by_id(state, registry_secret_id)
    if registry_secret_id and not registry_secret:
        raise RuntimeError("平台默认镜像仓库秘钥不存在，请在秘钥管理重新选择")
    registry_url = normalize_registry_server((registry_secret or {}).get("target")) if registry_secret else normalize_registry_server(REGISTRY_URL)
    username = (registry_secret or {}).get("username") if registry_secret else REGISTRY_USERNAME
    password = (registry_secret or {}).get("secret") if registry_secret else REGISTRY_PASSWORD
    if registry_secret and registry_secret.get("type") != "registry":
        raise RuntimeError("平台默认镜像仓库秘钥类型必须是镜像仓库账号")
    if registry_secret and (not username or not password):
        raise RuntimeError("平台默认镜像仓库账号缺少用户名或秘钥内容")
    return {
        "url": registry_url,
        "username": username or "",
        "password": password or "",
        "namespace": str(settings.get("imageNamespace") or IMAGE_NAMESPACE or "deploy-platform").strip().strip("/") or "deploy-platform",
        "source": (registry_secret or {}).get("name") or ("环境变量" if registry_url else "未配置"),
    }


def image_name(task, execution_id, config=None):
    app = safe_name(task["name"])
    tag = execution_id[:12]
    config = config or {}
    namespace = str(config.get("namespace") or IMAGE_NAMESPACE or "deploy-platform").strip().strip("/")
    repo = f"{namespace}/{app}:{tag}" if namespace else f"{app}:{tag}"
    registry_url = normalize_registry_server(config.get("url") or REGISTRY_URL)
    return f"{registry_url}/{repo}" if registry_url else repo


def registry_server_from_image(image):
    first = str(image or "").split("/")[0]
    if "." in first or ":" in first or first == "localhost":
        return first
    return "https://index.docker.io/v1/"


def normalize_registry_server(value):
    value = str(value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else f"dummy://{value}")
    if parsed.netloc:
        return parsed.netloc
    return value.split("/")[0]


def dockerconfigjson_for_secret(secret, image):
    username = str((secret or {}).get("username") or "").strip()
    password = str((secret or {}).get("secret") or "")
    server = normalize_registry_server((secret or {}).get("target")) or registry_server_from_image(image)
    if not username or not password:
        raise RuntimeError("镜像拉取秘钥缺少用户名或密码")
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
    config = {"auths": {server: {"username": username, "password": password, "auth": auth}}}
    return base64.b64encode(json.dumps(config, separators=(",", ":")).encode("utf-8")).decode("utf-8")


def image_repository_path(image):
    image = str(image or "").strip()
    if not image:
        return ""
    parts = image.split("/", 1)
    if len(parts) == 2 and registry_server_from_image(image) == parts[0]:
        return parts[1]
    return image


def image_for_pull(image, pull_secret=None):
    pull_registry = normalize_registry_server((pull_secret or {}).get("target"))
    if not pull_registry:
        return image
    repo = image_repository_path(image)
    return f"{pull_registry}/{repo}" if repo else image


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


def run_command_stream(args, execution_id, cwd=None, env=None, redact=None, on_cancel=None):
    started_at = time.monotonic()
    process = subprocess.Popen(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    output = deque(maxlen=5000)
    batch = deque(maxlen=5000)
    dropped_lines = 0
    output_queue = queue.Queue()
    output_finished = object()
    last_cancel_check = 0

    def flush():
        nonlocal batch, dropped_lines
        if not batch:
            return
        text = "\n".join(batch)
        if dropped_lines:
            text = f"[deploy-platform] 前面 {dropped_lines} 行日志已省略，仅保留最后 {len(batch)} 行输出\n{text}"
        append_log(execution_id, redact(text) if redact else text)
        batch.clear()
        dropped_lines = 0

    def read_output():
        try:
            for raw_line in process.stdout or []:
                output_queue.put(raw_line)
        finally:
            output_queue.put(output_finished)

    def terminate_process():
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()

    threading.Thread(target=read_output, daemon=True).start()

    try:
        reader_finished = False
        while not reader_finished:
            try:
                raw_line = output_queue.get(timeout=0.5)
            except queue.Empty:
                raw_line = None
            if raw_line is output_finished:
                reader_finished = True
            elif raw_line is not None:
                output.append(raw_line)
                if len(batch) == batch.maxlen:
                    dropped_lines += 1
                batch.append(raw_line.rstrip("\n"))

            current_time = time.monotonic()
            if current_time - last_cancel_check >= 0.5:
                last_cancel_check = current_time
                if is_execution_cancelled(execution_id):
                    try:
                        if on_cancel:
                            on_cancel()
                    finally:
                        terminate_process()
                    raise RuntimeError("发布已取消")
        flush()
        code = process.wait()
        return code, "".join(output), time.monotonic() - started_at
    finally:
        if process.poll() is None:
            terminate_process()
        flush()
        if process.stdout:
            process.stdout.close()


def cleanup_build_artifacts(execution_id, work_dir, image=None, image_built=False):
    cleaned = []
    if CLEAN_WORKSPACE_AFTER_BUILD and work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
        cleaned.append("工作区")

    if CLEAN_LOCAL_IMAGE_AFTER_BUILD and REGISTRY_URL and image and image_built:
        code, output = run_command(["docker", "image", "rm", image])
        if code == 0:
            cleaned.append("本地镜像")
        else:
            append_log(execution_id, f"本地镜像清理失败: {output.strip() or image}")

    if DOCKER_PRUNE_AFTER_BUILD:
        code, output = run_command(["docker", "image", "prune", "-f"])
        if code == 0:
            cleaned.append("Docker dangling 镜像")
        else:
            append_log(execution_id, f"Docker dangling 镜像清理失败: {output.strip()}")

    if cleaned:
        append_log(execution_id, f"构建后清理完成: {', '.join(cleaned)}")


def secret_by_id(state, secret_id):
    if not secret_id:
        return None
    return find_by_id(state.get("secrets", []), secret_id)


def git_secret_by_id(state, secret_id):
    secret_id = str(secret_id or "").strip()
    if not secret_id:
        return None
    secret = secret_by_id(state, secret_id)
    if not secret:
        raise RuntimeError(f"Git 凭据不存在或已被删除: {secret_id}。请到秘钥管理确认已保存，并在任务的 Git 凭据下拉框重新选择。")
    if secret.get("type") not in {"git_https_token", "git_http_password", "git_ssh_key"}:
        raise RuntimeError(f"Git 凭据类型不正确: {secret.get('name') or secret_id}，请选择 Git HTTPS Token、GitLab 账号密码或 Git SSH 私钥。")
    if not secret.get("secret"):
        raise RuntimeError(f"Git 凭据 {secret.get('name') or secret_id} 缺少秘钥内容。")
    return secret


def authenticated_repo_url(repo, secret):
    if not secret or secret.get("type") not in {"git_https_token", "git_http_password"}:
        return repo
    if not re.match(r"^https?://", repo, flags=re.IGNORECASE):
        return repo
    token = secret.get("secret") or ""
    username = secret.get("username") or ("x-access-token" if secret.get("type") == "git_https_token" else "")
    if not token or "@" in repo.split("://", 1)[1].split("/", 1)[0]:
        return repo
    scheme, rest = repo.split("://", 1)
    return f"{scheme}://{quote(username, safe='')}:{quote(token, safe='')}@{rest}"


def redact_secret_text(text, secret=None):
    if not text:
        return text
    redacted = text
    if secret:
        for value in [secret.get("secret"), quote(secret.get("secret") or "", safe=""), secret.get("username"), quote(secret.get("username") or "", safe="")]:
            if value:
                redacted = redacted.replace(value, "***")
    return re.sub(r"https://[^\s/:]+:[^\s@]+@", "https://***:***@", redacted)


def git_error_message(output, secret=None):
    message = redact_secret_text((output or "").strip(), secret)
    if "could not read Username" in message:
        return "Git HTTP 仓库需要账号认证。请在秘钥管理添加 Git HTTPS Token 或 GitLab 账号密码，并在任务编辑页的 Git 凭据中选择该秘钥。"
    if "Authentication failed" in message or "HTTP Basic: Access denied" in message:
        return "Git 认证失败，请检查任务绑定的 Git HTTPS Token 或 GitLab 账号密码是否正确。"
    if "terminal prompts disabled" in message:
        return "Git 需要交互式输入账号密码，但平台不支持交互输入。请绑定 Git HTTPS Token、GitLab 账号密码或改用 Git SSH 私钥。"
    return message


def clone_environment(work_dir, secret):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
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
    secret = git_secret_by_id(state, secret_id) if secret_id else None
    work_dir = WORKSPACE_DIR / f"branch-check-{uuid.uuid4().hex[:8]}"
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    repo_url = authenticated_repo_url(repo, secret)
    env = clone_environment(work_dir, secret)
    code, output = run_command(["git", "ls-remote", "--heads", repo_url], env=env)
    shutil.rmtree(work_dir, ignore_errors=True)
    if code != 0:
        raise RuntimeError(git_error_message(output, secret) or "读取仓库分支失败")
    branches = []
    for line in output.splitlines():
        if "refs/heads/" not in line:
            continue
        branches.append(line.split("refs/heads/", 1)[1].strip())
    return sorted(set(branches))


def is_relative_child(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def normalize_artifact_pattern(pattern):
    value = str(pattern or "").strip().strip("'\"").replace("\\", "/")
    if value.startswith("/workspace/"):
        value = value[len("/workspace/") :]
    if value.startswith("./"):
        value = value[2:]
    if value.startswith("/"):
        value = value[1:]
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise RuntimeError("JAR 包路径必须是仓库内的相对路径，例如 ruoyi-admin/target/*.jar")
    return value


def normalize_static_artifact_path(value):
    value = str(value or "").strip().strip("'\"").replace("\\", "/")
    if value.startswith("/workspace/"):
        value = value[len("/workspace/") :]
    if value.startswith("./"):
        value = value[2:]
    if value.startswith("/"):
        value = value[1:]
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise RuntimeError("前端产物目录必须是仓库内的相对路径，例如 dist 或 build")
    return value


def node_static_artifact_dir(task, src_dir, app_dir):
    artifact_path = str(task.get("artifactPath") or "").strip()
    patterns = [artifact_path] if artifact_path else ["dist", "build"]
    candidates = []
    for pattern in patterns:
        normalized = normalize_static_artifact_path(pattern)
        candidates.extend([app_dir / normalized, src_dir / normalized])
    for candidate in candidates:
        if candidate.is_dir() and is_relative_child(candidate, src_dir):
            return candidate
    return None


def write_nginx_conf(context_dir, port):
    conf = context_dir / "default.conf"
    conf.write_text(
        f"""server {{
    listen {port};
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {{
        try_files $uri $uri/ /index.html;
    }}
}}
""",
        encoding="utf-8",
    )
    return conf


def java_artifact_candidates(task, src_dir, app_dir):
    artifact_path = str(task.get("artifactPath") or "").strip()
    patterns = [artifact_path] if artifact_path else ["target/*.jar", "**/target/*.jar"]
    candidates = []
    for pattern in patterns:
        if not pattern:
            continue
        pattern = normalize_artifact_pattern(pattern)
        matches = []
        if any(char in pattern for char in "*?["):
            matches.extend(src_dir.glob(pattern))
            if not artifact_path:
                matches.extend(app_dir.glob(pattern))
        else:
            matches.append(src_dir / pattern)
            if not artifact_path:
                matches.append(app_dir / pattern)
        candidates.extend(item for item in matches if item.is_file() and item.suffix == ".jar" and is_relative_child(item, src_dir))

    def sort_key(path):
        name = path.name
        classifier = name.endswith("-sources.jar") or name.endswith("-javadoc.jar") or name.endswith("-tests.jar")
        return (classifier, len(path.parts), str(path))

    return sorted(set(candidates), key=sort_key)


def dockerfile_env_value(value):
    value = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def normalize_jvm_options(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\r", " ").replace("\n", " ")).strip()


def normalize_http_path(value):
    path = str(value or "").strip()
    if not path:
        return ""
    if re.match(r"^https?://", path, flags=re.IGNORECASE):
        parsed = urlparse(path)
        path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def generate_dockerfile(task, app_dir, src_dir):
    existing = app_dir / "Dockerfile"
    language = task.get("language")
    port = int(task.get("containerPort") or 8080)
    if task_app_type(task) != "frontend" and existing.exists():
        return existing, app_dir, None

    if task_app_type(task) == "frontend":
        if language != "node":
            raise RuntimeError("前端静态站点目前仅支持 Node.js 构建")
        static_dir = node_static_artifact_dir(task, src_dir, app_dir)
        if not static_dir:
            target = task.get("artifactPath") or "dist 或 build"
            raise RuntimeError(f"未找到前端构建产物目录: {target}")
        context_dir = src_dir / ".deploy-platform-image"
        if context_dir.exists():
            shutil.rmtree(context_dir, ignore_errors=True)
        html_dir = context_dir / "html"
        shutil.copytree(static_dir, html_dir)
        write_nginx_conf(context_dir, port)
        generated = context_dir / "Dockerfile"
        generated.write_text(
            f"FROM nginx:1.27-alpine\nCOPY default.conf /etc/nginx/conf.d/default.conf\nCOPY html /usr/share/nginx/html\nEXPOSE {port}\n",
            encoding="utf-8",
        )
        return generated, context_dir, static_dir.relative_to(src_dir).as_posix()

    if language == "java":
        jars = java_artifact_candidates(task, src_dir, app_dir)
        if not jars:
            target = task.get("artifactPath") or "target/*.jar 或 **/target/*.jar"
            raise RuntimeError(f"未找到 Java 构建产物: {target}")
        context_dir = src_dir / ".deploy-platform-image"
        if context_dir.exists():
            shutil.rmtree(context_dir, ignore_errors=True)
        context_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(jars[0], context_dir / "app.jar")
        generated = context_dir / "Dockerfile"
        jvm_options = normalize_jvm_options(task.get("jvmOptions"))
        java_opts_line = f"ENV JAVA_TOOL_OPTIONS={dockerfile_env_value(jvm_options)}\n" if jvm_options else "ENV JAVA_TOOL_OPTIONS=\"\"\n"
        generated.write_text(
            f"FROM {runtime_base(task)}\nWORKDIR /app\nCOPY app.jar app.jar\n{java_opts_line}EXPOSE {port}\nENTRYPOINT [\"java\",\"-jar\",\"/app/app.jar\"]\n",
            encoding="utf-8",
        )
        return generated, context_dir, jars[0].relative_to(src_dir).as_posix()
    elif language == "golang":
        generated = app_dir / ".deploy-platform.Dockerfile"
        generated.write_text(
            f"FROM alpine:3.20\nWORKDIR /app\nCOPY . /app\nEXPOSE {port}\nCMD [\"./app\"]\n",
            encoding="utf-8",
        )
    elif language == "node":
        generated = app_dir / ".deploy-platform.Dockerfile"
        generated.write_text(
            f"FROM {runtime_base(task)}\nWORKDIR /app\nCOPY . /app\nEXPOSE {port}\nCMD [\"npm\",\"start\"]\n",
            encoding="utf-8",
        )
    elif language == "python":
        generated = app_dir / ".deploy-platform.Dockerfile"
        generated.write_text(
            f"FROM {runtime_base(task)}\nWORKDIR /app\nCOPY . /app\nEXPOSE {port}\nCMD [\"python\",\"app.py\"]\n",
            encoding="utf-8",
        )
    else:
        raise RuntimeError(f"暂不支持自动生成 {language} 运行镜像")
    return generated, src_dir if language == "java" else app_dir, jar if language == "java" else None


def create_manifest(task, target, image, pull_secret=None):
    app = safe_name(task["name"])
    namespace = safe_name(target.get("namespace") or "default")
    replicas = int(target.get("replicas") or task.get("replicas") or 1)
    container_port = int(task.get("containerPort") or 8080)
    service_port = int(task.get("servicePort") or 80)
    health_path = normalize_http_path(task.get("healthPath"))
    readiness_probe_block = ""
    if health_path:
        readiness_probe_block = f"""          readinessProbe:
            httpGet:
              path: {json.dumps(health_path, ensure_ascii=False)}
              port: {container_port}
            initialDelaySeconds: 10
            periodSeconds: 10
"""
    ingress_host = target.get("ingress") or ""
    image_pull_secret_block = ""
    runtime_env = parse_runtime_env(task.get("runtimeEnv"))
    jvm_options = normalize_jvm_options(task.get("jvmOptions"))
    env_block = ""
    if task.get("language") == "java" and jvm_options:
        runtime_env["JAVA_TOOL_OPTIONS"] = jvm_options
    if runtime_env:
        env_lines = ["          env:"]
        for key, value in runtime_env.items():
            env_lines.append(f"            - name: {key}")
            env_lines.append(f"              value: {json.dumps(value, ensure_ascii=False)}")
        env_block = "\n".join(env_lines) + "\n"
    docs = []
    if AUTO_CREATE_NAMESPACE:
        docs.append(
            f"""apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
  labels:
    managed-by: deploy-platform
"""
        )
    if pull_secret:
        secret_name = safe_name(f"{app}-{pull_secret.get('name') or 'registry'}-pull")
        dockerconfigjson = dockerconfigjson_for_secret(pull_secret, image)
        image_pull_secret_block = f"""      imagePullSecrets:
        - name: {secret_name}
"""
        docs.append(
            f"""apiVersion: v1
kind: Secret
metadata:
  name: {secret_name}
  namespace: {namespace}
  labels:
    app: {app}
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: {dockerconfigjson}
"""
        )

    docs.extend([
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
{image_pull_secret_block}      containers:
        - name: {app}
          image: {image}
          imagePullPolicy: Always
{env_block}          ports:
            - containerPort: {container_port}
{readiness_probe_block}
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
    ])
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
        if execution.get("status") == "cancelled":
            raise RuntimeError("发布已取消")
        if execution.get("status") in {"success", "partial", "failed"}:
            return
        dispatched_clusters = []
        for target in task.get("clusters", []):
            agent_task_id = uuid.uuid4().hex[:12]
            cluster_name = str(target.get("name") or "").strip()
            if not cluster_name:
                continue
            target = {**target, "name": cluster_name}
            cluster_ref = next((item for item in state.get("clusters", []) if str(item.get("name") or "").strip() == cluster_name), {})
            pull_secret_id = str(target.get("imagePullSecretId") or cluster_ref.get("imagePullSecretId") or "").strip()
            pull_secret = secret_by_id(state, pull_secret_id)
            if pull_secret_id and (not pull_secret or pull_secret.get("type") != "registry"):
                raise ValueError(f"集群 {cluster_name} 绑定的镜像拉取秘钥不存在或类型不正确")
            pull_image = image_for_pull(image, pull_secret)
            payload = {
                "appName": task["name"],
                "actor": execution.get("actor") or task.get("lastActor") or "system",
                "namespace": target.get("namespace") or "default",
                "image": pull_image,
                "pushImage": image,
                "manifest": create_manifest(task, target, pull_image, pull_secret),
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
            if pull_image != image:
                execution.setdefault("logs", []).append({"time": now_text(), "message": f"集群 {cluster_name} 使用拉取镜像地址: {pull_image}"})
            dispatched_clusters.append(cluster_name)
        if not dispatched_clusters:
            raise ValueError("任务未绑定有效部署集群")
        execution["status"] = "deploying"
        execution["image"] = image
        execution["stage"] = "Agent 部署"
        execution["progress"] = 88
        execution.setdefault("logs", []).append({"time": now_text(), "message": f"已创建 Agent 发布任务: {', '.join(dispatched_clusters)}"})
        task_ref = find_by_id(state["tasks"], task["id"])
        if task_ref and execution_is_latest_for_task(state, execution):
            task_ref["status"] = "deploying"
            task_ref["lastRun"] = now_text()
            task_ref["stage"] = "Agent 部署"
            task_ref["progress"] = 88

    mutate_state(update)


def trim_notice_text(value, limit=1800):
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...\n已截断，完整内容请到平台执行详情查看。"


def lark_field(label, value):
    return {
        "is_short": True,
        "text": {
            "tag": "lark_md",
            "content": f"**{label}**\n{trim_notice_text(value or '-', 260)}",
        },
    }


def lark_notification_card(task, event, event_label, message, event_time):
    template = {
        "BUILD_SUCCESS": "green",
        "BUILD_FAILED": "red",
        "DEPLOY_FAILED": "red",
        "HEALTH_FAILED": "orange",
    }.get(event, "blue")
    icon = {
        "BUILD_SUCCESS": "成功",
        "BUILD_FAILED": "失败",
        "DEPLOY_FAILED": "失败",
        "HEALTH_FAILED": "告警",
    }.get(event, "通知")
    clusters = task.get("clusters") or []
    if isinstance(clusters, list):
        cluster_names = [str(item.get("name") or item) if isinstance(item, dict) else str(item) for item in clusters if item]
        cluster_text = "、".join(cluster_names) or "未绑定"
    else:
        cluster_text = str(clusters or "未绑定")
    rule_label = "CF Pages" if task_deploy_rule(task) == "cf_pages" else "K8s 服务"
    app_type_label = "前端静态站点" if task_app_type(task) == "frontend" else "后端服务"
    actor = task.get("lastActor") or task.get("actor") or "system"
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": f"{icon} · {event_label} · {task.get('name') or '未命名任务'}"},
            },
            "elements": [
                {
                    "tag": "div",
                    "fields": [
                        lark_field("任务", task.get("name") or "-"),
                        lark_field("状态", event_label),
                        lark_field("部署规则", rule_label),
                        lark_field("应用类型", app_type_label),
                        lark_field("发布人", actor),
                        lark_field("环境", task.get("env") or "-"),
                        lark_field("负责人", task.get("owner") or "-"),
                        lark_field("语言 / SDK", f"{task.get('language') or '-'} / {task.get('sdk') or '-'}"),
                        lark_field("通知时间", event_time),
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "fields": [
                        lark_field("部署集群", cluster_text),
                        lark_field("服务端口", f"{task.get('servicePort') or '-'} -> {task.get('containerPort') or '-'}"),
                    ],
                },
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**仓库**\n{trim_notice_text(task.get('repo') or '-', 700)}"},
                },
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**详情摘要**\n{trim_notice_text(message, 2400) or '-'}"},
                },
                {
                    "tag": "note",
                    "elements": [{"tag": "plain_text", "content": "完整构建日志、部署回执和 Kubernetes 诊断请在平台任务详情中查看。"}],
                },
            ],
        },
    }


def sign_lark_payload(payload, secret):
    secret = str(secret or "")
    if not secret:
        return payload
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    sign = base64.b64encode(hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()).decode("utf-8")
    signed = copy.deepcopy(payload)
    signed["timestamp"] = timestamp
    signed["sign"] = sign
    return signed


def resolve_notify_channel(task, channels):
    notify = task.get("notify") if isinstance(task.get("notify"), dict) else {}
    channel_id = str(notify.get("channelId") or "").strip()
    target = str(notify.get("target") or "").strip()
    channel_key = str(notify.get("channel") or "").strip()
    if channel_id:
        channel = next((item for item in channels if str(item.get("id")) == channel_id), None)
        if channel:
            return channel
    return next(
        (
            item
            for item in channels
            if str(item.get("id")) in {target, channel_key}
            or str(item.get("name") or "") in {target, channel_key}
            or str(item.get("target") or "") in {target, channel_key}
        ),
        None,
    )


def send_notification(task, event, message, execution_id=None):
    event_labels = {
        "BUILD_SUCCESS": "发布成功",
        "BUILD_FAILED": "构建失败",
        "DEPLOY_FAILED": "部署失败",
        "HEALTH_FAILED": "健康检查失败",
    }
    event_label = event_labels.get(event, event)
    enabled_events = task.get("notify", {}).get("events") or []
    if enabled_events and event_label not in enabled_events:
        return
    channels = read_state().get("notifyChannels", [])
    target = str(task.get("notify", {}).get("target") or "").strip()
    channel = resolve_notify_channel(task, channels)
    url = (channel or {}).get("target") or target
    if not url.startswith("http"):
        if execution_id:
            append_log(execution_id, "通知发送跳过: 未配置有效通知渠道或 Webhook 地址")
        return
    event_time = now_text()
    actor = task.get("lastActor") or task.get("actor") or "system"
    text = f"【{event_label}】{task.get('name')}\n发布人: {actor}\n{message}\n时间: {event_time}"
    channel_type = (channel or {}).get("type") or "webhook"
    if channel_type == "feishu":
        payload = lark_notification_card(task, event, event_label, message, event_time)
        payload = sign_lark_payload(payload, (channel or {}).get("secret"))
    elif channel_type in {"wecom", "dingtalk"}:
        payload = {"msgtype": "text", "text": {"content": text}}
    else:
        payload = {"task": task.get("name"), "actor": actor, "event": event, "eventLabel": event_label, "message": message, "time": event_time, "text": text}
    try:
        req = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
        response_body = urlopen(req, timeout=5).read().decode("utf-8", errors="replace")
        try:
            response_json = json.loads(response_body or "{}")
        except Exception:
            response_json = {}
        error_code = response_json.get("code", response_json.get("StatusCode", 0))
        error_message = response_json.get("msg") or response_json.get("message") or response_json.get("StatusMessage") or response_body
        if error_code not in {0, "0", None, ""}:
            raise RuntimeError(f"机器人返回错误: {error_code} {error_message}")
        if execution_id:
            append_log(execution_id, f"通知已发送: {channel.get('name') if channel else url}")
        return response_body
    except Exception as exc:
        error = f"通知发送失败: {channel.get('name') if channel else url} / {exc}"
        if execution_id:
            append_log(execution_id, error)
        print(error, flush=True)
        return None


def build_and_dispatch(execution_id):
    state = read_state()
    execution = find_by_id(state["executions"], execution_id)
    if not execution:
        return
    if execution.get("status") == "cancelled":
        return
    task = find_by_id(state["tasks"], execution["taskId"])
    if not task:
        set_execution_status(execution_id, "failed", "任务不存在")
        return

    work_dir = WORKSPACE_DIR / execution_id
    src_dir = work_dir / "src"
    image = ""
    image_built = False
    image_pushed = False
    failure_event = "BUILD_FAILED"
    try:
        deploy_rule = task_deploy_rule(task)
        append_log(execution_id, f"已获得执行槽，当前平台最多同时发布 {MAX_CONCURRENT_EXECUTIONS} 个任务")
        set_execution_status(execution_id, "building", "开始拉取代码", stage="拉取代码", progress=10)
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        branch = execution.get("branch")
        if not branch:
            raise RuntimeError("未选择发布分支")
        ensure_execution_active(execution_id)
        git_secret = git_secret_by_id(state, task.get("gitCredentialId")) if task.get("gitCredentialId") else None
        clone_repo = authenticated_repo_url(task["repo"], git_secret)
        clone_env = clone_environment(work_dir, git_secret)
        clone_cmd = ["git", "clone", "--depth", "1", "--branch", branch, clone_repo, str(src_dir)]
        code, output, elapsed = run_command_stream(
            clone_cmd,
            execution_id,
            env=clone_env,
            redact=lambda text: redact_secret_text(text, git_secret),
        )
        if code != 0:
            raise RuntimeError("代码拉取失败")
        append_log(execution_id, f"代码拉取耗时: {format_duration(elapsed)}")
        ensure_execution_active(execution_id)
        set_execution_status(execution_id, "building", "代码拉取完成", stage="准备编译", progress=25)

        app_dir = (src_dir / (task.get("workdir") or ".")).resolve()
        if not app_dir.exists():
            raise RuntimeError(f"工作路径不存在: {task.get('workdir')}")

        if deploy_rule == "cf_pages":
            failure_event = "DEPLOY_FAILED"
            pages_task = {**task, "language": "node"}
            if not str(pages_task.get("sdk") or "").startswith("node"):
                pages_task["sdk"] = "node22"
            build_env = parse_build_env(task.get("buildEnv"))
            build_env.update(cloudflare_pages_env(state, task))
            if build_env:
                append_log(execution_id, f"已注入 CF Pages 部署环境变量: {', '.join(sorted(build_env.keys()))}")
            command = pages_deploy_command(pages_task)
            set_execution_status(execution_id, "deploying", f"本机执行 CF Pages 部署命令: {command}", stage="CF Pages 部署", progress=55)
            code, output, elapsed = run_sdk_command(execution_id, pages_task, command, src_dir, build_env)
            if code != 0:
                raise RuntimeError("CF Pages 部署命令执行失败")
            append_log(execution_id, f"CF Pages 部署命令耗时: {format_duration(elapsed)}")
            ensure_execution_active(execution_id)
            set_execution_status(execution_id, "success", "CF Pages 部署完成", stage="发布完成", progress=100)
            send_notification(task, "BUILD_SUCCESS", "CF Pages 部署完成", execution_id)
            return

        command = task.get("buildCommand") or ""
        if command:
            set_execution_status(execution_id, "building", f"使用 {task.get('sdk')} 执行编译命令", stage="执行编译", progress=35)
            command, fast_options = apply_maven_fast_options(command)
            if fast_options:
                append_log(execution_id, f"已启用 Maven 构建优化参数: {' '.join(fast_options)}")
            if re.search(r"(^|\s)-U(\s|$)", command):
                append_log(execution_id, "检测到 Maven -U 参数，本次会强制检查依赖更新；如依赖没有频繁更新，去掉 -U 可明显加快后续构建。")
            if "-DskipTests" in command and "maven.test.skip" not in command:
                append_log(execution_id, "检测到 -DskipTests：它只跳过测试执行，仍会编译测试代码；发布场景可考虑改用 -Dmaven.test.skip=true。")
            maven_settings_path = write_maven_settings(task, src_dir)
            if maven_settings_path:
                original_command = command
                command, injected = apply_maven_settings_to_command(command, maven_settings_path)
                append_log(
                    execution_id,
                    f"已启用 Maven 私库 {redact_url_credentials(task.get('mavenRepoUrl'))}，覆盖仓库: {task.get('mavenMirrorOf') or 'maven-public'}",
                )
                if not injected and original_command == command:
                    append_log(execution_id, f"编译命令未以 mvn/mvnw 开头，请手动追加参数: -s {maven_settings_path}")
            build_env = parse_build_env(task.get("buildEnv"))
            if build_env:
                append_log(execution_id, f"已注入构建环境变量: {', '.join(sorted(build_env.keys()))}")
            code, output, elapsed = run_sdk_command(execution_id, task, command, src_dir, build_env)
            if code != 0:
                raise RuntimeError("编译命令执行失败")
            append_log(execution_id, f"编译命令耗时: {format_duration(elapsed)}")
            ensure_execution_active(execution_id)
            set_execution_status(execution_id, "building", "编译命令执行完成", stage="生成镜像", progress=55)

        registry = registry_config(state)
        image = image_name(task, execution_id, registry)
        dockerfile, docker_context, selected_artifact = generate_dockerfile(task, app_dir, src_dir)
        ensure_execution_active(execution_id)
        if selected_artifact:
            append_log(execution_id, f"已选择构建产物: {selected_artifact}")
        if task_app_type(task) == "frontend":
            append_log(execution_id, "前端静态站点使用平台生成的 nginx 镜像，不执行项目自带 Dockerfile。")
        set_execution_status(execution_id, "building", f"开始构建镜像 {image}", stage="构建镜像", progress=65)
        code, output, elapsed = run_command_stream(["docker", "build", "-t", image, "-f", str(dockerfile), "."], execution_id, cwd=docker_context)
        if code != 0:
            raise RuntimeError("Docker 镜像构建失败")
        append_log(execution_id, f"Docker 镜像构建耗时: {format_duration(elapsed)}")
        image_built = True
        ensure_execution_active(execution_id)
        set_execution_status(execution_id, "building", "Docker 镜像构建完成", image=image, stage="准备推送", progress=72)

        if registry["url"]:
            append_log(execution_id, f"使用镜像仓库配置: {registry['source']} / {registry['url']} / {registry['namespace']}")
            registry_env = None
            if registry["username"] and registry["password"]:
                docker_config_dir = work_dir / ".docker"
                docker_config_dir.mkdir(parents=True, exist_ok=True)
                registry_env = {**os.environ, "DOCKER_CONFIG": str(docker_config_dir)}
                code, output = run_command(
                    ["docker", "login", registry["url"], "-u", registry["username"], "--password-stdin"],
                    input_text=registry["password"],
                    env=registry_env,
                )
                append_log(execution_id, output)
                if code != 0:
                    raise RuntimeError("镜像仓库登录失败")
            ensure_execution_active(execution_id)
            set_execution_status(execution_id, "building", f"推送镜像 {image}", stage="推送镜像", progress=78)
            code, output, elapsed = run_command_stream(["docker", "push", image], execution_id, env=registry_env)
            if code != 0:
                raise RuntimeError("镜像推送失败")
            append_log(execution_id, f"镜像推送耗时: {format_duration(elapsed)}")
            image_pushed = True
            ensure_execution_active(execution_id)
            set_execution_status(execution_id, "building", "镜像推送完成", image=image, stage="等待部署", progress=84)
        else:
            append_log(execution_id, "未配置 REGISTRY_URL，镜像只保留在本机 Docker，远端集群可能无法拉取。")
            set_execution_status(execution_id, "building", "镜像保留在本机 Docker", image=image, stage="等待部署", progress=84)

        if not task.get("clusters"):
            raise RuntimeError("任务未绑定部署集群")
        ensure_execution_active(execution_id)
        set_execution_status(execution_id, "building", "准备下发 Agent 发布任务", image=image, stage="等待部署", progress=86)
        dispatch_agent_tasks(execution_id, task, image)
    except Exception as exc:
        if str(exc) == "发布已取消":
            return
        latest = read_state()
        current = find_by_id(latest["executions"], execution_id) or {}
        set_execution_status(execution_id, "failed", str(exc), stage=current.get("stage") or "执行失败", progress=current.get("progress") or 100)
        send_notification(task, failure_event, str(exc), execution_id)
    finally:
        cleanup_build_artifacts(execution_id, work_dir, image, image_built and image_pushed)


def create_execution_record(state, task, actor, branch, action="触发发布"):
    execution_id = uuid.uuid4().hex[:12]
    execution_actor = actor or "system"
    execution = {
        "id": execution_id,
        "taskId": task["id"],
        "taskName": task["name"],
        "deployRule": task_deploy_rule(task),
        "branch": branch,
        "actor": execution_actor,
        "status": "queued",
        "stage": "等待执行",
        "progress": 5,
        "image": "",
        "logs": [{"time": now_text(), "message": f"执行已进入队列，发布人: {execution_actor}"}],
        "clusterResults": {},
        "createdAt": now_text(),
        "updatedAt": now_text(),
    }
    state["executions"].insert(0, execution)
    task["status"] = "queued"
    task["stage"] = "等待执行"
    task["progress"] = 5
    task["lastRun"] = now_text()
    task["lastBranch"] = branch
    task["lastActor"] = execution_actor
    state["auditLogs"].insert(
        0,
        {
            "time": now_text(),
            "actor": execution_actor,
            "action": action,
            "target": f"{task['name']} / {branch}",
            "result": "已入队",
        },
    )
    return execution


def cancel_execution(execution_id, actor):
    def update(state):
        execution = find_by_id(state["executions"], execution_id)
        if not execution:
            raise ValueError("执行记录不存在")
        if execution.get("status") not in ACTIVE_STATUSES:
            raise ValueError("当前执行状态不可取消")
        execution["status"] = "cancelled"
        execution["stage"] = "已取消"
        execution["progress"] = execution.get("progress") or 0
        execution["updatedAt"] = now_text()
        execution.setdefault("logs", []).append({"time": now_text(), "message": f"发布已由 {actor or 'system'} 取消"})
        for item in state["agentTasks"]:
            if item.get("executionId") == execution_id and item.get("status") in {"pending", "running"}:
                item["status"] = "cancelled"
                item["updatedAt"] = now_text()
        task = find_by_id(state["tasks"], execution["taskId"])
        if task:
            require_actor_asset_access(state, actor, "task.deploy", task, "取消发布")
        if task and execution_is_latest_for_task(state, execution):
            task["status"] = "cancelled"
            task["stage"] = "已取消"
            task["progress"] = execution["progress"]
            task["lastRun"] = now_text()
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "取消发布", "target": execution.get("taskName"), "result": "成功"})
        return execution

    execution, state = mutate_state(update)
    return execution, state


def recover_interrupted_executions():
    recovered = []

    def update(state):
        for execution in state.get("executions", []):
            if not is_active_status(execution.get("status")):
                continue
            execution["status"] = "failed"
            execution["stage"] = "执行中断"
            execution["progress"] = execution.get("progress") or 0
            execution["updatedAt"] = now_text()
            execution.setdefault("logs", []).append(
                {
                    "time": now_text(),
                    "message": "平台服务已重启，内存执行队列无法恢复，本次发布已自动释放，请重新发布",
                }
            )
            recovered.append(execution.get("id"))
            task = find_by_id(state.get("tasks", []), execution.get("taskId"))
            if task and execution_is_latest_for_task(state, execution):
                task["status"] = "failed"
                task["stage"] = "执行中断"
                task["progress"] = execution["progress"]
                task["lastRun"] = now_text()
        for item in state.get("agentTasks", []):
            if item.get("status") in {"pending", "running"}:
                item["status"] = "cancelled"
                item["updatedAt"] = now_text()
                item.setdefault("logs", []).append({"time": now_text(), "message": "平台服务重启，发布执行已释放"})
        if recovered:
            state["auditLogs"].insert(
                0,
                {
                    "time": now_text(),
                    "actor": "system",
                    "action": "恢复执行队列",
                    "target": f"{len(recovered)} 个执行记录",
                    "result": "已释放",
                },
            )
        return recovered

    recovered, _state = mutate_state(update, detect_changes=True)
    if recovered:
        print(f"Recovered interrupted executions: {', '.join(str(item) for item in recovered)}", flush=True)
    return recovered


def delete_task(task_id, actor):
    def update(state):
        task = find_by_id(state["tasks"], task_id)
        if not task:
            raise ValueError("任务不存在")
        require_actor_asset_access(state, actor, "task.create", task, "删除")
        active_execution = next((item for item in state["executions"] if str(item.get("taskId")) == str(task_id) and is_active_status(item.get("status"))), None)
        if active_execution:
            raise ValueError("任务正在发布中，请先取消发布")
        state["tasks"] = [item for item in state["tasks"] if str(item.get("id")) != str(task_id)]
        state["executions"] = [item for item in state["executions"] if str(item.get("taskId")) != str(task_id)]
        state["agentTasks"] = [item for item in state["agentTasks"] if str(item.get("taskId")) != str(task_id)]
        state["schedules"] = [item for item in state.setdefault("schedules", []) if str(item.get("taskId")) != str(task_id)]
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "删除任务", "target": task.get("name"), "result": "成功"})
        return task

    task, state = mutate_state(update)
    return task, state


def normalize_task_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("任务配置格式不正确")
    notify = payload.get("notify") if isinstance(payload.get("notify"), dict) else {}
    deploy_rule = task_deploy_rule(payload)
    app_type = "frontend" if deploy_rule == "cf_pages" else task_app_type(payload)
    package_manager = pages_package_manager(payload)
    clusters = payload.get("clusters") if isinstance(payload.get("clusters"), list) else []
    normalized_clusters = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        name = str(cluster.get("name") or "").strip()
        if not name:
            continue
        normalized_clusters.append({
            **cluster,
            "name": name,
            "imagePullSecretId": str(cluster.get("imagePullSecretId") or "").strip(),
            "status": cluster.get("status") or "success",
        })
    task_payload = {
        "name": str(payload.get("name") or "").strip(),
        "owner": str(payload.get("owner") or "").strip(),
        "env": str(payload.get("env") or "test").strip(),
        "tag": str(payload.get("tag") or "").strip(),
        "organizationId": str(payload.get("organizationId") or "default").strip() or "default",
        "templateId": str(payload.get("templateId") or "").strip(),
        "deployRule": deploy_rule,
        "appType": app_type,
        "repo": str(payload.get("repo") or "").strip(),
        "workdir": str(payload.get("workdir") or ".").strip() or ".",
        "artifactPath": str(payload.get("artifactPath") or "").strip(),
        "gitCredentialId": str(payload.get("gitCredentialId") or "").strip(),
        "language": str(payload.get("language") or "java").strip().lower(),
        "sdk": str(payload.get("sdk") or "").strip(),
        "buildCommand": str(payload.get("buildCommand") or "").strip(),
        "buildEnv": str(payload.get("buildEnv") or ""),
        "pagesPackageManager": package_manager,
        "pagesDeployCommand": str(payload.get("pagesDeployCommand") or "").strip() or default_pages_deploy_command(package_manager),
        "mavenRepoUrl": str(payload.get("mavenRepoUrl") or "").strip(),
        "mavenMirrorOf": str(payload.get("mavenMirrorOf") or "maven-public").strip() or "maven-public",
        "containerPort": int(payload.get("containerPort") or 8080),
        "servicePort": int(payload.get("servicePort") or 80),
        "replicas": int(payload.get("replicas") or 1),
        "healthPath": str(payload.get("healthPath") or "").strip(),
        "runtimeEnv": str(payload.get("runtimeEnv") or ""),
        "jvmOptions": normalize_jvm_options(payload.get("jvmOptions")),
        "cloudflareAccountIdSecretId": str(payload.get("cloudflareAccountIdSecretId") or "").strip(),
        "cloudflareApiTokenSecretId": str(payload.get("cloudflareApiTokenSecretId") or "").strip(),
        "clusters": normalized_clusters,
        "notify": {
            "channelId": str(notify.get("channelId") or "").strip(),
            "channel": notify.get("channel") or "企业微信",
            "target": notify.get("target") or "",
            "events": notify.get("events") if isinstance(notify.get("events"), list) else [],
        },
    }
    if app_type == "frontend":
        task_payload["language"] = "node"
        if not task_payload["sdk"] or not task_payload["sdk"].startswith("node"):
            task_payload["sdk"] = "node22"
        task_payload["mavenRepoUrl"] = ""
        task_payload["mavenMirrorOf"] = "maven-public"
        task_payload["jvmOptions"] = ""
    elif task_payload["language"] != "java":
        task_payload["artifactPath"] = ""
        task_payload["jvmOptions"] = ""
    if deploy_rule == "cf_pages":
        task_payload["clusters"] = []
        task_payload["artifactPath"] = ""
        task_payload["runtimeEnv"] = ""
        task_payload["jvmOptions"] = ""
    else:
        task_payload["cloudflareAccountIdSecretId"] = ""
        task_payload["cloudflareApiTokenSecretId"] = ""
    return task_payload


def save_task_config(task_id, payload, actor):
    task_payload = normalize_task_payload(payload)
    if not task_payload["name"]:
        raise ValueError("任务名称不能为空")
    if not task_payload["repo"]:
        raise ValueError("仓库地址不能为空")
    if task_payload["deployRule"] == "k8s" and not task_payload["buildCommand"]:
        raise ValueError("编译命令不能为空")
    if task_payload["deployRule"] == "cf_pages" and not task_payload["pagesDeployCommand"]:
        raise ValueError("CF Pages 部署命令不能为空")

    def update(state):
        actor_user = find_user(state, actor)
        if not actor_user:
            raise ValueError("操作用户不存在")
        if not user_has_permission(state, actor_user, "task.create"):
            raise ValueError("当前用户没有保存任务权限")
        if not user_can_access_asset(state, actor_user, task_payload):
            raise ValueError("当前用户组无权保存该任务")
        if task_payload.get("gitCredentialId"):
            secret = git_secret_by_id(state, task_payload.get("gitCredentialId"))
            if not user_can_access_asset(state, actor_user, secret):
                raise ValueError(f"当前用户组无权绑定该 Git 凭据: {secret.get('name')}")
        for field_name, expected_type, label in (
            ("cloudflareAccountIdSecretId", "cloudflare_account_id", "Cloudflare Account ID"),
            ("cloudflareApiTokenSecretId", "cloudflare_api_token", "Cloudflare API Token"),
        ):
            secret_id = str(task_payload.get(field_name) or "").strip()
            if not secret_id:
                continue
            secret = find_by_id(state.get("secrets", []), secret_id)
            if not secret:
                raise ValueError(f"{label} 秘钥不存在")
            if secret.get("type") != expected_type:
                raise ValueError(f"{label} 秘钥类型不正确")
            if not user_can_access_asset(state, actor_user, secret):
                raise ValueError(f"当前用户组无权绑定该 {label} 秘钥")
        notify_channel_id = (task_payload.get("notify") or {}).get("channelId")
        if notify_channel_id:
            notify_channel = find_by_id(state.get("notifyChannels", []), notify_channel_id)
            if not notify_channel:
                raise ValueError("通知渠道不存在")
            if not user_can_access_asset(state, actor_user, notify_channel):
                raise ValueError("当前用户组无权绑定该通知渠道")
        for target in task_payload.get("clusters", []):
            cluster = next((item for item in state.get("clusters", []) if item.get("name") == target.get("name")), None)
            if cluster and not user_can_access_asset(state, actor_user, cluster):
                raise ValueError(f"当前用户组无权绑定集群 {cluster.get('name')}")
            pull_secret_id = target.get("imagePullSecretId")
            if pull_secret_id:
                secret = find_by_id(state.get("secrets", []), pull_secret_id)
                if not secret:
                    raise ValueError("镜像拉取秘钥不存在")
                if not user_can_access_asset(state, actor_user, secret):
                    raise ValueError("当前用户组无权绑定该镜像拉取秘钥")
        if task_id:
            task = find_by_id(state["tasks"], task_id)
            if not task:
                raise ValueError("任务不存在")
            require_actor_asset_access(state, actor, "task.create", task, "编辑")
            task.update(task_payload)
            state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "编辑任务", "target": task.get("name"), "result": "成功"})
            return task

        task = {
            "id": int(time.time() * 1000),
            **task_payload,
            "branch": "",
            "lastBranch": "",
            "status": "pending",
            "lastRun": "草稿",
            "alerts": 0,
        }
        state["tasks"].insert(0, task)
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "创建任务", "target": task.get("name"), "result": "成功"})
        return task

    task, state = mutate_state(update)
    return task, state


def normalize_secret_payload(payload, keep_existing_secret=False):
    if not isinstance(payload, dict):
        raise ValueError("秘钥配置格式不正确")
    name = str(payload.get("name") or "").strip()
    secret_type = str(payload.get("type") or "git_https_token").strip()
    if not name:
        raise ValueError("秘钥名称不能为空")
    if secret_type not in {
        "git_https_token",
        "git_http_password",
        "git_ssh_key",
        "registry",
        "agent_token",
        "webhook",
        "cloudflare_account_id",
        "cloudflare_api_token",
    }:
        raise ValueError("秘钥类型不正确")
    normalized = {
        "name": name,
        "type": secret_type,
        "organizationId": str(payload.get("organizationId") or "default").strip() or "default",
        "target": str(payload.get("target") or "").strip(),
        "username": str(payload.get("username") or "").strip(),
        "knownHosts": str(payload.get("knownHosts") or ""),
    }
    secret_value = str(payload.get("secret") or "")
    if secret_value or not keep_existing_secret:
        normalized["secret"] = secret_value
    if not keep_existing_secret and not normalized.get("secret"):
        raise ValueError("秘钥内容不能为空")
    if secret_type == "git_http_password" and not normalized.get("username"):
        raise ValueError("GitLab 账号密码类型必须填写用户名")
    return normalized


def public_secret_config(secret):
    item = {key: copy.deepcopy(value) for key, value in (secret or {}).items() if key != "secret"}
    item["secret"] = ""
    item["hasSecret"] = bool((secret or {}).get("secret"))
    return item


def secret_name_exists(state, name, ignore_id=None):
    normalized_name = str(name or "").strip().lower()
    return any(str(item.get("name") or "").strip().lower() == normalized_name and str(item.get("id")) != str(ignore_id or "") for item in state.get("secrets", []))


def save_secret_config(secret_id, payload, actor):
    secret_payload = normalize_secret_payload(payload, keep_existing_secret=bool(secret_id))

    def update(state):
        actor_user = find_user(state, actor)
        if not actor_user:
            raise ValueError("操作用户不存在")
        if not user_has_permission(state, actor_user, "secret.manage"):
            raise ValueError("当前用户没有保存秘钥权限")
        if not user_can_access_asset(state, actor_user, secret_payload):
            raise ValueError("当前用户组无权保存该秘钥")
        if secret_name_exists(state, secret_payload["name"], secret_id):
            raise ValueError(f"秘钥名称 {secret_payload['name']} 已存在")

        if secret_id:
            secret = find_by_id(state["secrets"], secret_id)
            if not secret:
                raise ValueError("秘钥不存在或已被删除")
            require_actor_asset_access(state, actor, "secret.manage", secret, "编辑")
            secret.update(secret_payload)
            secret["updatedAt"] = now_text()
            state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "编辑秘钥", "target": f"{secret.get('name')} / {secret.get('type')}", "result": "成功"})
            return secret

        secret = {
            "id": int(time.time() * 1000),
            **secret_payload,
            "createdAt": now_text(),
        }
        state["secrets"].insert(0, secret)
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "添加秘钥", "target": f"{secret.get('name')} / {secret.get('type')}", "result": "成功"})
        return secret

    secret, state = mutate_state(update)
    return secret, state


def delete_secret_config(secret_id, actor):
    def update(state):
        secret = find_by_id(state["secrets"], secret_id)
        if not secret:
            raise ValueError("秘钥不存在或已被删除")
        require_actor_asset_access(state, actor, "secret.manage", secret, "删除")
        task_with_git = next((task for task in state.get("tasks", []) if str(task.get("gitCredentialId")) == str(secret_id)), None)
        if task_with_git:
            raise ValueError(f"任务 {task_with_git.get('name')} 正在使用该 Git 凭据，请先取消绑定")
        task_with_cloudflare_account = next((task for task in state.get("tasks", []) if str(task.get("cloudflareAccountIdSecretId")) == str(secret_id)), None)
        if task_with_cloudflare_account:
            raise ValueError(f"任务 {task_with_cloudflare_account.get('name')} 正在使用该 Cloudflare Account ID 秘钥，请先取消绑定")
        task_with_cloudflare_token = next((task for task in state.get("tasks", []) if str(task.get("cloudflareApiTokenSecretId")) == str(secret_id)), None)
        if task_with_cloudflare_token:
            raise ValueError(f"任务 {task_with_cloudflare_token.get('name')} 正在使用该 Cloudflare API Token 秘钥，请先取消绑定")
        task_with_pull_secret = next((task for task in state.get("tasks", []) if any(str(cluster.get("imagePullSecretId")) == str(secret_id) for cluster in task.get("clusters", []))), None)
        if task_with_pull_secret:
            raise ValueError(f"任务 {task_with_pull_secret.get('name')} 正在使用该镜像拉取秘钥，请先取消绑定")
        cluster_with_secret = next((cluster for cluster in state.get("clusters", []) if str(cluster.get("imagePullSecretId")) == str(secret_id)), None)
        if cluster_with_secret:
            raise ValueError(f"集群 {cluster_with_secret.get('name')} 正在使用该默认镜像拉取秘钥，请先取消绑定")
        settings = state.get("platformSettings") if isinstance(state.get("platformSettings"), dict) else {}
        if str(settings.get("registrySecretId")) == str(secret_id):
            raise ValueError("平台默认推送镜像仓库正在使用该秘钥，请先切换仓库配置")
        state["secrets"] = [item for item in state.get("secrets", []) if str(item.get("id")) != str(secret_id)]
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "删除秘钥", "target": f"{secret.get('name')} / {secret.get('type')}", "result": "成功"})
        return secret

    secret, state = mutate_state(update)
    return secret, state


def create_execution(task_id, actor, branch):
    def update(state):
        task = find_by_id(state["tasks"], task_id)
        if not task:
            raise ValueError("任务不存在")
        require_actor_asset_access(state, actor, "task.deploy", task, "发布")
        if not branch:
            raise ValueError("请选择发布分支")
        active = active_execution_for_task(state, task["id"])
        if active:
            raise ValueError(f"任务正在执行中: {active.get('id')} / {active.get('stage') or active.get('status')}")
        return create_execution_record(state, task, actor, branch)

    execution, state = mutate_state(update)
    BUILD_EXECUTOR.submit(build_and_dispatch, execution["id"])
    return execution, state


def create_batch_executions(items, actor):
    if not isinstance(items, list) or not items:
        raise ValueError("请选择要批量发布的任务")
    if len(items) > MAX_CONCURRENT_EXECUTIONS:
        raise ValueError(f"批量发布一次最多选择 {MAX_CONCURRENT_EXECUTIONS} 个任务")

    def update(state):
        executions = []
        for item in items:
            task = find_by_id(state["tasks"], item.get("taskId"))
            if not task:
                raise ValueError(f"任务不存在: {item.get('taskId')}")
            require_actor_asset_access(state, actor, "task.deploy", task, "发布")
            branch = item.get("branch")
            if not branch:
                raise ValueError(f"{task['name']} 未选择发布分支")
            active = active_execution_for_task(state, task["id"])
            if active:
                raise ValueError(f"任务 {task['name']} 正在执行中: {active.get('id')}")
            executions.append(create_execution_record(state, task, actor, branch, "批量发布"))
        return executions

    executions, state = mutate_state(update)
    for execution in executions:
        BUILD_EXECUTOR.submit(build_and_dispatch, execution["id"])
    return executions, state


def schedule_execution(task_id, actor, branch, scheduled_at):
    run_at = parse_schedule_time(scheduled_at)
    if run_at <= datetime.now():
        raise ValueError("定时发布时间必须晚于当前时间")

    def update(state):
        task = find_by_id(state["tasks"], task_id)
        if not task:
            raise ValueError("任务不存在")
        require_actor_asset_access(state, actor, "task.deploy", task, "定时发布")
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
        task = find_by_id(state["tasks"], schedule["taskId"])
        if task:
            require_actor_asset_access(state, actor, "task.deploy", task, "取消定时发布")
        schedule["status"] = "cancelled"
        schedule["enabled"] = False
        schedule["updatedAt"] = now_text()
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
                active = active_execution_for_task(state, task["id"])
                if active:
                    raise RuntimeError(f"任务已有执行中的发布: {active.get('id')}")
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
        BUILD_EXECUTOR.submit(build_and_dispatch, execution_id)


def execution_has_agent_tasks(state, execution_id):
    return any(str(item.get("executionId")) == str(execution_id) for item in state.get("agentTasks", []))


def recover_waiting_deployments():
    recovery_items = []
    with STATE_LOCK:
        state = read_state()
        now = datetime.now()
        for execution in state.get("executions", []):
            if execution.get("status") != "building":
                continue
            if execution.get("stage") != "等待部署" or int(execution.get("progress") or 0) < 84:
                continue
            if not execution.get("image") or execution_has_agent_tasks(state, execution.get("id")):
                continue
            updated_at = parse_time_text(execution.get("updatedAt") or execution.get("createdAt"))
            if updated_at and (now - updated_at).total_seconds() < WAITING_DEPLOY_RECOVERY_SECONDS:
                continue
            task = find_by_id(state.get("tasks", []), execution.get("taskId"))
            if task:
                recovery_items.append((execution.get("id"), copy.deepcopy(task), execution.get("image")))

    for execution_id, task, image in recovery_items:
        try:
            append_log(execution_id, "检测到等待部署阶段未创建 Agent 任务，自动补发")
            dispatch_agent_tasks(execution_id, task, image)
        except Exception as exc:
            set_execution_status(execution_id, "failed", f"Agent 发布任务补发失败: {exc}", stage="部署异常", progress=100)


def scheduler_loop():
    while True:
        try:
            trigger_due_schedules()
            recover_waiting_deployments()
        except Exception as exc:
            print(f"schedule loop error: {exc}", flush=True)
        time.sleep(15)


def mark_agent_task_running(agent_task, agent_instance=None):
    def update(state):
        item = find_by_id(state["agentTasks"], agent_task["id"])
        if not item or item.get("status") not in {"pending", "running"}:
            return
        item["status"] = "running"
        item["updatedAt"] = now_text()
        if agent_instance:
            item["assignedAgent"] = agent_instance
        execution = find_by_id(state["executions"], agent_task["executionId"])
        if execution and execution.get("status") in ACTIVE_STATUSES:
            execution.setdefault("clusterResults", {})[agent_task["clusterName"]] = "running"

    mutate_state(update, detect_changes=True)


def agent_task_matches_cluster(item, cluster):
    return str(item.get("clusterName") or "").strip() == cluster


def agent_task_is_stale(item):
    updated_at = parse_time_text(item.get("updatedAt") or item.get("createdAt"))
    if not updated_at:
        return True
    return (datetime.now() - updated_at).total_seconds() >= AGENT_TASK_RETRY_SECONDS


def agent_task_can_be_taken_over(item):
    if item.get("assignedAgent"):
        return False
    updated_at = parse_time_text(item.get("updatedAt") or item.get("createdAt"))
    if not updated_at:
        return True
    return (datetime.now() - updated_at).total_seconds() >= AGENT_TASK_TAKEOVER_SECONDS


def next_agent_task_for_cluster(agent_tasks, cluster):
    pending = next((item for item in agent_tasks if agent_task_matches_cluster(item, cluster) and item.get("status") == "pending"), None)
    if pending:
        return pending
    return next((item for item in agent_tasks if agent_task_matches_cluster(item, cluster) and item.get("status") == "running" and (agent_task_is_stale(item) or agent_task_can_be_taken_over(item))), None)


def update_agent_result(agent_task_id, status, logs, agent_instance=None):
    notification = None
    status = status if status in {"success", "failed"} else "failed"

    def update(state):
        nonlocal notification
        item = find_by_id(state["agentTasks"], agent_task_id)
        if not item:
            return None
        assigned_agent = item.get("assignedAgent")
        if assigned_agent and agent_instance and assigned_agent != agent_instance:
            item.setdefault("logs", []).append({"time": now_text(), "message": f"忽略非当前 Agent 实例回报: {agent_instance}"})
            return item
        if item.get("status") in {"success", "failed", "cancelled"}:
            return item
        item["status"] = status
        item["updatedAt"] = now_text()
        if logs:
            item.setdefault("logs", []).append({"time": now_text(), "message": logs})
        execution = find_by_id(state["executions"], item["executionId"])
        if execution:
            if execution.get("status") == "cancelled":
                return item
            execution.setdefault("clusterResults", {})[item["clusterName"]] = status
            statuses = list(execution["clusterResults"].values())
            if statuses and all(value == "success" for value in statuses):
                execution["status"] = "success"
                execution["stage"] = "发布完成"
                execution["progress"] = 100
                task = find_by_id(state["tasks"], execution["taskId"])
                if task and execution_is_latest_for_task(state, execution):
                    task["status"] = "success"
                    task["stage"] = "发布完成"
                    task["progress"] = 100
                    notification = (copy.deepcopy(task), "BUILD_SUCCESS", f"集群 {item['clusterName']} 部署完成", execution.get("id"))
            elif any(value == "failed" for value in statuses):
                execution["status"] = "partial" if any(value == "success" for value in statuses) else "failed"
                execution["stage"] = "部署异常"
                execution["progress"] = 100 if execution["status"] == "partial" else max(90, int(execution.get("progress") or 90))
                task = find_by_id(state["tasks"], execution["taskId"])
                if task and execution_is_latest_for_task(state, execution):
                    task["status"] = execution["status"]
                    task["stage"] = execution["stage"]
                    task["progress"] = execution["progress"]
                    notification = (copy.deepcopy(task), "DEPLOY_FAILED", logs or f"集群 {item['clusterName']} 部署失败", execution.get("id"))
        return item

    item, state = mutate_state(update)
    if notification:
        send_notification(*notification)
    return item, state


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def is_spa_route(self, parsed):
        if parsed.path.startswith("/api/"):
            return False
        name = Path(parsed.path).name
        return "." not in name

    def serve_index(self):
        index_path = APP_DIR / "index.html"
        try:
            data = index_path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        parsed = urlparse(self.path)
        no_cache_exts = (".html", ".js", ".css")
        if parsed.path.startswith("/api/") or parsed.path == "/" or self.is_spa_route(parsed) or parsed.path.endswith(no_cache_exts):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=60")
        super().end_headers()

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def cookie_value(self, name):
        cookies = self.headers.get("Cookie", "")
        for item in cookies.split(";"):
            if "=" not in item:
                continue
            key, value = item.strip().split("=", 1)
            if key == name:
                return value
        return ""

    def require_agent_token(self, parsed):
        query = parse_qs(parsed.query)
        token = query.get("token", [""])[0] or self.headers.get("X-Agent-Token", "")
        if AGENT_SHARED_TOKEN and token != AGENT_SHARED_TOKEN:
            self.send_json({"error": "unauthorized"}, status=401)
            return False
        return True

    def require_session_user(self):
        try:
            user, _ = session_user(self.cookie_value("deploy_platform_session"))
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=401)
            return None
        return user

    def websocket_handshake(self):
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400)
            return False
        accept = websocket_accept_key(key)
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        return True

    def websocket_send(self, payload):
        frame = websocket_frame(payload)
        self.connection.sendall(frame)

    def websocket_serve(self, initial_state):
        if not self.websocket_handshake():
            return
        with WS_LOCK:
            WS_CLIENTS.add(self)
        try:
            self.websocket_send({"type": "state", "state": client_state(initial_state, compact=True)})
            self.connection.settimeout(1.0)
            while True:
                try:
                    chunk = read_exact(self.connection, 2)
                    if not chunk:
                        break
                    opcode = chunk[0] & 0x0F
                    length = chunk[1] & 0x7F
                    if length == 126:
                        length = int.from_bytes(read_exact(self.connection, 2), "big")
                    elif length == 127:
                        length = int.from_bytes(read_exact(self.connection, 8), "big")
                    masked = bool(chunk[1] & 0x80)
                    mask = read_exact(self.connection, 4) if masked else b""
                    payload = bytearray(read_exact(self.connection, length)) if length else bytearray()
                    if masked and payload:
                        for i in range(len(payload)):
                            payload[i] ^= mask[i % 4]
                    if opcode == 0x8:
                        break
                except socket.timeout:
                    continue
        finally:
            with WS_LOCK:
                WS_CLIENTS.discard(self)
            try:
                self.connection.close()
            except Exception:
                pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"status": "ok", "database": "postgres" if use_postgres() else "sqlite"})
            return
        if parsed.path == "/api/ws":
            user = self.require_session_user()
            if not user:
                return
            state = read_state()
            self.websocket_serve(state)
            return
        if parsed.path == "/api/state":
            state = read_state()
            query = parse_qs(parsed.query)
            compact = query.get("compact", [""])[0] in {"1", "true", "yes"}
            requested_revision = query.get("revision", [""])[0]
            if requested_revision and requested_revision == str(state.get("revision") or 0):
                self.send_json({"unchanged": True, "revision": state.get("revision") or 0})
                return
            self.send_json(client_state(state, compact=compact))
            return
        match = re.match(r"^/api/executions/([^/]+)/logs$", parsed.path)
        if match:
            state = read_state()
            execution = find_by_id(state.get("executions", []), match.group(1))
            if not execution:
                self.send_json({"error": "执行记录不存在"}, status=404)
                return
            self.send_json({"executionId": execution.get("id"), "logs": execution.get("logs") or []})
            return
        if parsed.path == "/api/agent/tasks":
            query = parse_qs(parsed.query)
            cluster = query.get("cluster", [""])[0].strip()
            agent_instance = query.get("instanceId", [""])[0].strip()
            if not self.require_agent_token(parsed):
                return
            state = read_state()
            task = next_agent_task_for_cluster(state["agentTasks"], cluster)
            if not task:
                self.send_json({"task": None})
                return
            mark_agent_task_running(task, agent_instance)
            self.send_json({"task": task})
            return
        if self.is_spa_route(parsed):
            self.serve_index()
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/login":
            body = self.read_json_body()
            try:
                user, state = authenticate_user(body.get("username"), body.get("password"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=401)
                return
            cookie = f"deploy_platform_session={user.get('token')}; Path=/; SameSite=Lax; HttpOnly"
            self.send_json({"user": user}, headers={"Set-Cookie": cookie})
            return
        if parsed.path == "/api/auth/session":
            body = self.read_json_body()
            try:
                user, state = session_user(body.get("token") or self.cookie_value("deploy_platform_session"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=401)
                return
            cookie = f"deploy_platform_session={user.get('token')}; Path=/; SameSite=Lax; HttpOnly"
            self.send_json({"user": user}, headers={"Set-Cookie": cookie})
            return
        if parsed.path == "/api/auth/logout":
            self.send_json({"ok": True}, headers={"Set-Cookie": "deploy_platform_session=; Path=/; Max-Age=0; SameSite=Lax; HttpOnly"})
            return
        if parsed.path == "/api/tasks":
            body = self.read_json_body()
            try:
                task, state = save_task_config(None, body.get("task") or {}, body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"task": task, "state": client_state(state, compact=True)})
            return
        if parsed.path == "/api/secrets":
            body = self.read_json_body()
            try:
                secret, state = save_secret_config(None, body.get("secret") or {}, body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"secret": public_secret_config(secret), "state": client_state(state, compact=True)})
            return
        if parsed.path == "/api/tasks/batch-run":
            body = self.read_json_body()
            try:
                executions, state = create_batch_executions(body.get("items"), body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"executions": executions, "state": client_state(state, compact=True)})
            return
        match = re.match(r"^/api/tasks/([^/]+)/run$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                execution, state = create_execution(match.group(1), body.get("actor"), body.get("branch"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"execution": execution, "state": client_state(state, compact=True)})
            return
        match = re.match(r"^/api/tasks/([^/]+)/schedule$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                schedule, state = schedule_execution(match.group(1), body.get("actor"), body.get("branch"), body.get("scheduledAt"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"schedule": schedule, "state": client_state(state, compact=True)})
            return
        match = re.match(r"^/api/executions/([^/]+)/cancel$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                execution, state = cancel_execution(match.group(1), body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"execution": execution, "state": client_state(state, compact=True)})
            return
        match = re.match(r"^/api/schedules/([^/]+)/cancel$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                schedule, state = cancel_schedule(match.group(1), body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"schedule": schedule, "state": client_state(state, compact=True)})
            return
        if parsed.path == "/api/repositories/branches":
            body = self.read_json_body()
            try:
                state = read_state()
                secret_id = body.get("gitCredentialId")
                if secret_id:
                    secret = find_by_id(state.get("secrets", []), secret_id)
                    if secret:
                        require_actor_asset_access(state, body.get("actor"), "secret.view", secret, "读取")
                branches = list_repository_branches(body.get("repo") or "", body.get("gitCredentialId"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"branches": branches})
            return
        match = re.match(r"^/api/agent/tasks/([^/]+)/result$", parsed.path)
        if match:
            if not self.require_agent_token(parsed):
                return
            body = self.read_json_body()
            item, state = update_agent_result(match.group(1), body.get("status") or "failed", body.get("logs") or "", body.get("instanceId") or "")
            self.send_json({"ok": True, "task": {"id": match.group(1), "status": (item or {}).get("status")}})
            return
        if parsed.path == "/api/agent/heartbeat":
            if not self.require_agent_token(parsed):
                return
            body = self.read_json_body()

            def update(state):
                cluster = body.get("cluster")
                state["agentHeartbeats"] = [item for item in state["agentHeartbeats"] if item.get("cluster") != cluster]
                state["agentHeartbeats"].append({"cluster": cluster, "time": now_text(), "version": body.get("version", "dev"), "instanceId": body.get("instanceId") or ""})

            mutate_state(update)
            self.send_json({"ok": True})
            return
        self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        match = re.match(r"^/api/tasks/([^/]+)$", parsed.path)
        if match:
            query = parse_qs(parsed.query)
            try:
                task, state = delete_task(match.group(1), query.get("actor", ["system"])[0])
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"task": task, "state": client_state(state, compact=True)})
            return
        match = re.match(r"^/api/secrets/([^/]+)$", parsed.path)
        if match:
            query = parse_qs(parsed.query)
            try:
                secret, state = delete_secret_config(match.group(1), query.get("actor", ["system"])[0])
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"secret": public_secret_config(secret), "state": client_state(state, compact=True)})
            return
        self.send_error(404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        match = re.match(r"^/api/tasks/([^/]+)$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                task, state = save_task_config(match.group(1), body.get("task") or {}, body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"task": task, "state": client_state(state, compact=True)})
            return
        match = re.match(r"^/api/secrets/([^/]+)$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                secret, state = save_secret_config(match.group(1), body.get("secret") or {}, body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"secret": public_secret_config(secret), "state": client_state(state, compact=True)})
            return
        if parsed.path != "/api/state":
            self.send_error(404)
            return
        try:
            body = self.read_json_body()
            if isinstance(body, dict) and "state" in body:
                actor = body.get("actor")
                state = body.get("state") or {}
            else:
                actor = None
                state = body
            with STATE_LOCK:
                current_state = read_state()
                state = merge_defaults(state)
                state = preserve_existing_user_passwords(state, current_state)
                state = preserve_sensitive_values(state, current_state)
                state = preserve_runtime_fields(state, current_state)
                validate_state_update(state, actor, current_state=current_state)
                write_state(state, current_state=current_state)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json({"ok": True, "state": client_state(state, compact=True)})

    def send_json(self, payload, status=200, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    read_state()
    atexit.register(flush_pending_state_write)
    threading.Thread(target=state_writer_loop, daemon=True).start()
    recover_interrupted_executions()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "80"))
    db_kind = "postgres" if use_postgres() else f"sqlite={DB_PATH}"
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Deploy Platform listening on :{port}, {db_kind}", flush=True)
    server.serve_forever()
