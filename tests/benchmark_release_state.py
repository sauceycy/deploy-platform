"""Reproducible synthetic benchmark; never opens the platform's real database.

Run: python3 tests/benchmark_release_state.py
"""
import copy
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import state_store
from log_sequence import LogSequence


def timed(fn, rounds=5):
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 3)


def main():
    state = s.default_state_copy()
    state["tasks"] = [{"id": "task", "name": "benchmark"}]
    state["executions"] = [
        {"id": str(i), "taskId": "task", "status": "building" if i == 0 else "success",
         "logs": [{"time": "2026-01-01", "message": f"{j}:" + "x" * 1000} for j in range(1000)]}
        for i in range(100)]
    result = {"executions": 100, "logs": 100000}
    result["legacy_two_deepcopies_ms"] = timed(lambda: copy.deepcopy(copy.deepcopy(state)))
    result["legacy_full_serialize_ms"] = timed(lambda: s.state_payload(state))
    result["legacy_payload_bytes"] = len(s.state_payload(state).encode())
    # Startup conversion is outside the warm-path measurements, as in the server.
    for execution in state['executions']:
        execution['logs'] = LogSequence(execution['logs'])
    with patch.object(s, "STATE_CACHE", state), patch.object(s, "enqueue_state_write"), \
            patch.object(s, "broadcast_state"), patch.object(s, 'broadcast_ws'):
        progress = iter(range(30, 40))
        result["runtime_update_ms"] = timed(lambda: s.set_execution_status("0", "building", progress=next(progress)))
        result["config_update_ms"] = timed(lambda: s.mutate_state(lambda value: value["platformSettings"].update(imageNamespace="benchmark"), read_only_history=True))
        result['streaming_append_ms'] = timed(lambda: s.append_log('0', 'benchmark line'))
    result['log_sequence_append_ms_by_length'] = {}
    for count in (1000, 100000):
        sequence = LogSequence({'message': 'x' * 1000} for _ in range(count))
        result['log_sequence_append_ms_by_length'][str(count)] = timed(lambda: sequence.appended({'message': 'new line'}))
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        with conn:
            state_store.save(conn, state)
        next_state = {**state, "revision": int(state.get("revision") or 0) + 1, "executions": [
            {**state["executions"][0], "logs": state["executions"][0]["logs"].appended({"message": "new line"})},
            *state["executions"][1:]]}
        started = time.perf_counter()
        with conn:
            stats = state_store.save(conn, next_state, state)
        result["incremental_append_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result["incremental_rows"] = stats["rows"]
        result["incremental_payload_bytes"] = stats["bytes"]
        assert state_store.load(conn) == next_state
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
