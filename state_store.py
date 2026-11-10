"""Transactional incremental state storage, shared by SQLite and PostgreSQL.

The legacy app_state row remains an upgrade backup. A committed format marker
switches reads to the new tables; a failed migration never exposes partial data.
The caller owns the single-writer lock and only advances its baseline on commit.
"""
import json


COLLECTIONS = ("executions", "agentTasks")
LOG_CHUNK_SIZE = 128


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS state_sections (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS state_records (collection TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (collection, id))")
    conn.execute("CREATE TABLE IF NOT EXISTS state_record_order (collection TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS state_log_chunks (collection TEXT NOT NULL, id TEXT NOT NULL, chunk INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY (collection, id, chunk))")


def load(conn):
    marker = conn.execute("SELECT value FROM app_state WHERE key = 'incremental_format'").fetchone()
    if not marker:
        return None
    if marker[0] != "1":
        raise RuntimeError("Unsupported incremental state format")
    state = {name: json.loads(value) for name, value in conn.execute("SELECT name, value FROM state_sections").fetchall()}
    records = {}
    for collection in COLLECTIONS:
        state[collection] = []
    for collection, record_id, value in conn.execute("SELECT collection, id, value FROM state_records").fetchall():
        item = json.loads(value)
        item["logs"] = []
        records[(collection, record_id)] = item
    for collection, value in conn.execute("SELECT collection, value FROM state_record_order").fetchall():
        state[collection] = [records[(collection, record_id)] for record_id in json.loads(value)]
    for collection, record_id, value in conn.execute("SELECT collection, id, value FROM state_log_chunks ORDER BY collection, id, chunk").fetchall():
        records[(collection, record_id)]["logs"].extend(json.loads(value))
    return state


def save(conn, state, previous=None, postgres=False):
    ensure_schema(conn)
    # On startup (including retries of the initial migration), reconcile against
    # committed data, never against an uncommitted in-memory baseline.
    if previous is None:
        previous = load(conn) or {}
    placeholder = "%s" if postgres else "?"
    stats = {"bytes": 0, "rows": 0}

    def execute(sql, args):
        conn.execute(sql.replace("?", placeholder), args)
        stats["rows"] += 1

    def serialize(value):
        payload = encode(value)
        stats["bytes"] += len(payload.encode("utf-8"))
        return payload

    for key in previous.keys() - state.keys():
        if key not in COLLECTIONS:
            execute("DELETE FROM state_sections WHERE name = ?", (key,))
    for key, value in state.items():
        if key in COLLECTIONS or (key in previous and (value is previous[key] or value == previous[key])):
            continue
        execute("INSERT INTO state_sections (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (key, serialize(value)))

    for collection in COLLECTIONS:
        old = {str(item["id"]): item for item in previous.get(collection, [])}
        seen = set()
        order = [str(item["id"]) for item in state.get(collection, [])]
        if collection not in previous or order != list(old):
            execute("INSERT INTO state_record_order (collection, value) VALUES (?, ?) ON CONFLICT(collection) DO UPDATE SET value = excluded.value", (collection, serialize(order)))
        for item in state.get(collection, []):
            record_id = str(item["id"])
            if record_id in seen:
                raise ValueError(f"Duplicate {collection} id: {record_id}")
            seen.add(record_id)
            before = old.get(record_id, {})
            if before is item:
                continue
            metadata = {key: value for key, value in item.items() if key != "logs"}
            old_metadata = {key: value for key, value in before.items() if key != "logs"}
            if record_id not in old or metadata != old_metadata:
                execute("INSERT INTO state_records (collection, id, value) VALUES (?, ?, ?) ON CONFLICT(collection, id) DO UPDATE SET value = excluded.value", (collection, record_id, serialize(metadata)))
            logs, old_logs = item.get("logs") or [], before.get("logs") or []
            if logs is old_logs:
                continue
            # Also supports replacement/truncation, not just append. Only changed
            # chunks cross the database connection, including after a retry.
            for start in range(0, len(logs), LOG_CHUNK_SIZE):
                chunk = logs[start:start + LOG_CHUNK_SIZE]
                if chunk == old_logs[start:start + LOG_CHUNK_SIZE]:
                    continue
                execute("INSERT INTO state_log_chunks (collection, id, chunk, value) VALUES (?, ?, ?, ?) ON CONFLICT(collection, id, chunk) DO UPDATE SET value = excluded.value", (collection, record_id, start // LOG_CHUNK_SIZE, serialize(chunk)))
            if len(logs) < len(old_logs):
                execute("DELETE FROM state_log_chunks WHERE collection = ? AND id = ? AND chunk >= ?", (collection, record_id, (len(logs) + LOG_CHUNK_SIZE - 1) // LOG_CHUNK_SIZE))
        for record_id in old.keys() - seen:
            execute("DELETE FROM state_log_chunks WHERE collection = ? AND id = ?", (collection, record_id))
            execute("DELETE FROM state_records WHERE collection = ? AND id = ?", (collection, record_id))
    conn.execute("INSERT INTO app_state (key, value) VALUES ('incremental_format', '1') ON CONFLICT(key) DO NOTHING")
    return stats
