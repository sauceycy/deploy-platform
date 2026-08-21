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
