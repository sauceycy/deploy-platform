import json
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import server as s


class StatePerformanceTests(unittest.TestCase):
    def setUp(self):
        state = s.default_state_copy()
        state['executions'] = [{'id': 'build', 'taskId': 'task', 'status': 'building',
                                'logs': [{'time': 'old', 'message': 'original'}]}]
        self.patches = [patch.object(s, 'STATE_CACHE', state),
                        patch.object(s, 'PENDING_STATE_PAYLOAD', None),
                        patch.object(s, 'PENDING_STATE_REVISION', 0),
                        patch.object(s, 'LAST_PERSISTED_REVISION', -1),
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
            s.append_log('build', 'new')
            self.assertFalse(s.is_execution_cancelled('build'))
            user, _ = s.session_user(token)
            self.assertEqual(user['username'], s.STATE_CACHE['users'][0]['username'])
        with patch.object(s, 'persist_state_payload') as persist:
            s.flush_pending_state_write()
            self.assertEqual(json.loads(persist.call_args.args[0])['executions'][0]['logs'][-1]['message'], 'new')

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

    def test_slow_persistence_does_not_block_logs_or_session(self):
        entered, release = threading.Event(), threading.Event()
        saved = []
        def persist(payload):
            saved.append(json.loads(payload))
            entered.set()
            release.wait(3)
        s.append_log('build', 'first')
        token = s.issue_session_token(s.STATE_CACHE['users'][0])
        with patch.object(s, 'persist_state_payload', side_effect=persist):
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
        with patch.object(s, 'persist_state_payload', side_effect=fail):
            with self.assertRaises(OSError):
                s.flush_pending_state_write()
        with patch.object(s, 'persist_state_payload') as persist:
            s.flush_pending_state_write()
        self.assertEqual(len(json.loads(persist.call_args.args[0])['executions'][0]['logs']), 3)

    def test_old_snapshot_cannot_overwrite_newer_save(self):
        s.append_log('build', 'first')
        old, revision = s.PENDING_STATE_PAYLOAD, s.PENDING_STATE_REVISION
        s.append_log('build', 'second')
        with patch.object(s, 'persist_state_payload') as persist:
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

    def test_unchanged_mutation_does_not_save_and_session_revocation_still_works(self):
        token = s.issue_session_token(s.STATE_CACHE['users'][0])
        with patch.object(s, 'enqueue_state_write') as enqueue, \
                patch.object(s, 'canonical_json', side_effect=AssertionError('serialized history')):
            s.mutate_state(lambda state: None, detect_changes=True)
            enqueue.assert_not_called()
        s.mutate_state(lambda state: state['users'][0].update(authVersion=999))
        with self.assertRaisesRegex(ValueError, '权限已变更'):
            s.session_user(token)


if __name__ == '__main__':
    unittest.main()
