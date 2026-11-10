from contextlib import nullcontext
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

import server as s
import test_parallel_execution as parallel


class BuildMemoryTests(unittest.TestCase):
    def setUp(self):
        parallel.ParallelStateTests.setUp(self)

    def handler(self, path, body, actor='admin'):
        handler = s.Handler.__new__(s.Handler)
        handler.path = path
        handler.require_session_actor = Mock(return_value=actor)
        handler.read_json_body = Mock(return_value=body)
        handler.send_json = Mock()
        return handler

    def test_memory_validation_canonicalizes_sizes_and_rejects_unsafe_values(self):
        for value, expected in (('8G', '8g'), ('1.5g', '1536m'), ('512MiB', '512m'),
                                ('8589934592', '8g'), ('', ''), (None, '')):
            with self.subTest(value=value):
                self.assertEqual(s.normalize_sdk_memory(value), expected)
        for value in ('0', '5m', '-1g', '8g --privileged', 'nan', '1e10', '1.00000000001m', '9' * 33, True, {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                s.normalize_sdk_memory(value)

    def test_old_settings_fall_back_to_environment_and_reset_restores_it(self):
        s.STATE_CACHE['platformSettings'].pop('sdkBuildMemory', None)
        with patch.object(s, 'SDK_BUILD_MEMORY', '4g'):
            self.assertEqual(s.sdk_memory_for_execution(s.STATE_CACHE), '4g')
            s.save_build_memory({'sdkBuildMemory': '6g'}, 'admin')
            self.assertEqual(s.sdk_memory_for_execution(s.STATE_CACHE), '6g')
            s.save_build_memory({'sdkBuildMemory': ''}, 'admin')
            self.assertEqual(s.sdk_memory_for_execution(s.STATE_CACHE), '4g')
        self.assertEqual(s.read_raw_state()['platformSettings']['sdkBuildMemory'], '')
        with patch.object(s, 'SDK_BUILD_MEMORY', '0'):
            self.assertEqual(s.sdk_memory_for_execution(s.STATE_CACHE), '')

    def test_save_is_durable_before_ack_and_storage_wait_is_outside_state_lock(self):
        persist = s.persist_queued_state

        def verify(snapshot, revision):
            self.assertFalse(s.STATE_LOCK._is_owned())
            persist(snapshot, revision)

        handler = self.handler('/api/settings/build-memory', {'sdkBuildMemory': '10g'})
        with patch.object(s, 'persist_queued_state', side_effect=verify):
            handler.do_PUT()
        self.assertTrue(handler.send_json.call_args.args[0]['ok'])
        self.assertEqual(s.read_raw_state()['platformSettings']['sdkBuildMemory'], '10g')
        s.STATE_CACHE = None
        self.assertEqual(s.state_snapshot()['platformSettings']['sdkBuildMemory'], '10g')

    def test_storage_failure_does_not_ack_and_same_value_can_be_retried(self):
        handler = self.handler('/api/settings/build-memory', {'sdkBuildMemory': '10g'})
        with patch.object(s, 'persist_state_snapshot', side_effect=OSError('offline')):
            handler.do_PUT()
        self.assertEqual(handler.send_json.call_args.kwargs['status'], 503)
        handler.do_PUT()
        self.assertTrue(handler.send_json.call_args.args[0]['ok'])
        self.assertEqual(s.read_raw_state()['platformSettings']['sdkBuildMemory'], '10g')

    def test_non_global_user_and_user_without_permission_cannot_change_default(self):
        for role, global_access in (('developer', False), ('viewer', True)):
            with self.subTest(role=role):
                s.STATE_CACHE['users'].append({'username': role, 'role': role, 'globalAccess': global_access,
                                               'organizationIds': ['default']})
                with self.assertRaises(ValueError):
                    s.save_build_memory({'sdkBuildMemory': '10g'}, role)
        self.assertEqual(s.STATE_CACHE['platformSettings']['sdkBuildMemory'], '')

    def test_unrelated_full_state_save_cannot_overwrite_new_memory_setting(self):
        stale = s.client_state(s.STATE_CACHE)
        s.save_build_memory({'sdkBuildMemory': '10g'}, 'admin')
        stale['platformSettings']['sdkBuildMemory'] = '2g'
        handler = self.handler('/api/state', {'state': stale})
        handler.do_PUT()
        self.assertTrue(handler.send_json.call_args.args[0]['ok'])
        self.assertEqual(s.STATE_CACHE['platformSettings']['sdkBuildMemory'], '10g')

    def test_memory_update_shares_history_and_only_writes_small_configuration_partitions(self):
        class HistoricalExecution(dict):
            def __deepcopy__(self, memo):
                raise AssertionError('copied historical execution')

        s.STATE_CACHE['executions'] = [HistoricalExecution(id=str(i), taskId='0', status='success',
            logs=[{'message': 'x' * 1024}] * 1000) for i in range(50)]
        s.merge_defaults(s.STATE_CACHE)
        old = s.STATE_CACHE
        s.persist_state_snapshot(old)
        original_save = s.persist_state_snapshot
        stats = []

        def save(snapshot):
            result = original_save(snapshot)
            stats.append(result)
            return result

        with patch.object(s, 'read_state', side_effect=AssertionError('full read')), \
                patch.object(s, 'persist_state_snapshot', side_effect=save):
            s.save_build_memory({'sdkBuildMemory': '6g'}, 'admin')
        self.assertTrue(all(before is after for before, after in zip(old['executions'], s.STATE_CACHE['executions'])))
        self.assertEqual(stats[0]['rows'], 3)  # platformSettings, auditLogs and revision
        self.assertLess(stats[0]['bytes'], 4096)

    def test_five_config_builds_keep_independent_enqueued_memory_after_defaults_change(self):
        task = s.STATE_CACHE['tasks'][0]
        task['deployConfigs'] = [{'id': f'cfg-{i}', 'name': f'config-{i}', 'sdkBuildMemory': value}
                                 for i, value in enumerate(('2g', '4g', '6g', '8g', ''))]
        s.save_build_memory({'sdkBuildMemory': '10g'}, 'admin')
        records = [s.create_execution('0', 'admin', 'main', f'cfg-{i}')[0] for i in range(5)]
        s.save_build_memory({'sdkBuildMemory': '12g'}, 'admin')
        s.STATE_CACHE = None
        current = s.state_snapshot()
        task = current['tasks'][0]
        records = [s.find_by_id(current['executions'], record['id']) for record in records]
        barrier = threading.Barrier(5)
        commands, errors = {}, []

        def run(command, execution_id, **kwargs):
            commands[execution_id] = command[command.index('--memory') + 1]
            barrier.wait(3)
            return 0, 'ok', 0

        def worker(record):
            try:
                effective = s.task_for_execution(task, record)
                s.run_sdk_command(record['id'], effective, 'compile', Path(self.temp.name), {})
            except Exception as exc:
                errors.append(exc)

        with patch.object(s, 'build_cache_config', return_value=([], {}, [])), \
                patch.object(s, 'builder_image', return_value='sdk:test'), \
                patch.object(s, 'docker_login_env_for_secret', return_value=None), \
                patch.object(s, 'sdk_pull_secret', return_value=None), patch.object(s, 'append_log'), \
                patch.object(s, 'cache_lock', side_effect=lambda *a: nullcontext()), \
                patch.object(s, 'run_command_stream', side_effect=run):
            workers = [threading.Thread(target=worker, args=(record,)) for record in records]
            for thread in workers:
                thread.start()
            for thread in workers:
                thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in workers))
        self.assertEqual(errors, [])
        self.assertEqual([commands[record['id']] for record in records], ['2g', '4g', '6g', '8g', '10g'])
        self.assertEqual(s.create_execution('1', 'admin', 'main')[0]['sdkBuildMemory'], '12g')

    def test_invalid_override_rejects_config_and_old_execution_uses_current_fallback(self):
        with self.assertRaises(ValueError):
            s.normalize_deploy_config({'sdkBuildMemory': 'unlimited'})
        old = {'deployConfigSnapshot': {'id': 'old', 'sdkBuildMemory': '4g'}}
        task = s.task_for_execution(s.STATE_CACHE['tasks'][0], old)
        self.assertNotIn('_sdkBuildMemory', task)
        self.assertEqual(s.sdk_memory_for_execution(s.STATE_CACHE, task), '4g')

    def test_old_task_editor_keeps_override_and_explicit_empty_restores_inheritance(self):
        payload = {'name': 'app', 'repo': 'https://example.invalid/repo.git', 'buildCommand': 'compile',
                   'language': 'java', 'sdk': 'jdk17', 'deployConfigs': [{'id': 'default', 'sdkBuildMemory': '8g'}]}
        s.save_task_config('0', payload, 'admin')
        del payload['deployConfigs'][0]['sdkBuildMemory']
        task, _ = s.save_task_config('0', payload, 'admin')
        self.assertEqual(task['deployConfigs'][0]['sdkBuildMemory'], '8g')
        payload['deployConfigs'][0]['sdkBuildMemory'] = ''
        task, _ = s.save_task_config('0', payload, 'admin')
        self.assertEqual(task['deployConfigs'][0]['sdkBuildMemory'], '')


if __name__ == '__main__':
    unittest.main()
