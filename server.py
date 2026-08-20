import json
import os
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DEPLOY_PLATFORM_DB", "/data/deploy-platform.sqlite3"))
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
                "user.view",
                "user.manage",
                "rbac.view",
                "rbac.manage",
                "audit.view",
            ],
        },
        "developer": {
            "label": "开发人员",
            "permissions": ["task.view", "task.create", "task.deploy", "cluster.view", "template.view", "channel.view"],
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
    "auditLogs": [],
}


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return conn


def read_state():
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = 'state'").fetchone()
        if not row:
            write_state(DEFAULT_STATE)
            return DEFAULT_STATE
        return json.loads(row[0])


def write_state(state):
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('state', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (payload,),
        )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "public, max-age=60")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/health":
            self.send_json({"status": "ok"})
            return
        if self.path == "/api/state":
            self.send_json(read_state())
            return
        super().do_GET()

    def do_PUT(self):
        if self.path != "/api/state":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = self.rfile.read(length).decode("utf-8")
            state = json.loads(body)
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
    port = int(os.environ.get("PORT", "80"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Deploy Platform listening on :{port}, sqlite={DB_PATH}", flush=True)
    server.serve_forever()
