import json
import io
import sqlite3
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import server as s
from log_sequence import LogSequence


class StatePerformanceTests(unittest.TestCase):
    def setUp(self):
        state = s.default_state_copy()
        state['executions'] = [{'id': 'build', 'taskId': 'task', 'status': 'building',
                                'logs': [{'time': 'old', 'message': 'original'}]}]
        self.patches = [patch.object(s, 'STATE_CACHE', state),
                        patch.object(s, 'PENDING_STATE_PAYLOAD', None),
                        patch.object(s, 'PENDING_STATE_REVISION', 0),
                        patch.object(s, 'LAST_PERSISTED_REVISION', -1),
                        patch.object(s, 'LAST_PERSISTED_STATE', None),
                        patch.object(s, 'LAST_PERSISTED_TARGET', None),
                        patch.object(s, 'STATE_WRITE_EVENT', threading.Event()),
                        patch.object(s, 'WS_CLIENTS', set())]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_hot_paths_do_not_copy_or_serialize_history(self):
        token = s.issue_session_token(s.STATE_CACHE['users'][0])
        original_copy = s.copy.deepcopy

        def copy_without_history(value, *args, **kwargs):
            if value is s.STATE_CACHE or value is s.STATE_CACHE['executions']:
                raise AssertionError('copied execution history')
            return original_copy(value, *args, **kwargs)

        with patch.object(s.copy, 'deepcopy', side_effect=copy_without_history), \
                patch.object(s, 'state_payload', side_effect=AssertionError('foreground serialization')):
            s.set_execution_status('build', 'building', stage='执行编译', progress=35)
            s.set_execution_commit_info('build', {'hash': 'abc'})
            s.append_log('build', 'new')
            self.assertFalse(s.is_execution_cancelled('build'))
            user, _ = s.session_user(token)
            self.assertEqual(user['username'], s.STATE_CACHE['users'][0]['username'])
        with patch.object(s, 'persist_state_snapshot') as persist:
            s.flush_pending_state_write()
            self.assertEqual(persist.call_args.args[0]['executions'][0]['logs'][-1]['message'], 'new')

    def test_generations_and_read_copies_are_isolated(self):
        old = s.state_snapshot()
        s.append_log('build', 'new')
        queued = s.PENDING_STATE_PAYLOAD
        self.assertEqual(len(old['executions'][0]['logs']), 1)
        copy = s.read_state()
        copy['executions'][0]['logs'][0]['message'] = 'changed externally'
        self.assertEqual(old['executions'][0]['logs'][0]['message'], 'original')
        s.mutate_state(lambda state: state['executions'][0].update(status='cancelled'))
        self.assertTrue(s.is_execution_cancelled('build'))
        self.assertEqual(queued['executions'][0]['status'], 'building')
        self.assertEqual(len(s.STATE_CACHE['executions'][0]['logs']), 2)

    def test_metadata_updates_do_not_copy_or_read_the_execution_log_list(self):
        class UnreadableLogs(list):
            def __iter__(self):
                raise AssertionError('metadata update iterated historical logs')
            def __getitem__(self, key):
                raise AssertionError('metadata update read historical logs')
            def __deepcopy__(self, memo):
                raise AssertionError('metadata update copied historical logs')
        original = s.STATE_CACHE
        logs = UnreadableLogs([{'message': 'historical'}])
        original['executions'][0]['logs'] = logs
        s.set_execution_commit_info('build', {'hash': 'abc'})
        s.set_execution_status('build', 'building', stage='compile', progress=35)
        self.assertIs(s.STATE_CACHE['executions'][0]['logs'], logs)
        self.assertEqual(s.STATE_CACHE['executions'][0]['commitHash'], 'abc')
        self.assertNotIn('commitHash', original['executions'][0])
        self.assertNotIn('progress', original['executions'][0])

    def test_status_message_still_gets_its_own_log_list(self):
        original = s.STATE_CACHE
        s.set_execution_status('build', 'building', 'stage changed', stage='compile', progress=35)
        current = s.STATE_CACHE
        self.assertIsNot(current['executions'][0]['logs'], original['executions'][0]['logs'])
        self.assertEqual(len(original['executions'][0]['logs']), 1)
        self.assertEqual(current['executions'][0]['logs'][-1]['message'], 'stage changed')

    def test_slow_persistence_does_not_block_logs_or_session(self):
        entered, release = threading.Event(), threading.Event()
        saved = []
        def persist(payload):
            saved.append(payload)
            entered.set()
            release.wait(3)
        s.append_log('build', 'first')
        token = s.issue_session_token(s.STATE_CACHE['users'][0])
        with patch.object(s, 'persist_state_snapshot', side_effect=persist):
            thread = threading.Thread(target=s.flush_pending_state_write)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                s.append_log('build', 'second')
                s.session_user(token)
                self.assertFalse(s.is_execution_cancelled('build'))
                self.assertEqual(len(saved[0]['executions'][0]['logs']), 2)
            finally:
                release.set()
                thread.join(3)
            s.flush_pending_state_write()
        self.assertEqual(len(saved[-1]['executions'][0]['logs']), 3)

    def test_failed_save_keeps_newest_pending_revision_and_retries(self):
        s.append_log('build', 'first')
        def fail(_payload):
            s.append_log('build', 'second')
            raise OSError('offline')
        with patch.object(s, 'persist_state_snapshot', side_effect=fail):
            with self.assertRaises(OSError):
                s.flush_pending_state_write()
        with patch.object(s, 'persist_state_snapshot') as persist:
            s.flush_pending_state_write()
        self.assertEqual(len(persist.call_args.args[0]['executions'][0]['logs']), 3)

    def test_old_snapshot_cannot_overwrite_newer_save(self):
        s.append_log('build', 'first')
        old, revision = s.PENDING_STATE_PAYLOAD, s.PENDING_STATE_REVISION
        s.append_log('build', 'second')
        with patch.object(s, 'persist_state_snapshot') as persist:
            s.flush_pending_state_write()
            s.persist_queued_state(old, revision)
            self.assertEqual(persist.call_count, 1)

    def test_concurrent_logs_preserve_all_messages_and_frame_order(self):
        with patch.object(s, 'broadcast_ws') as broadcast:
            threads = [threading.Thread(target=lambda n=n: s.append_log('build', str(n))) for n in range(30)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)
        logs = s.STATE_CACHE['executions'][0]['logs']
        self.assertEqual({item['message'] for item in logs[1:]}, {str(n) for n in range(30)})
        self.assertEqual([call.args[0]['logCount'] for call in broadcast.call_args_list], list(range(2, 32)))

    def test_compact_http_and_unchanged_revision_preserve_full_logs(self):
        handler = s.Handler.__new__(s.Handler)
        handler.path = '/api/state?compact=1'
        handler.command = 'GET'
        handler.windows_get = Mock(return_value=False)
        handler.require_session_user = Mock(return_value={'username': 'admin'})
        handler.send_json = Mock()
        with patch.object(s, 'read_state', side_effect=AssertionError('full read')):
            handler.do_GET()
            response = handler.send_json.call_args.args[0]
            self.assertEqual(response['executions'][0]['logs'], [])
            self.assertEqual(response['executions'][0]['logCount'], 1)
            self.assertNotIn('password', response['users'][0])
            handler.path = '/api/state?compact=1&revision=' + str(s.STATE_CACHE.get('revision') or 0)
            with patch.object(s, 'client_state', side_effect=AssertionError('unchanged state rendered')):
                handler.do_GET()
            self.assertTrue(handler.send_json.call_args.args[0]['unchanged'])
        self.assertEqual(len(s.STATE_CACHE['executions'][0]['logs']), 1)

    def test_configuration_and_enqueue_do_not_copy_old_log_entries(self):
        class HistoricalLog(dict):
            def __deepcopy__(self, memo):
                raise AssertionError('historical log copied')
        original = s.STATE_CACHE['executions'][0]
        original['logs'] = [HistoricalLog(message='old')]
        s.mutate_state(lambda state: state['auditLogs'].append({'action': 'config'}), read_only_history=True)
        self.assertIs(s.STATE_CACHE['executions'][0], original)
        s.mutate_state(lambda state: state['executions'].insert(0, {'id': 'next', 'logs': []}), read_only_history=True)
        self.assertIs(s.STATE_CACHE['executions'][1], original)

    def test_failed_runtime_mutation_does_not_modify_published_generation(self):
        old = s.state_snapshot()
        def fail(state):
            state['executions'][0]['logs'].append({'message': 'uncommitted'})
            state['executions'][0]['status'] = 'failed'
            raise ValueError('abort')
        with self.assertRaises(ValueError):
            s.mutate_execution('build', fail)
        self.assertIs(s.STATE_CACHE, old)
        self.assertEqual(len(old['executions'][0]['logs']), 1)

    def test_state_patch_contains_only_changed_sections_and_redacts(self):
        with patch.object(s, 'WS_CLIENTS', {Mock(delta=True)}), patch.object(s, 'broadcast_ws') as broadcast:
            old = s.state_snapshot()
            s.set_execution_status('build', 'building', stage='compile')
            message = broadcast.call_args.args[0]
            self.assertEqual(message['type'], 'state_patch')
            self.assertEqual(message['baseRevision'], old.get('revision', 0))
            self.assertEqual(set(message['patch']), {'executions', 'revision'})
            s.mutate_state(lambda state: state['secrets'].append({'id': 'x', 'secret': 'private'}), read_only_history=True)
            message = broadcast.call_args.args[0]
            self.assertEqual(message['patch']['secrets'][-1]['secret'], '')
            self.assertTrue(message['patch']['secrets'][-1]['hasSecret'])

    def test_idle_scheduler_does_not_copy_or_write_state(self):
        with patch.object(s, 'read_state', side_effect=AssertionError('full read')), \
                patch.object(s, 'mutate_state', side_effect=AssertionError('idle write')):
            s.trigger_due_schedules()
            s.recover_waiting_deployments()
            s.recover_stuck_agent_deployments()

    def test_saved_logs_and_configuration_survive_reload(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(s, 'DB_PATH', Path(folder) / 'state.db'), \
                patch.object(s, 'use_postgres', return_value=False):
            s.append_log('build', 'persist me')
            s.mutate_state(lambda state: state['platformSettings'].update(imageNamespace='saved'))
            s.flush_pending_state_write()
            restored = s.read_raw_state()
        self.assertEqual(restored['executions'][0]['logs'][-1]['message'], 'persist me')
        self.assertEqual(restored['platformSettings']['imageNamespace'], 'saved')

    def test_committed_runtime_save_only_executes_changed_data_statements(self):
        statements = []
        real_connect = sqlite3.connect
        def traced_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(s, 'DB_PATH', Path(folder) / 'state.db'), \
                patch.object(s, 'use_postgres', return_value=False), \
                patch.object(s.sqlite3, 'connect', side_effect=traced_connect):
            old = s.STATE_CACHE
            s.persist_state_snapshot(old)
            statements.clear()
            new = {**old, 'revision': 1, 'platformSettings': {**old['platformSettings'], 'imageNamespace': 'warm'}}
            stats = s.persist_state_snapshot(new)
            self.assertEqual(stats['rows'], 2)
            commands = [sql for sql in statements if not sql.startswith(('BEGIN', 'COMMIT'))]
            self.assertEqual(len(commands), 2)
            self.assertTrue(all(sql.startswith('INSERT INTO state_sections ') for sql in commands))
            restored = s.read_raw_state()
        self.assertEqual(restored['platformSettings']['imageNamespace'], 'warm')
        self.assertEqual(restored['executions'][0]['logs'], old['executions'][0]['logs'])

    def test_unchanged_mutation_does_not_save_and_session_revocation_still_works(self):
        token = s.issue_session_token(s.STATE_CACHE['users'][0])
        with patch.object(s, 'enqueue_state_write') as enqueue, \
                patch.object(s, 'canonical_json', side_effect=AssertionError('serialized history')):
            s.mutate_state(lambda state: None, detect_changes=True)
            enqueue.assert_not_called()
        s.mutate_state(lambda state: state['users'][0].update(authVersion=999))
        with self.assertRaisesRegex(ValueError, '权限已变更'):
            s.session_user(token)

    def test_streaming_and_callback_appends_do_not_iterate_or_copy_old_logs(self):
        s.STATE_CACHE['executions'][0]['logs'] = LogSequence({'message': str(i)} for i in range(16383))
        original = s.state_snapshot()
        with patch.object(LogSequence, '__iter__', side_effect=AssertionError('scanned old logs')), \
                patch.object(LogSequence, '__deepcopy__', side_effect=AssertionError('copied old logs')):
            s.append_log('build', 'streamed')
            s.set_execution_status('build', 'building', message='stage changed', stage='compile')
            before_failed = s.state_snapshot()
            def fail(state):
                state['executions'][0]['logs'].append({'message': 'uncommitted'})
                raise ValueError('abort')
            with self.assertRaisesRegex(ValueError, 'abort'):
                s.mutate_execution('build', fail)
        self.assertIs(s.STATE_CACHE, before_failed)
        self.assertEqual(len(original['executions'][0]['logs']), 16383)
        self.assertEqual(s.STATE_CACHE['executions'][0]['logs'][-2]['message'], 'streamed')
        self.assertEqual(s.STATE_CACHE['executions'][0]['logs'][-1]['message'], 'stage changed')

    def test_persistent_log_summaries_are_bounded_and_full_log_json_remains_compatible(self):
        entries = [{'message': str(i)} for i in range(1000)]
        execution = {**s.STATE_CACHE['executions'][0], 'logs': LogSequence(entries)}
        with patch.object(LogSequence, '__iter__', side_effect=AssertionError('full scan for summary')):
            summary = s.execution_summary(execution, compact=True)
        self.assertEqual(summary['logCount'], 1000)
        self.assertEqual(summary['latestLog']['message'], '999')
        self.assertEqual(summary['logs'], [])
        self.assertEqual(json.loads(s.state_payload({'executions': [execution]}))['executions'][0]['logs'], entries)
        handler = s.Handler.__new__(s.Handler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        handler.send_json({'logs': execution['logs']})
        self.assertEqual(json.loads(handler.wfile.getvalue())['logs'], entries)
        with patch.object(s, 'STATE_CACHE', {**s.STATE_CACHE, 'executions': [execution]}):
            external = s.read_state()
            external['executions'][0]['logs'][0]['message'] = 'external'
        self.assertEqual(execution['logs'][0]['message'], '0')

    def test_five_execution_log_streams_persist_and_recover_independently(self):
        s.STATE_CACHE['executions'] = [
            {'id': str(i), 'taskId': str(i), 'status': 'building', 'logs': LogSequence([{'message': 'initial'}])}
            for i in range(5)]
        original = s.state_snapshot()
        barrier = threading.Barrier(6)
        errors = []
        def stream(index):
            try:
                barrier.wait(3)
                for n in range(129):
                    s.append_log(str(index), f'{index}:{n}')
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=stream, args=(i,)) for i in range(5)]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(s, 'DB_PATH', Path(folder) / 'state.db'), \
                patch.object(s, 'use_postgres', return_value=False):
            s.persist_state_snapshot(original)
            for worker in workers:
                worker.start()
            barrier.wait(3)
            for worker in workers:
                worker.join(5)
                self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            s.flush_pending_state_write()
            restored = s.read_raw_state()
            # Switching database targets must initialize, not reuse a foreign baseline.
            with patch.object(s, 'DB_PATH', Path(folder) / 'other.db'):
                s.persist_state_snapshot(s.state_snapshot())
                self.assertEqual(s.read_raw_state(), restored)
            with patch.object(s, 'STATE_CACHE', None):
                restarted = s.state_snapshot()
                self.assertIsInstance(restarted['executions'][0]['logs'], LogSequence)
                s.append_log('0', 'after restart')
                s.flush_pending_state_write()
                self.assertEqual(s.read_raw_state()['executions'][0]['logs'][-1]['message'], 'after restart')
                self.assertEqual(len(restarted['executions'][0]['logs']), 130)
        for index, execution in enumerate(restored['executions']):
            self.assertEqual([entry['message'] for entry in execution['logs']],
                             ['initial', *[f'{index}:{n}' for n in range(129)]])
        self.assertTrue(all(len(execution['logs']) == 1 for execution in original['executions']))


if __name__ == '__main__':
    unittest.main()
