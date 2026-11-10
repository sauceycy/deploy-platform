import json
import os
import queue
import socket
import subprocess
import threading
import time
import uuid
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from agent_store import AgentStore


PLATFORM_URL = os.environ.get("PLATFORM_URL", "http://deploy-platform-web").rstrip("/")
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "dev-01")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "dev-agent-token")
AGENT_INSTANCE_ID = os.environ.get("AGENT_INSTANCE_ID") or socket.gethostname()
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "5"))
AGENT_MAX_CONCURRENT_TASKS = max(1, int(os.environ.get("AGENT_MAX_CONCURRENT_TASKS", "5")))
KUBECTL_TIMEOUT_SECONDS = max(180, int(os.environ.get("KUBECTL_TIMEOUT_SECONDS", "300")))
AGENT_STATE_DIR = os.environ.get("AGENT_STATE_DIR", "/data/agent")
CF_ACCESS_CLIENT_ID = os.environ.get("CF_ACCESS_CLIENT_ID", "").strip()
CF_ACCESS_CLIENT_SECRET = os.environ.get("CF_ACCESS_CLIENT_SECRET", "").strip()
AGENT_HEADERS = {
    "User-Agent": "DeployPlatformAgent/0.1 (+https://github.com/sauceycy/deploy-platform)",
    "Accept": "application/json",
    "X-Agent-Token": AGENT_TOKEN,
}
if CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET:
    AGENT_HEADERS.update(
        {
            "CF-Access-Client-Id": CF_ACCESS_CLIENT_ID,
            "CF-Access-Client-Secret": CF_ACCESS_CLIENT_SECRET,
        }
    )


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
        if exc.code == 403 and "Attention Required" in body and "Cloudflare" in body:
            raise RuntimeError(
                f"{method} {path} 被 Cloudflare 拦截，平台后端未收到请求。请给 /api/agent/* 配置 WAF 跳过规则，"
                "或在 Agent 环境变量中配置 CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET。"
            ) from exc
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
        timeout=KUBECTL_TIMEOUT_SECONDS,
    )
    return process.returncode, process.stdout


def deployment_is_available(namespace, deployment):
    code, output = run_kubectl(["get", "deployment", deployment, "-n", namespace, "-o", "json"])
    if code != 0:
        return False, output
    try:
        resource = json.loads(output)
    except Exception:
        return False, output
    spec_replicas = int(resource.get("spec", {}).get("replicas") or 1)
    status = resource.get("status", {})
    available = int(status.get("availableReplicas") or 0)
    updated = int(status.get("updatedReplicas") or 0)
    observed = int(status.get("observedGeneration") or 0)
    generation = int(resource.get("metadata", {}).get("generation") or 0)
    ready = available >= spec_replicas and updated >= spec_replicas and observed >= generation
    summary = f"deployment {deployment}: available={available}/{spec_replicas}, updated={updated}/{spec_replicas}, observedGeneration={observed}, generation={generation}"
    return ready, summary


def last_lines(text, limit=120):
    lines = str(text or "").splitlines()
    return "\n".join(lines[-limit:])


def pod_names_for_deployment(namespace, deployment):
    code, output = run_kubectl(["get", "pods", "-n", namespace, "-l", f"app={deployment}", "-o", "json"])
    if code != 0:
        return [], output
    try:
        resource = json.loads(output)
    except Exception:
        return [], output
    return [item.get("metadata", {}).get("name") for item in resource.get("items", []) if item.get("metadata", {}).get("name")], output


def summarize_pod_statuses(pods_json):
    try:
        resource = json.loads(pods_json)
    except Exception:
        return pods_json
    summaries = []
    for pod in resource.get("items", []):
        name = pod.get("metadata", {}).get("name", "unknown")
        phase = pod.get("status", {}).get("phase", "Unknown")
        statuses = pod.get("status", {}).get("containerStatuses", [])
        container_parts = []
        for status in statuses:
            state = status.get("state", {})
            if state.get("waiting"):
                waiting = state["waiting"]
                container_parts.append(f"{status.get('name')}: waiting {waiting.get('reason')} {waiting.get('message', '')}".strip())
            elif state.get("terminated"):
                terminated = state["terminated"]
                container_parts.append(f"{status.get('name')}: terminated {terminated.get('reason')} exitCode={terminated.get('exitCode')}")
            else:
                container_parts.append(f"{status.get('name')}: ready={status.get('ready')} restartCount={status.get('restartCount')}")
        summaries.append(f"{name}: phase={phase}; {'; '.join(container_parts) or 'no container status'}")
    return "\n".join(summaries) or "未找到匹配 Pod"


