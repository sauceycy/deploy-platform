import json
import os
import subprocess
import time
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PLATFORM_URL = os.environ.get("PLATFORM_URL", "http://deploy-platform-web").rstrip("/")
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "dev-01")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "dev-agent-token")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "5"))
AGENT_HEADERS = {
    "User-Agent": "DeployPlatformAgent/0.1",
    "Accept": "application/json",
    "X-Agent-Token": AGENT_TOKEN,
}


def api_get(path, query=None):
    return api_request("GET", path, query=query)


def api_post(path, payload):
    return api_request("POST", path, payload=payload)


def api_request(method, path, payload=None, query=None):
    url = f"{PLATFORM_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    data = None
    headers = AGENT_HEADERS
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {**AGENT_HEADERS, "Content-Type": "application/json"}
    req = Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{method} {path} HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {path} failed: {exc.reason}") from exc


def run_kubectl(args, manifest=None):
    process = subprocess.run(
        ["kubectl", *args],
        input=manifest,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return process.returncode, process.stdout


def execute_task(task):
    payload = task["payload"]
    manifest = payload["manifest"]
    namespace = payload["namespace"]
    deployment = payload["deployment"]
    logs = []

    code, output = run_kubectl(["apply", "-f", "-"], manifest)
    logs.append(output)
    if code != 0:
        return "failed", "\n".join(logs)

    code, output = run_kubectl(["rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=180s"])
    logs.append(output)
    if code != 0:
        return "failed", "\n".join(logs)
    return "success", "\n".join(logs)


def heartbeat():
    try:
        api_post("/api/agent/heartbeat", {"cluster": CLUSTER_NAME, "version": "0.1.0"})
    except Exception:
        pass


if __name__ == "__main__":
    print(f"Deploy Agent started cluster={CLUSTER_NAME} platform={PLATFORM_URL}", flush=True)
    last_heartbeat = 0
    pending_result = None
    while True:
        try:
            if time.time() - last_heartbeat > 30:
                heartbeat()
                last_heartbeat = time.time()
            if pending_result:
                api_post(
                    f"/api/agent/tasks/{pending_result['id']}/result",
                    {"status": pending_result["status"], "logs": pending_result["logs"]},
                )
                print(f"reported task result id={pending_result['id']} status={pending_result['status']}", flush=True)
                pending_result = None
                continue
            result = api_get("/api/agent/tasks", {"cluster": CLUSTER_NAME})
            task = result.get("task")
            if task:
                status, logs = execute_task(task)
                pending_result = {"id": task["id"], "status": status, "logs": logs}
                api_post(f"/api/agent/tasks/{task['id']}/result", {"status": status, "logs": logs})
                print(f"reported task result id={task['id']} status={status}", flush=True)
                pending_result = None
            else:
                time.sleep(POLL_SECONDS)
        except Exception as exc:
            print(f"agent error: {exc}", flush=True)
            time.sleep(POLL_SECONDS)
