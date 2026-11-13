import copy
import json
import sqlite3
import unittest

import state_store as store
from log_sequence import LogSequence


class IncrementalStoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.state = {"revision": 1, "tasks": [{"id": 1}], "secrets": [{"secret": "keep-me"}],
                      "executions": [{"id": "a", "taskId": 1, "status": "building",
                                      "logs": [{"message": "x" * 1000} for _ in range(1000)]}],
                      "agentTasks": []}

    def save(self, state, previous=None):
        with self.conn:
            return store.save(self.conn, state, previous)

    def test_legacy_upgrade_is_atomic_and_keeps_backup(self):
        legacy = json.dumps(self.state)
        self.conn.execute("INSERT INTO app_state VALUES ('state', ?)", (legacy,))
        self.conn.commit()
        self.assertIsNone(store.load(self.conn))
        self.save(self.state)
        self.assertEqual(store.load(self.conn), self.state)
        self.assertEqual(self.conn.execute("SELECT value FROM app_state WHERE key='state'").fetchone()[0], legacy)

    def test_append_writes_only_revision_and_last_log_chunk(self):
        self.save(self.state)
        new = {**self.state, "revision": 2, "executions": [
            {**self.state["executions"][0], "logs": [*self.state["executions"][0]["logs"], {"message": "new"}]}]}
        stats = self.save(new, self.state)
        self.assertEqual(stats["rows"], 2)
        self.assertLess(stats["bytes"], 130_000)
        self.assertEqual(store.load(self.conn), new)
        # Replaying a committed generation is idempotent, including after restart.
        self.assertEqual(self.save(new)["rows"], 0)

    def test_replacement_truncation_reorder_and_deletion(self):
        second = {"id": "b", "logs": [{"message": "b"}]}
        self.state["executions"].append(second)
        self.save(self.state)
        new = copy.deepcopy(self.state)
        new["executions"].reverse()
        new["executions"][1]["logs"] = [{"message": "replacement"}]
        del new["secrets"]
        self.save(new, self.state)
        self.assertEqual(store.load(self.conn), new)
        final = {**new, "executions": []}
        self.save(final, new)
        self.assertEqual(store.load(self.conn), final)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM state_log_chunks").fetchone()[0], 0)

    def test_failure_rolls_back_and_retry_uses_committed_baseline(self):
        self.save(self.state)
        new = copy.deepcopy(self.state)
        new["revision"] = 2
        new["executions"][0]["logs"].append({"message": "new"})
        real = self.conn

        class FailingConnection:
            def execute(self, sql, args=()):
                if sql.startswith("INSERT INTO state_log_chunks"):
                    raise sqlite3.OperationalError("disk failure")
                return real.execute(sql, args)

        with self.assertRaises(sqlite3.OperationalError), self.conn:
            store.save(FailingConnection(), new, self.state)
        self.assertEqual(store.load(self.conn), self.state)
        self.save(new)
        self.assertEqual(store.load(self.conn), new)

    def test_status_update_does_not_serialize_existing_logs(self):
        self.save(self.state)
        new = {**self.state, "executions": [{**self.state["executions"][0], "status": "success"}]}
        stats = self.save(new, self.state)
        self.assertEqual(stats["rows"], 1)
        self.assertLess(stats["bytes"], 100)

    def test_persistent_logs_coalesce_rollback_retry_replace_and_truncate(self):
        self.state['executions'][0]['logs'] = LogSequence({'message': str(i)} for i in range(255))
        self.save(self.state)
        logs = self.state['executions'][0]['logs']
        for i in range(255, 513):
            logs = logs.appended({'message': str(i)})
        new = {**self.state, 'revision': 2, 'executions': [{**self.state['executions'][0], 'logs': logs}]}
        # A failed coalesced flush must not advance the committed baseline.
        with self.assertRaisesRegex(RuntimeError, 'abort'), self.conn:
            store.save(self.conn, new, self.state)
            raise RuntimeError('abort')
        self.assertEqual(store.load(self.conn), self.state)
        self.save(new, self.state)
        self.assertEqual(store.load(self.conn), new)
        self.assertEqual(self.save(new)['rows'], 0)  # Reconcile after restart.
        replacement = {**new, 'executions': [{**new['executions'][0],
            'logs': LogSequence([{'message': 'replacement'}, *logs[1:129]])}]}
        self.save(replacement, new)
        self.assertEqual(store.load(self.conn), replacement)
        empty = {**replacement, 'executions': [{**replacement['executions'][0], 'logs': LogSequence()}]}
        self.save(empty, replacement)
        self.assertEqual(store.load(self.conn), empty)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM state_log_chunks').fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
