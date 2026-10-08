"""Pull Windows ZIP releases and run the local WinSW deployment adapter."""

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen


AGENT_USER_AGENT = "DeployPlatform-Windows-Agent/0.1"


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def verify_package(package, expected_sha256, staging):
    with package.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != expected_sha256:
            raise ValueError("Release ZIP SHA256 mismatch")
    with zipfile.ZipFile(package) as archive:
        entries = archive.infolist()
        if sum(item.file_size for item in entries) > 512 * 1024 * 1024:
            raise ValueError("Expanded release exceeds 512 MiB")
        names = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            parts = item.filename.split("/")
            if not parts or path.is_absolute() or "\\" in item.filename or ":" in item.filename or any(
                part in {"", ".", ".."} or part.rstrip(". ") != part or
                re.search(r'[<>|?*\x00-\x1f]', part) or
                re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", part.split(".")[0], re.I)
                for part in parts
            ):
                raise ValueError("Unsafe Windows ZIP entry")
            if item.filename.lower() in names or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("Duplicate or linked ZIP entry")
            names.add(item.filename.lower())
        manifest = json.loads(archive.read("release-manifest.json"))
        records = manifest["files"]
        if {item.filename for item in entries} != set(records) | {"release-manifest.json"}:
            raise ValueError("Release manifest file set mismatch")
        for name, record in records.items():
            data = archive.read(name)
            if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("Release manifest checksum mismatch")
        for required in ("deploy/windows/Expand-Release.ps1", "deploy/windows/Start-HttpService.ps1", "deploy/windows/inspect_runtime.py"):
            if required not in records:
                raise ValueError(f"Missing MT5 deployment adapter: {required}")
        staging.mkdir(parents=True, exist_ok=True)
        archive.extractall(staging)