def collect_failure_diagnostics(namespace, deployment):
    diagnostics = ["=== Kubernetes 部署诊断 ==="]

    code, output = run_kubectl(["get", "deployment", deployment, "-n", namespace, "-o", "wide"])
    diagnostics.append("$ kubectl get deployment")
    diagnostics.append(output.strip() or f"exit={code}")

    code, output = run_kubectl(["get", "pods", "-n", namespace, "-l", f"app={deployment}", "-o", "wide"])
    diagnostics.append("$ kubectl get pods")
    diagnostics.append(output.strip() or f"exit={code}")

    pod_names, pods_json = pod_names_for_deployment(namespace, deployment)
    diagnostics.append("$ pod status summary")
    diagnostics.append(summarize_pod_statuses(pods_json))

    code, output = run_kubectl(["describe", "deployment", deployment, "-n", namespace])
    diagnostics.append("$ kubectl describe deployment")
    diagnostics.append(last_lines(output, 80) or f"exit={code}")

    for pod_name in pod_names[:3]:
        code, output = run_kubectl(["describe", "pod", pod_name, "-n", namespace])
        diagnostics.append(f"$ kubectl describe pod {pod_name}")
        diagnostics.append(last_lines(output, 100) or f"exit={code}")

        code, output = run_kubectl(["logs", pod_name, "-n", namespace, "--all-containers=true", "--tail=120"])
        diagnostics.append(f"$ kubectl logs {pod_name} --all-containers --tail=120")
        diagnostics.append(output.strip() or f"exit={code}")

        code, output = run_kubectl(["logs", pod_name, "-n", namespace, "--all-containers=true", "--previous", "--tail=80"])
        if code == 0 and output.strip():
            diagnostics.append(f"$ kubectl logs {pod_name} --previous --all-containers --tail=80")
            diagnostics.append(output.strip())

    return "\n".join(diagnostics)


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
        ready, summary = deployment_is_available(namespace, deployment)
        logs.append(summary)
        if ready:
            return "success", "\n".join(logs)
        logs.append(collect_failure_diagnostics(namespace, deployment))
        return "failed", "\n".join(logs)
    return "success", "\n".join(logs)


def heartbeat():
    try:
        api_post("/api/agent/heartbeat", {"cluster": CLUSTER_NAME, "instanceId": AGENT_INSTANCE_ID, "version": "0.1.0"})
    except Exception as exc:
        print(f"agent heartbeat error: {exc}", flush=True)


def heartbeat_loop(stop_event):
    # Keep reporting liveness while kubectl or result reporting blocks the worker.
    while not stop_event.is_set():
        heartbeat()
        if stop_event.wait(30):
            break


class AgentWorker:
    """Each slot claims once, executes once, and retries reporting independently.

    Retain the request ID across a lost claim response and retain the result
    across reporting failures, so either failure cannot re-run kubectl.
    """
    def __init__(self, store, recovered=None):
        self.store = store
        self.recovered = recovered if recovered is not None else queue.Queue()
        self.request_id = None
        self.pending_result = None

    def tick(self):
        if self.request_id is None:
            try:
                record = self.recovered.get_nowait()
                self.request_id = record["requestId"]
                self.pending_result = record["result"]
            except queue.Empty:
                request_id = uuid.uuid4().hex
                self.store.begin(request_id)
                self.request_id = request_id
        if self.pending_result is None:
            result = api_get("/api/agent/tasks", {"cluster": CLUSTER_NAME, "instanceId": AGENT_INSTANCE_ID,
                                                   "requestId": self.request_id})
            task = result.get("task")
            if not task:
                return False
            self.store.received(self.request_id, task)
            # Only the command owner runs this claimed task. A cancelled task
            # returned by an idempotent claim retry is acknowledged without apply.
            if task.get("status", "running") == "running":
                try:
                    status, logs = execute_task(task)
                except Exception as exc:
                    status, logs = "failed", f"Agent execution failed: {exc}"
            else:
                status, logs = "failed", "Task ended before Agent began deployment"
            self.pending_result = {"id": task["id"], "status": status, "logs": logs}
        result = self.pending_result
        self.store.result(self.request_id, result)
        api_post(f"/api/agent/tasks/{result['id']}/result",
                 {"status": result["status"], "logs": result["logs"],
                  "instanceId": AGENT_INSTANCE_ID, "cluster": CLUSTER_NAME})
        print(f"reported task result id={result['id']} status={result['status']}", flush=True)
        self.store.complete(self.request_id)
        self.pending_result = None
        self.request_id = None
        return True


def deployment_worker_loop(stop_event, store, recovered):
    worker = AgentWorker(store, recovered)
    while not stop_event.is_set():
        try:
            if worker.tick():
                continue
        except Exception as exc:
            print(f"agent worker error: {exc}", flush=True)
        if stop_event.wait(POLL_SECONDS):
            return


if __name__ == "__main__":
    store = AgentStore(os.path.join(AGENT_STATE_DIR, "jobs.sqlite3"))
    # Persist identity with the ledger, including when a Pod is replaced using
    # the same persistent volume. Different Agent replicas need different volumes.
    AGENT_INSTANCE_ID = store.identity(AGENT_INSTANCE_ID)
    recovered = queue.Queue()
    for record in store.recover():
        recovered.put(record)
    print(f"Deploy Agent started cluster={CLUSTER_NAME} instance={AGENT_INSTANCE_ID} platform={PLATFORM_URL} concurrency={AGENT_MAX_CONCURRENT_TASKS}", flush=True)
    heartbeat_stop = threading.Event()
    threading.Thread(target=heartbeat_loop, args=(heartbeat_stop,), daemon=True, name="agent-heartbeat").start()
    workers = [threading.Thread(target=deployment_worker_loop, args=(heartbeat_stop, store, recovered),
                                name=f"agent-deploy-{index + 1}") for index in range(AGENT_MAX_CONCURRENT_TASKS)]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join()
    finally:
        heartbeat_stop.set()
