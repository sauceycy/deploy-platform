import base64
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
AGENT_SHARED_TOKEN = os.environ.get("AGENT_SHARED_TOKEN", "dev-agent-token")
AGENT_TASK_RETRY_SECONDS = int(os.environ.get("AGENT_TASK_RETRY_SECONDS", "300"))
ACTIVE_STATUSES = {"queued", "building", "deploying", "running"}
CLEAN_WORKSPACE_AFTER_BUILD = os.environ.get("CLEAN_WORKSPACE_AFTER_BUILD", "true").lower() != "false"
CLEAN_LOCAL_IMAGE_AFTER_BUILD = os.environ.get("CLEAN_LOCAL_IMAGE_AFTER_BUILD", "true").lower() != "false"
DOCKER_PRUNE_AFTER_BUILD = os.environ.get("DOCKER_PRUNE_AFTER_BUILD", "false").lower() == "true"

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
    "platformSettings": {
        "registrySecretId": "",
        "imageNamespace": IMAGE_NAMESPACE,
    },
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


def parse_time_text(value):
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


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


def set_execution_status(execution_id, status, message=None, image=None, stage=None, progress=None):
    def update(state):
        execution = find_by_id(state["executions"], execution_id)
        if not execution:
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
        if task:
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


def parse_build_env(value):
    env = {}
    for line_number, raw_line in enumerate(str(value or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise RuntimeError(f"构建环境变量第 {line_number} 行格式错误，应为 KEY=VALUE")
        key, item_value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise RuntimeError(f"构建环境变量名不合法: {key}")
        env[key] = item_value
    return env


def docker_env_args(env):
    args = []
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    return args


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


def generate_dockerfile(task, app_dir, src_dir):
    existing = app_dir / "Dockerfile"
    if existing.exists():
        return existing, app_dir, None

    language = task.get("language")
    port = int(task.get("containerPort") or 8080)
    if language == "java":
        generated = src_dir / ".deploy-platform.Dockerfile"
        jars = java_artifact_candidates(task, src_dir, app_dir)
        if not jars:
            target = task.get("artifactPath") or "target/*.jar 或 **/target/*.jar"
            raise RuntimeError(f"未找到 Java 构建产物: {target}")
        jar = jars[0].relative_to(src_dir).as_posix()
        generated.write_text(
            f"FROM {runtime_base(task)}\nWORKDIR /app\nCOPY {jar} app.jar\nEXPOSE {port}\nENTRYPOINT [\"java\",\"-jar\",\"/app/app.jar\"]\n",
            encoding="utf-8",
        )
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
    namespace = target.get("namespace") or "default"
    replicas = int(target.get("replicas") or task.get("replicas") or 1)
    container_port = int(task.get("containerPort") or 8080)
    service_port = int(task.get("servicePort") or 80)
    health_path = task.get("healthPath") or "/"
    ingress_host = target.get("ingress") or ""
    image_pull_secret_block = ""
    docs = []
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
            payload = {
                "appName": task["name"],
                "namespace": target.get("namespace") or "default",
                "image": image,
                "manifest": create_manifest(task, target, image, pull_secret),
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
            dispatched_clusters.append(cluster_name)
        if not dispatched_clusters:
            raise ValueError("任务未绑定有效部署集群")
        execution["status"] = "deploying"
        execution["image"] = image
        execution["stage"] = "Agent 部署"
        execution["progress"] = 88
        execution.setdefault("logs", []).append({"time": now_text(), "message": f"已创建 Agent 发布任务: {', '.join(dispatched_clusters)}"})
        task_ref = find_by_id(state["tasks"], task["id"])
        if task_ref:
            task_ref["status"] = "deploying"
            task_ref["lastRun"] = now_text()
            task_ref["stage"] = "Agent 部署"
            task_ref["progress"] = 88

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
    image = ""
    image_built = False
    image_pushed = False
    try:
        registry = registry_config(state)
        set_execution_status(execution_id, "building", "开始拉取代码", stage="拉取代码", progress=10)
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        branch = execution.get("branch")
        if not branch:
            raise RuntimeError("未选择发布分支")
        ensure_execution_active(execution_id)
        git_secret = secret_by_id(state, task.get("gitCredentialId"))
        clone_repo = authenticated_repo_url(task["repo"], git_secret)
        clone_env = clone_environment(work_dir, git_secret)
        clone_cmd = ["git", "clone", "--depth", "1", "--branch", branch, clone_repo, str(src_dir)]
        code, output = run_command(clone_cmd, env=clone_env)
        append_log(execution_id, redact_secret_text(output, git_secret))
        if code != 0:
            raise RuntimeError("代码拉取失败")
        ensure_execution_active(execution_id)
        set_execution_status(execution_id, "building", "代码拉取完成", stage="准备编译", progress=25)

        app_dir = (src_dir / (task.get("workdir") or ".")).resolve()
        if not app_dir.exists():
            raise RuntimeError(f"工作路径不存在: {task.get('workdir')}")

        command = task.get("buildCommand") or ""
        if command:
            set_execution_status(execution_id, "building", f"使用 {task.get('sdk')} 执行编译命令", stage="执行编译", progress=35)
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
            docker_src_dir = HOST_WORKSPACE_DIR / execution_id / "src"
            docker_cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{docker_src_dir}:/workspace",
                "-w",
                f"/workspace/{task.get('workdir') or '.'}",
                *docker_env_args(build_env),
                builder_image(task.get("sdk")),
                "sh",
                "-lc",
                command,
            ]
            code, output = run_command(docker_cmd)
            append_log(execution_id, output)
            if code != 0:
                raise RuntimeError("编译命令执行失败")
            ensure_execution_active(execution_id)
            set_execution_status(execution_id, "building", "编译命令执行完成", stage="生成镜像", progress=55)

        image = image_name(task, execution_id, registry)
        dockerfile, docker_context, selected_artifact = generate_dockerfile(task, app_dir, src_dir)
        ensure_execution_active(execution_id)
        if selected_artifact:
            append_log(execution_id, f"已选择 Java 制品: {selected_artifact}")
        set_execution_status(execution_id, "building", f"开始构建镜像 {image}", stage="构建镜像", progress=65)
        code, output = run_command(["docker", "build", "-t", image, "-f", str(dockerfile), "."], cwd=docker_context)
        append_log(execution_id, output)
        if code != 0:
            raise RuntimeError("Docker 镜像构建失败")
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
            code, output = run_command(["docker", "push", image], env=registry_env)
            append_log(execution_id, output)
            if code != 0:
                raise RuntimeError("镜像推送失败")
            image_pushed = True
            ensure_execution_active(execution_id)
            set_execution_status(execution_id, "building", "镜像推送完成", image=image, stage="等待部署", progress=84)
        else:
            append_log(execution_id, "未配置 REGISTRY_URL，镜像只保留在本机 Docker，远端集群可能无法拉取。")
            set_execution_status(execution_id, "building", "镜像保留在本机 Docker", image=image, stage="等待部署", progress=84)

        if not task.get("clusters"):
            raise RuntimeError("任务未绑定部署集群")
        ensure_execution_active(execution_id)
        dispatch_agent_tasks(execution_id, task, image)
        send_notification(task, "BUILD_SUCCESS", f"镜像已构建: {image}")
    except Exception as exc:
        if str(exc) == "发布已取消":
            return
        latest = read_state()
        current = find_by_id(latest["executions"], execution_id) or {}
        set_execution_status(execution_id, "failed", str(exc), stage=current.get("stage") or "执行失败", progress=current.get("progress") or 100)
        send_notification(task, "BUILD_FAILED", str(exc))
    finally:
        cleanup_build_artifacts(execution_id, work_dir, image, image_built and image_pushed)


def create_execution_record(state, task, actor, branch, action="触发发布"):
    execution_id = uuid.uuid4().hex[:12]
    execution = {
        "id": execution_id,
        "taskId": task["id"],
        "taskName": task["name"],
        "branch": branch,
        "actor": actor or "system",
        "status": "queued",
        "stage": "等待执行",
        "progress": 5,
        "image": "",
        "logs": [{"time": now_text(), "message": "执行已进入队列"}],
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
            task["status"] = "cancelled"
            task["stage"] = "已取消"
            task["progress"] = execution["progress"]
            task["lastRun"] = now_text()
        state["auditLogs"].insert(0, {"time": now_text(), "actor": actor or "system", "action": "取消发布", "target": execution.get("taskName"), "result": "成功"})
        return execution

    execution, state = mutate_state(update)
    return execution, state


def delete_task(task_id, actor):
    def update(state):
        task = find_by_id(state["tasks"], task_id)
        if not task:
            raise ValueError("任务不存在")
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
    return {
        "name": str(payload.get("name") or "").strip(),
        "owner": str(payload.get("owner") or "").strip(),
        "env": str(payload.get("env") or "test").strip(),
        "tag": str(payload.get("tag") or "").strip(),
        "repo": str(payload.get("repo") or "").strip(),
        "workdir": str(payload.get("workdir") or ".").strip() or ".",
        "artifactPath": str(payload.get("artifactPath") or "").strip(),
        "gitCredentialId": str(payload.get("gitCredentialId") or "").strip(),
        "language": str(payload.get("language") or "java").strip(),
        "sdk": str(payload.get("sdk") or "").strip(),
        "buildCommand": str(payload.get("buildCommand") or "").strip(),
        "buildEnv": str(payload.get("buildEnv") or ""),
        "mavenRepoUrl": str(payload.get("mavenRepoUrl") or "").strip(),
        "mavenMirrorOf": str(payload.get("mavenMirrorOf") or "maven-public").strip() or "maven-public",
        "containerPort": int(payload.get("containerPort") or 8080),
        "servicePort": int(payload.get("servicePort") or 80),
        "replicas": int(payload.get("replicas") or 1),
        "healthPath": str(payload.get("healthPath") or "").strip(),
        "clusters": normalized_clusters,
        "notify": {
            "channel": notify.get("channel") or "企业微信",
            "target": notify.get("target") or "",
            "events": notify.get("events") if isinstance(notify.get("events"), list) else [],
        },
    }


def save_task_config(task_id, payload, actor):
    task_payload = normalize_task_payload(payload)
    if not task_payload["name"]:
        raise ValueError("任务名称不能为空")
    if not task_payload["repo"]:
        raise ValueError("仓库地址不能为空")
    if not task_payload["buildCommand"]:
        raise ValueError("编译命令不能为空")

    def update(state):
        if task_id:
            task = find_by_id(state["tasks"], task_id)
            if not task:
                raise ValueError("任务不存在")
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
        if item and item["status"] in {"pending", "running"}:
            item["status"] = "running"
            item["updatedAt"] = now_text()
        execution = find_by_id(state["executions"], agent_task["executionId"])
        if execution:
            execution.setdefault("clusterResults", {})[agent_task["clusterName"]] = "running"

    mutate_state(update)


def agent_task_matches_cluster(item, cluster):
    return str(item.get("clusterName") or "").strip() == cluster


def agent_task_is_stale(item):
    updated_at = parse_time_text(item.get("updatedAt") or item.get("createdAt"))
    if not updated_at:
        return True
    return (datetime.now() - updated_at).total_seconds() >= AGENT_TASK_RETRY_SECONDS


def next_agent_task_for_cluster(agent_tasks, cluster):
    pending = next((item for item in agent_tasks if agent_task_matches_cluster(item, cluster) and item.get("status") == "pending"), None)
    if pending:
        return pending
    return next((item for item in agent_tasks if agent_task_matches_cluster(item, cluster) and item.get("status") == "running" and agent_task_is_stale(item)), None)


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
            if execution.get("status") == "cancelled":
                return item
            execution.setdefault("clusterResults", {})[item["clusterName"]] = status
            statuses = list(execution["clusterResults"].values())
            if statuses and all(value == "success" for value in statuses):
                execution["status"] = "success"
                execution["stage"] = "发布完成"
                execution["progress"] = 100
                task = find_by_id(state["tasks"], execution["taskId"])
                if task:
                    task["status"] = "success"
                    task["stage"] = "发布完成"
                    task["progress"] = 100
            elif any(value == "failed" for value in statuses):
                execution["status"] = "partial" if any(value == "success" for value in statuses) else "failed"
                execution["stage"] = "部署异常"
                execution["progress"] = 100 if execution["status"] == "partial" else max(90, int(execution.get("progress") or 90))
                task = find_by_id(state["tasks"], execution["taskId"])
                if task:
                    task["status"] = execution["status"]
                    task["stage"] = execution["stage"]
                    task["progress"] = execution["progress"]
        return item

    item, state = mutate_state(update)
    return item, state


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def end_headers(self):
        parsed = urlparse(self.path)
        no_cache_exts = (".html", ".js", ".css")
        if parsed.path.startswith("/api/") or parsed.path == "/" or parsed.path.endswith(no_cache_exts):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=60")
        super().end_headers()

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def require_agent_token(self, parsed):
        query = parse_qs(parsed.query)
        token = query.get("token", [""])[0] or self.headers.get("X-Agent-Token", "")
        if AGENT_SHARED_TOKEN and token != AGENT_SHARED_TOKEN:
            self.send_json({"error": "unauthorized"}, status=401)
            return False
        return True

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
            cluster = query.get("cluster", [""])[0].strip()
            if not self.require_agent_token(parsed):
                return
            state = read_state()
            task = next_agent_task_for_cluster(state["agentTasks"], cluster)
            if not task:
                self.send_json({"task": None})
                return
            mark_agent_task_running(task)
            self.send_json({"task": task})
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/tasks":
            body = self.read_json_body()
            try:
                task, state = save_task_config(None, body.get("task") or {}, body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"task": task, "state": state})
            return
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
        match = re.match(r"^/api/executions/([^/]+)/cancel$", parsed.path)
        if match:
            body = self.read_json_body()
            try:
                execution, state = cancel_execution(match.group(1), body.get("actor"))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_json({"execution": execution, "state": state})
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
            if not self.require_agent_token(parsed):
                return
            body = self.read_json_body()
            item, state = update_agent_result(match.group(1), body.get("status") or "failed", body.get("logs") or "")
            self.send_json({"ok": True, "task": {"id": match.group(1), "status": (item or {}).get("status")}})
            return
        if parsed.path == "/api/agent/heartbeat":
            if not self.require_agent_token(parsed):
                return
            body = self.read_json_body()

            def update(state):
                cluster = body.get("cluster")
                state["agentHeartbeats"] = [item for item in state["agentHeartbeats"] if item.get("cluster") != cluster]
                state["agentHeartbeats"].append({"cluster": cluster, "time": now_text(), "version": body.get("version", "dev")})

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
            self.send_json({"task": task, "state": state})
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
            self.send_json({"task": task, "state": state})
            return
        if parsed.path != "/api/state":
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