class Agent:
    def __init__(self, config_path):
        self.config_path = config_path.resolve()
        self.config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        self.url = self.config["platformUrl"].rstrip("/")
        parsed = urlparse(self.url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("platformUrl must be an HTTP(S) address without credentials")
        self.cluster = self.config["cluster"]
        self.instance = self.config.get("instanceId") or socket.gethostname()
        token = self.config.get("agentToken") or os.environ.get("WINDOWS_AGENT_TOKEN")
        if not token:
            raise ValueError("Configure agentToken or WINDOWS_AGENT_TOKEN")
        self.headers = {"X-Agent-Token": token, "Accept": "application/json", "User-Agent": AGENT_USER_AGENT}
        for key, env_name in (("CF-Access-Client-Id", "CF_ACCESS_CLIENT_ID"), ("CF-Access-Client-Secret", "CF_ACCESS_CLIENT_SECRET")):
            if os.environ.get(env_name):
                self.headers[key] = os.environ[env_name]
        self.root = Path(self.config["stateDirectory"]).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = None
        if os.name == "nt":
            import msvcrt
            self.lock = (self.root / "agent.lock").open("a+b")
            self.lock.seek(0)
            self.lock.write(b"0")
            self.lock.flush()
            self.lock.seek(0)
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
        if len(self.config["applications"]) != 1:
            raise ValueError("This MT5 Agent supports exactly one configured application per server")
        self.db = sqlite3.connect(self.root / "tasks.sqlite3")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, task TEXT, status TEXT, logs TEXT)")
        self.db.execute("UPDATE tasks SET status='failed', logs='Agent stopped during release. Inspect service state before retry or rollback; this task will not be replayed.' WHERE status='running'")
        self.db.commit()
        self.current = None
        self.cancelled = threading.Event()
        self.stopped = threading.Event()

    def request(self, path, payload=None):
        data = json.dumps({"cluster": self.cluster, "instanceId": self.instance, **payload}).encode() if payload is not None else None
        query = urlencode({"cluster": self.cluster, "instanceId": self.instance})
        req = Request(self.url + path + "?" + query, data=data, headers={**self.headers, "Content-Type": "application/json"})
        with urlopen(req, timeout=20) as response:
            return json.load(response)

    def redact(self, text):
        values = [self.headers["X-Agent-Token"]]
        if self.current:
            env_file = self.current.get('payload', {}).get('envFile') or {}
            values.append(env_file.get('content', ''))
            values.extend(env_file.get('variables', {}).values())
        for application in self.config["applications"].values():
            values.extend(str(value) for value in application.get("Environment", {}).values())
        for value in sorted((value for value in values if isinstance(value, str)), key=len, reverse=True):
            if value:
                text = text.replace(value, "***")
        text = "\n".join("[sensitive configuration output omitted]" if re.search(r"input_value=|credentialRef|(?:password|secret|token)\s*[:=]", line, re.I) else line for line in text.splitlines())
        return text

    def background(self):
        while not self.stopped.is_set():
            try:
                self.request("/api/windows-agent/heartbeat", {'capabilities': ['dotenv-v1']})
                task = self.current
                if task:
                    result = self.request(f"/api/windows-agent/tasks/{task['id']}/progress", {})
                    if result.get("status") != "running":
                        self.cancelled.set()
                        (self.root / f"{task['id']}.cancel").touch()
            except Exception as error:
                print(f"Heartbeat unavailable ({type(error).__name__}); retrying", flush=True)
            self.stopped.wait(15)

    def download(self, task, destination):
        payload = task["payload"]
        path = payload["downloadPath"]
        if path != f"/api/windows-agent/artifacts/{task['id']}":
            raise ValueError("Unexpected artifact URL")
        size = int(payload["size"])
        if not 0 < size <= 256 * 1024 * 1024:
            raise ValueError("Release ZIP must be at most 256 MiB")
        query = urlencode({"cluster": self.cluster, "instanceId": self.instance})
        req = Request(self.url + path + "?" + query, headers=self.headers)
        received = 0
        temporary = destination.with_suffix(".part")
        with urlopen(req, timeout=60) as response, temporary.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                if self.cancelled.is_set():
                    raise RuntimeError("Release cancelled before cutover")
                received += len(chunk)
                if received > size:
                    raise ValueError("Release ZIP size mismatch")
                target.write(chunk)
        if received != size:
            raise ValueError("Release ZIP download incomplete")
        temporary.replace(destination)

    def deployment_settings(self, payload):
        settings = self.config['applications'].get(payload['application'])
        if not settings:
            raise ValueError(f"Application is not configured locally: {payload['application']}")
        settings = dict(settings)
        if 'envFile' in payload:
            env_file = payload['envFile']
            if not isinstance(env_file, dict) or not isinstance(env_file.get('content'), str) or not isinstance(env_file.get('variables'), dict) or len(env_file['content'].encode('utf-8')) > 65536 or '\x00' in env_file['content']:
                raise ValueError('Invalid Windows environment payload')
            variables = env_file['variables']
            if any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str) or '\x00' in value for key, value in variables.items()):
                raise ValueError('Invalid Windows environment variables')
            if not env_file['content'].strip():
                return settings
            settings['EnvContent'] = env_file['content']
            environment = {key: value for key, value in variables.items() if key.lower() != 'python_mt5_sidecar_dotenv_enabled'}
            # Inject explicitly so production does not need automatic dotenv loading.
            environment['PYTHON_MT5_SIDECAR_DOTENV_ENABLED'] = 'false'
            settings['Environment'] = environment
        return settings

    def execute(self, task):
        payload = task["payload"]
        if payload["action"] not in {"deploy", "rollback"}:
            raise ValueError("Unsupported Windows action")
        settings = self.deployment_settings(payload)
        task_dir = self.root / task["id"]
        task_dir.mkdir(exist_ok=True)
        local_config = task_dir / "application.json"
        save_json(local_config, settings)
        args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                str(Path(__file__).with_name("Invoke-Mt5Release.ps1")), "-ConfigPath", str(local_config),
                "-Action", payload["action"], "-CancellationFile", str(self.root / f"{task['id']}.cancel")]
        if payload["action"] == "deploy":
            package = task_dir / "release.zip"
            self.download(task, package)
            staging = task_dir / "verified"
            verify_package(package, payload["sha256"], staging)
            args += ["-Package", str(package), "-ExpectedSha256", payload["sha256"], "-VerifiedDirectory", str(staging)]
        if self.cancelled.is_set():
            raise RuntimeError("Release cancelled before cutover")
        output_path = task_dir / "deployment.log"
        started = time.monotonic()
        with output_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
            last_offset = 0
            try:
                while process.poll() is None:
                    time.sleep(2)
                    text = output_path.read_text(encoding="utf-8", errors="replace")
                    if len(text) > last_offset:
                        message = self.redact(text[last_offset:][-2000:])
                        last_offset = len(text)
                        try:
                            self.request(f"/api/windows-agent/tasks/{task['id']}/progress", {"message": message})
                        except Exception:
                            pass
                    if time.monotonic() - started > 3600:
                        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                        raise RuntimeError("Deployment exceeded one hour. Inspect WinSW state before retrying.")
            finally:
                if process.poll() is None:
                    process.wait(timeout=10)
        logs = self.redact(output_path.read_text(encoding="utf-8", errors="replace")[-30000:])
        return ("success" if process.returncode == 0 else "failed"), logs

    def report_pending(self):
        for task_id, status, logs in self.db.execute("SELECT id,status,logs FROM tasks WHERE status IN ('success','failed')").fetchall():
            try:
                result = self.request(f"/api/windows-agent/tasks/{task_id}/result", {"status": status, "logs": logs})
            except HTTPError as error:
                if error.code != 404:
                    raise
                result = {"ok": True}
            if result.get("ok"):
                self.db.execute("UPDATE tasks SET status='reported' WHERE id=?", (task_id,))
                self.db.commit()

    def run(self):
        # Independent of the business service: upgrading its .venv cannot stop this agent.
        worker = threading.Thread(target=self.background, daemon=True)
        worker.start()
        try:
            while True:
                try:
                    self.report_pending()
                    task = self.request("/api/windows-agent/tasks").get("task")
                    if not task:
                        time.sleep(5)
                        continue
                    if not re.fullmatch(r"[a-f0-9]{12}", str(task["id"])):
                        raise ValueError("Invalid task ID")
                    if self.db.execute("SELECT id FROM tasks WHERE id=?", (task["id"],)).fetchone():
                        raise ValueError("Refusing to execute duplicate release task")
                    self.db.execute("INSERT INTO tasks VALUES (?,?,'running','')", (task["id"], json.dumps(task)))
                    self.db.commit()
                    self.cancelled.clear()
                    self.current = task
                    try:
                        status, logs = self.execute(task)
                    except Exception as error:
                        status, logs = "failed", self.redact(f"{type(error).__name__}: {error}")
                    finally:
                        self.current = None
                    self.db.execute("UPDATE tasks SET status=?,logs=? WHERE id=?", (status, logs, task["id"]))
                    self.db.commit()
                except Exception as error:
                    print(f"Agent request failed ({type(error).__name__}); retrying", flush=True)
                    time.sleep(5)
        finally:
            self.stopped.set()
            worker.join(timeout=25)
            if self.lock:
                self.lock.close()
            self.db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    if os.name != "nt":
        parser.error("This agent runs on Windows only")
    Agent(arguments.config).run()
