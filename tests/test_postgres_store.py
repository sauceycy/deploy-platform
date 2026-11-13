"""Real PostgreSQL contracts, enabled only with a dedicated TEST_POSTGRES_URL.

Each test owns a random schema. Never use the platform/production database URL.
CI supplies an ephemeral PostgreSQL service; absent local configuration skips.
"""
import copy
import os
import unittest
import uuid

import state_store as store
from log_sequence import LogSequence


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "dedicated TEST_POSTGRES_URL not configured")
class PostgresStoreTests(unittest.TestCase):
    def setUp(self):
        # A configured environment must fail on missing driver/unavailable DB.
        import psycopg
        from psycopg import sql

        self.psycopg = psycopg
        self.sql = sql
        self.schema = "regression_" + uuid.uuid4().hex
        self.connection = psycopg.connect(os.environ["TEST_POSTGRES_URL"], autocommit=True, connect_timeout=5)
        self.addCleanup(self.cleanup_schema)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.connection.autocommit = False
        with self.connection.transaction():
            self.connection.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.state = {"revision": 1, "tasks": [{"id": "task"}], "agentTasks": [],
                      "executions": [{"id": "one", "taskId": "task", "status": "building",
                                      "logs": [{"message": "x" * 1024} for _ in range(511)]}]}
        self.legacy = store.encode(self.state)
        with self.connection.transaction():
            self.connection.execute("INSERT INTO app_state VALUES ('state', %s)", (self.legacy,))

    def cleanup_schema(self):
        try:
            self.connection.rollback()
            self.connection.autocommit = True
            self.connection.execute(self.sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(self.sql.Identifier(self.schema)))
        finally:
            self.connection.close()

    def save(self, state, previous=None):
        with self.connection.transaction():
            return store.save(self.connection, state, previous, postgres=True)

    def reloaded(self):
        # A fresh session proves the data/format marker actually committed.
        with self.psycopg.connect(os.environ["TEST_POSTGRES_URL"], autocommit=True, connect_timeout=5) as connection:
            connection.execute(self.sql.SQL("SET search_path TO {}").format(self.sql.Identifier(self.schema)))
            return store.load(connection)

    def test_migration_rollback_and_retry_preserve_legacy_backup(self):
        with self.assertRaisesRegex(RuntimeError, "abort migration"):
            with self.connection.transaction():
                store.save(self.connection, self.state, postgres=True)
                raise RuntimeError("abort migration")
        self.assertIsNone(self.reloaded())
        self.save(self.state)
        self.assertEqual(self.reloaded(), self.state)
        with self.connection.transaction():
            legacy = self.connection.execute("SELECT value FROM app_state WHERE key='state'").fetchone()[0]
        self.assertEqual(legacy, self.legacy)

    def test_append_and_metadata_have_bounded_payload_and_survive_reconnect(self):
        self.save(self.state)
        appended = copy.deepcopy(self.state)
        appended["revision"] = 2
        appended["executions"][0]["logs"].append({"message": "new"})
        stats = self.save(appended, self.state)
        self.assertEqual(stats["rows"], 2)
        self.assertLessEqual(stats["bytes"], 140_000)
        updated = {**appended, "executions": [{**appended["executions"][0], "status": "success"}]}
        stats = self.save(updated, appended)
        self.assertEqual(stats["rows"], 1)
        self.assertLessEqual(stats["bytes"], 256)
        self.assertEqual(self.reloaded(), updated)
        self.assertEqual(self.save(updated)["rows"], 0)

    def test_sql_error_rolls_back_prior_writes_and_retry_reconciles(self):
        self.save(self.state)
        changed = copy.deepcopy(self.state)
        changed["revision"] = 2
        changed["executions"][0]["logs"].append({"message": "new"})
        real = self.connection

        class FailingConnection:
            def execute(self, sql, args=()):
                if sql.startswith("INSERT INTO state_log_chunks"):
                    return real.execute("SELECT 1 / 0")
                return real.execute(sql, args)

        with self.assertRaises(self.psycopg.errors.DivisionByZero):
            with real.transaction():
                store.save(FailingConnection(), changed, self.state, postgres=True)
        self.assertEqual(self.reloaded(), self.state)
        self.save(changed)
        self.assertEqual(self.reloaded(), changed)

    def test_reorder_log_truncation_and_deletion(self):
        self.state["executions"].append({"id": "two", "logs": [{"message": "second"}]})
        self.save(self.state)
        changed = copy.deepcopy(self.state)
        changed["executions"].reverse()
        changed["executions"][1]["logs"] = [{"message": "replacement"}]
        del changed["tasks"]
        self.save(changed, self.state)
        self.assertEqual(self.reloaded(), changed)
        final = {**changed, "executions": []}
        self.save(final, changed)
        self.assertEqual(self.reloaded(), final)
        with self.connection.transaction():
            self.assertEqual(self.connection.execute("SELECT count(*) FROM state_log_chunks").fetchone()[0], 0)
            self.assertEqual(self.connection.execute("SELECT count(*) FROM state_records").fetchone()[0], 0)

    def test_warm_baseline_avoids_schema_marker_and_unchanged_history_queries(self):
        self.save(self.state)
        new = {**self.state, 'revision': 2}
        statements = []
        real = self.connection
        class ObservedConnection:
            def execute(self, sql, args=()):
                statements.append(sql)
                return real.execute(sql, args)
        with real.transaction():
            stats = store.save(ObservedConnection(), new, self.state, postgres=True)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith('INSERT INTO state_sections '))
        self.assertEqual(stats['rows'], 1)
        self.assertEqual(self.reloaded(), new)

    def test_persistent_blocks_rollback_coalesce_and_reload(self):
        self.state['executions'][0]['logs'] = LogSequence(self.state['executions'][0]['logs'])
        self.save(self.state)
        logs = self.state['executions'][0]['logs']
        for i in range(258):
            logs = logs.appended({'message': str(i)})
        changed = {**self.state, 'revision': 2,
                   'executions': [{**self.state['executions'][0], 'logs': logs}]}
        with self.assertRaisesRegex(RuntimeError, 'abort'):
            with self.connection.transaction():
                store.save(self.connection, changed, self.state, postgres=True)
                raise RuntimeError('abort')
        self.assertEqual(self.reloaded(), self.state)
        self.save(changed, self.state)
        self.assertEqual(self.reloaded(), changed)
        truncated = {**changed, 'executions': [{**changed['executions'][0], 'logs': LogSequence(logs[-129:])}]}
        self.save(truncated, changed)
        self.assertEqual(self.reloaded(), truncated)
        self.assertEqual(self.save(truncated)['rows'], 0)


if __name__ == "__main__":
    unittest.main()
