"""Deterministic volume budgets, independent of CI hardware speed.

Bytes measure serialized application payloads, not database pages/WAL or network
protocol overhead. Real SQL and reloads verify that the small writes retain data.
"""
import sqlite3
import unittest
from unittest.mock import Mock, patch

import server as s
import state_store as store
import log_sequence


class RecordingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.writes = []
        self.statements = []

    def execute(self, sql, args=()):
        self.statements.append(sql)
        if sql.startswith(("INSERT INTO state_", "DELETE FROM state_", "UPDATE state_")):
            self.writes.append((sql, args))
        return self.connection.execute(sql, args)


class PerformanceContracts(unittest.TestCase):
    def database(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return connection

    def history(self, count):
        # A nearly full last chunk exercises the maximum append amplification.
        logs = [{"message": "x" * 1024} for _ in range(511)]
        return {"revision": 1, "tasks": [{"id": "task"}], "agentTasks": [],
                "executions": [{"id": str(i), "taskId": "task", "status": "building",
                                "logs": logs} for i in range(count)]}

    def test_append_payload_does_not_grow_with_unrelated_history(self):
        budgets = []
        for count in (2, 64):
            with self.subTest(executions=count):
                connection = self.database()
                old = self.history(count)
                legacy = store.encode(old)
                connection.execute("INSERT INTO app_state VALUES ('state', ?)", (legacy,))
                with connection:
                    store.save(connection, old)
                new = {**old, "revision": 2, "executions": [
                    {**old["executions"][0], "logs": [*old["executions"][0]["logs"], {"message": "new"}]},
                    *old["executions"][1:]]}
                observed = RecordingConnection(connection)
                with connection:
                    stats = store.save(observed, new, old)
                self.assertEqual(len(observed.writes), 2)
                self.assertEqual(stats["rows"], 2)
                self.assertLessEqual(stats["bytes"], 140_000)
                self.assertEqual(store.load(connection), new)
                self.assertEqual(connection.execute("SELECT value FROM app_state WHERE key='state'").fetchone()[0], legacy)
                budgets.append(stats)
        self.assertEqual(budgets[0], budgets[1])

    def test_metadata_change_never_reads_or_serializes_log_contents(self):
        connection = self.database()
        old = self.history(8)
        with connection:
            store.save(connection, old)

        class UnreadableLogs(list):
            def __iter__(self):
                raise AssertionError("metadata update iterated historical logs")

            def __getitem__(self, key):
                raise AssertionError("metadata update read historical logs")

        # Published generations share the same immutable log collection here.
        expected = {**old, "executions": [{**old["executions"][0], "status": "success"}, *old["executions"][1:]]}
        old["executions"][0]["logs"] = UnreadableLogs(old["executions"][0]["logs"])
        new = {**old, "executions": [{**old["executions"][0], "status": "success"}, *old["executions"][1:]]}
        observed = RecordingConnection(connection)
        with connection:
            stats = store.save(observed, new, old)
        self.assertEqual(len(observed.writes), 1)
        self.assertLessEqual(stats["bytes"], 256)
        self.assertEqual(store.load(connection), expected)

    def test_enqueue_preserves_existing_metadata_and_log_rows(self):
        connection = self.database()
        old = self.history(64)
        with connection:
            store.save(connection, old)
        new = {**old, "revision": 2, "executions": [
            {"id": "new", "taskId": "task", "status": "queued", "logs": [{"message": "queued"}]},
            *old["executions"]]}
        observed = RecordingConnection(connection)
        with connection:
            stats = store.save(observed, new, old)
        self.assertEqual(len(observed.writes), 4)
        self.assertLessEqual(stats["bytes"], 2048)
        for sql, args in observed.writes:
            if sql.startswith(("INSERT INTO state_records ", "INSERT INTO state_log_chunks ")):
                self.assertEqual(args[:2], ("executions", "new"))
        self.assertEqual(store.load(connection), new)

    def test_unchanged_collections_are_not_scanned_for_configuration_writes(self):
        connection = self.database()
        initial = self.history(64)
        with connection:
            store.save(connection, initial)
        class UnreadableHistory(list):
            def __iter__(self):
                raise AssertionError('scanned an unchanged history collection')
        old = {**initial, 'executions': UnreadableHistory(initial['executions']),
               'agentTasks': UnreadableHistory(initial['agentTasks'])}
        new = {**old, 'revision': 2, 'tasks': [{'id': 'task', 'name': 'renamed'}]}
        observed = RecordingConnection(connection)
        with connection:
            stats = store.save(observed, new, old)
        self.assertEqual(len(observed.writes), 2)
        self.assertEqual(len(observed.statements), 2)
        self.assertLessEqual(stats['bytes'], 100)
        expected = {**initial, 'revision': 2, 'tasks': new['tasks']}
        self.assertEqual(store.load(connection), expected)

    def test_snapshot_and_broadcast_are_bounded_as_history_grows(self):
        small = self.history(50)
        small["users"] = [{"id": "admin", "password": "PRIVATE_PASSWORD"}]
        small["secrets"] = [{"id": "key", "secret": "PRIVATE_SECRET"}]
        small["agentTasks"] = [{"id": str(i), "payload": {"manifest": "PRIVATE_MANIFEST"},
                                "logs": small["executions"][0]["logs"]} for i in range(50)]
        small["executions"][0]["logs"] = [*small["executions"][0]["logs"], {"message": "ERROR " + "e" * 5000}]
        older = [{**item, "id": "older-" + item["id"]} for item in self.history(200)["executions"]]
        large = {**small, "executions": [*small["executions"], *older],
                 "agentTasks": [*small["agentTasks"],
                                *[{**small["agentTasks"][0], "id": f"older-{i}"} for i in range(200)]]}
        compact = s.client_state(small, compact=True)
        self.assertEqual(s.client_state(large, compact=True), compact)
        payload = store.encode(compact)
        self.assertLessEqual(len(payload.encode()), 100_000)
        for private in ("PRIVATE_PASSWORD", "PRIVATE_SECRET", "PRIVATE_MANIFEST"):
            self.assertNotIn(private, payload)
        self.assertEqual(len(compact["executions"]), 50)
        self.assertEqual(len(compact["agentTasks"]), 50)
        self.assertEqual(compact["executions"][0]["logs"], [])
        self.assertEqual(compact["executions"][0]["logCount"], 512)
        frames = []
        for state in (small, large):
            updated = {**state, "revision": 2, "executions": [
                {**state["executions"][0], "progress": 70}, *state["executions"][1:]]}
            with patch.object(s, "WS_CLIENTS", {Mock(delta=True)}), patch.object(s, "broadcast_ws") as broadcast:
                s.broadcast_state(updated, state)
                frames.append(broadcast.call_args.args[0])
        self.assertEqual(frames[0], frames[1])
        self.assertEqual(set(frames[0]["patch"]), {"executions", "revision"})
        self.assertLessEqual(len(store.encode(frames[0]).encode()), 100_000)

    def test_persistent_log_append_skips_shared_prefix_without_relaxing_write_budget(self):
        budgets = []
        for count in (511, 16383):
            with self.subTest(logs=count):
                connection = self.database()
                logs = log_sequence.LogSequence({'message': 'x' * 1024} for _ in range(count))
                old = {'revision': 1, 'agentTasks': [], 'executions': [{'id': 'build', 'logs': logs}]}
                with connection:
                    store.save(connection, old)
                new = {**old, 'revision': 2, 'executions': [
                    {**old['executions'][0], 'logs': logs.appended({'message': 'new'})}]}
                observed = RecordingConnection(connection)
                with patch.object(log_sequence.LogSequence, '__iter__', side_effect=AssertionError('iterated old logs')), \
                        patch.object(log_sequence, '_different_chunks', wraps=log_sequence._different_chunks) as compared, connection:
                    stats = store.save(observed, new, old)
                self.assertLessEqual(compared.call_count, 15)
                self.assertEqual(stats['rows'], 2)
                self.assertEqual(len(observed.statements), 2)
                self.assertLessEqual(stats['bytes'], 140_000)
                self.assertEqual(store.load(connection), new)
                budgets.append(stats)
        self.assertEqual(budgets[0], budgets[1])


if __name__ == "__main__":
    unittest.main()
