import json
import os
import subprocess
import time
from urllib.parse import urlencode
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
    url = f"{PLATFORM_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    req = Request(url, headers=AGENT_HEADERS)
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def api_post(path, payload):
    req = Request(
        f"{PLATFORM_URL}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**AGENT_HEADERS, "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


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
    while True:
        try:
            if time.time() - last_heartbeat > 30:
                heartbeat()
                last_heartbeat = time.time()
            result = api_get("/api/agent/tasks", {"cluster": CLUSTER_NAME, "token": AGENT_TOKEN})
            task = result.get("task")
            if task:
                status, logs = execute_task(task)
                api_post(f"/api/agent/tasks/{task['id']}/result", {"status": status, "logs": logs})
            else:
                time.sleep(POLL_SECONDS)
        except Exception as exc:
            print(f"agent error: {exc}", flush=True)
            time.sleep(POLL_SECONDS)
