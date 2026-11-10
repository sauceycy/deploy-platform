"""Small durable inbox/outbox for the Kubernetes Agent.

Persist ownership before kubectl and persist its result before reporting it.
Commands interrupted by an Agent restart are reported as uncertain failures,
never silently dropped and never automatically replayed.
"""
import json
import fcntl
from pathlib import Path
import sqlite3
import threading
import time


class AgentStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.lock = threading.RLock()
        self.process_lock = path.with_suffix('.lock').open('a')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise RuntimeError('Another Agent already owns this state directory') from None
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        path.chmod(0o600)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        with self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS identity (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS jobs (request_id TEXT PRIMARY KEY, task_id TEXT UNIQUE, payload TEXT, status TEXT NOT NULL, result TEXT, updated_at REAL NOT NULL)")

    def identity(self, proposed):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO identity VALUES ('instance', ?) ON CONFLICT(name) DO NOTHING", (proposed,))
            return self.conn.execute("SELECT value FROM identity WHERE name = 'instance'").fetchone()[0]

    def begin(self, request_id):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO jobs (request_id, status, updated_at) VALUES (?, 'claiming', ?)", (request_id, time.time()))

    def received(self, request_id, task):
        with self.lock, self.conn:
            self.conn.execute("UPDATE jobs SET task_id=?, payload=?, status='running', updated_at=? WHERE request_id=?",
                              (task["id"], json.dumps(task), time.time(), request_id))

    def result(self, request_id, result):
        with self.lock, self.conn:
            self.conn.execute("UPDATE jobs SET status='result', result=?, updated_at=? WHERE request_id=?",
                              (json.dumps(result), time.time(), request_id))

    def complete(self, request_id):
        with self.lock, self.conn:
            self.conn.execute("UPDATE jobs SET status='reported', updated_at=? WHERE request_id=?", (time.time(), request_id))
            self.conn.execute("DELETE FROM jobs WHERE status='reported' AND request_id NOT IN (SELECT request_id FROM jobs WHERE status='reported' ORDER BY updated_at DESC LIMIT 1000)")

    def recover(self):
        with self.lock, self.conn:
            rows = self.conn.execute("SELECT request_id, task_id, status, result FROM jobs WHERE status != 'reported' ORDER BY updated_at").fetchall()
            recovered = []
            for request_id, task_id, status, result in rows:
                if status == "running":
                    result = json.dumps({"id": task_id, "status": "failed", "logs":
                        "Agent restarted during deployment; outcome is uncertain. Inspect Kubernetes state before retrying. The command was not replayed."})
                    self.conn.execute("UPDATE jobs SET status='result', result=?, updated_at=? WHERE request_id=?", (result, time.time(), request_id))
                recovered.append({"requestId": request_id, "result": json.loads(result) if result else None})
            return recovered

    def close(self):
        with self.lock:
            self.conn.close()
            self.process_lock.close()
