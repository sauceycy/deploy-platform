from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import server as s


class AgentTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        state = s.default_state_copy()
        state['clusters'] = [{'id': 'c', 'name': 'cluster', 'organizationId': 'default'}]
        state['tasks'] = [dict(id=str(i), name=f'app-{i}', organizationId='default') for i in range(6)]
        self.now = datetime(2026, 11, 10, 20, 0, 0)
        for item in [patch.object(s, 'STATE_CACHE', state), patch.object(s, 'WS_CLIENTS', set()),
                     patch.object(s, 'DB_PATH', Path(self.temp.name) / 'state.db'),
                     patch.object(s, 'use_postgres', return_value=False), patch.object(s, 'PENDING_STATE_PAYLOAD', None),
                     patch.object(s, 'LAST_PERSISTED_STATE', None), patch.object(s, 'LAST_PERSISTED_TARGET', None),
                     patch.object(s, 'LAST_PERSISTED_REVISION', -1), patch.object(s, 'STATE_WRITE_EVENT', threading.Event()),
                     patch.object(s, 'send_notification'), patch.object(s, 'append_publish_commit_log')]:
            item.start()
            self.addCleanup(item.stop)
        self.clock = patch.object(s, 'datetime', wraps=datetime)
        self.clock_mock = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.clock_mock.now.side_effect = lambda: self.now

    def jobs(self, count=2, same_target=False, windows=False):
        state = s.STATE_CACHE
        for i in range(count):
            execution = s.create_execution_record(state, state['tasks'][i], 'admin', 'main')
            execution.update(status='deploying', stage='Agent 部署', progress=88, clusterResults={'cluster': 'pending'})
            state['agentTasks'].append({'id': f'a-{i}', 'executionId': execution['id'], 'taskId': str(i),
                'clusterName': 'cluster', 'status': 'pending', 'logs': [], 'createdAt': '2020-01-01 00:00:00',
                'payload': {'namespace': 'default', 'deployment': 'shared' if same_target else f'app-{i}'},
                **({'kind': 'windows'} if windows else {})})

    def test_long_target_wait_gets_full_claim_window_after_unlock_and_survives_reload(self):
        self.jobs(same_target=True)
        first = s.claim_agent_task('cluster', 'owner', 'request')
        old = s.state_snapshot()
        s.recover_stuck_agent_deployments()
        self.assertNotIn('claimWaitSince', old['agentTasks'][1])
        self.assertIsNone(s.STATE_CACHE['agentTasks'][1]['claimWaitSince'])
        # A stable blocked queue must not write on every scheduler tick.
        with patch.object(s, 'mutate_execution', side_effect=AssertionError('idle write')):
            s.recover_stuck_agent_deployments()
        s.persist_state_snapshot(s.STATE_CACHE)
        s.STATE_CACHE = None
        self.assertIsNone(s.state_snapshot()['agentTasks'][1]['claimWaitSince'])
        self.now += timedelta(hours=1)
        s.update_agent_result(first['id'], 'success', 'done', 'owner')
        s.recover_stuck_agent_deployments()
        second = s.STATE_CACHE['agentTasks'][1]
        self.assertEqual(second['claimWaitSince'], s.now_text())
        self.assertEqual(second['status'], 'pending')
        self.now += timedelta(seconds=s.AGENT_TASK_PENDING_TIMEOUT_SECONDS - 1)
        s.recover_stuck_agent_deployments()
        self.assertEqual(s.STATE_CACHE['agentTasks'][1]['status'], 'pending')
        self.assertEqual(s.claim_agent_task('cluster', 'new-owner')['id'], second['id'])

    def test_cluster_capacity_wait_is_paused_for_sixth_job(self):
        self.jobs(6)
        claims = [s.claim_agent_task('cluster', f'worker-{i}') for i in range(5)]
        s.recover_stuck_agent_deployments()
        self.assertIsNone(s.STATE_CACHE['agentTasks'][5]['claimWaitSince'])
        self.now += timedelta(minutes=30)
        s.update_agent_result(claims[0]['id'], 'success', 'done', 'worker-0')
        s.recover_stuck_agent_deployments()
        self.assertEqual(s.STATE_CACHE['agentTasks'][5]['status'], 'pending')
        self.assertIsNotNone(s.claim_agent_task('cluster', 'worker-0'))

    def test_legacy_agent_capacity_prevents_serial_queue_timeout(self):
        self.jobs(2)
        s.STATE_CACHE['agentHeartbeats'] = [{'cluster': 'cluster', 'time': s.now_text(), 'version': '0.1.0'}]
        first = s.claim_agent_task('cluster', 'legacy-owner')
        self.assertIsNone(s.claim_agent_task('cluster', 'other-slot'))
        s.recover_stuck_agent_deployments()
        self.assertIsNone(s.STATE_CACHE['agentTasks'][1]['claimWaitSince'])
        self.now += timedelta(minutes=10)
        s.update_agent_result(first['id'], 'success', 'done', 'legacy-owner')
        s.recover_stuck_agent_deployments()
        self.assertEqual(s.STATE_CACHE['agentTasks'][1]['status'], 'pending')
        self.assertEqual(s.claim_agent_task('cluster', 'legacy-owner')['id'], 'a-1')

    def test_advertised_five_slots_keep_default_parallel_capacity(self):
        self.jobs(6)
        s.STATE_CACHE['agentHeartbeats'] = [{'cluster': 'cluster', 'time': s.now_text(),
                                           'version': '0.2.0', 'maxConcurrentTasks': 5}]
        claims = [s.claim_agent_task('cluster', f'worker-{i}') for i in range(5)]
        self.assertTrue(all(claims))
        self.assertEqual(len({item['id'] for item in claims}), 5)
        self.assertIsNone(s.claim_agent_task('cluster', 'sixth'))
        s.recover_stuck_agent_deployments()
        self.assertEqual([item['status'] for item in s.STATE_CACHE['agentTasks']], ['running'] * 5 + ['pending'])

    def test_capacity_is_bounded_and_invalid_reports_fall_back_to_one(self):
        self.jobs(1)
        for value, expected in ((9999, 5), (0, 1), (-3, 1), ('invalid', 1), (None, 1)):
            with self.subTest(value=value):
                s.STATE_CACHE['agentHeartbeats'] = [{'cluster': 'cluster', 'maxConcurrentTasks': value}]
                self.assertEqual(s.cluster_agent_capacity(s.STATE_CACHE, 'cluster'), expected)

    def test_windows_server_queue_is_not_timed_out_while_running(self):
        self.jobs(windows=True)
        s.STATE_CACHE['agentTasks'][0].update(status='running', assignedAgent='windows-owner')
        s.recover_stuck_agent_deployments()
        self.assertIsNone(s.STATE_CACHE['agentTasks'][1]['claimWaitSince'])
        self.now += timedelta(hours=1)
        s.update_agent_result('a-0', 'success', 'done', 'windows-owner')
        s.recover_stuck_agent_deployments()
        self.assertEqual(s.STATE_CACHE['agentTasks'][1]['status'], 'pending')
        self.assertEqual(s.STATE_CACHE['agentTasks'][1]['claimWaitSince'], s.now_text())

    def test_agent_claim_between_scan_and_mutation_cannot_be_failed(self):
        self.jobs(1)
        mutate = s.mutate_execution
        raced = False

        def claim_before_update(*args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                s.claim_agent_task('cluster', 'owner', 'request')
            return mutate(*args, **kwargs)

        with patch.object(s, 'mutate_execution', side_effect=claim_before_update):
            s.recover_stuck_agent_deployments()
        self.assertEqual(s.STATE_CACHE['agentTasks'][0]['status'], 'running')
        self.assertTrue(s.STATE_CACHE['agentTasks'][0]['resourceHeld'])
        self.assertEqual(s.STATE_CACHE['executions'][0]['status'], 'deploying')
        self.assertFalse(any('超时未领取' in log['message'] for log in s.STATE_CACHE['executions'][0]['logs']))

    def test_new_blocker_between_scan_and_mutation_prevents_timeout(self):
        self.jobs(same_target=True)
        mutate = s.mutate_execution
        raced = False

        def block_before_update(*args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                s.claim_agent_task('cluster', 'owner')
            return mutate(*args, **kwargs)

        with patch.object(s, 'mutate_execution', side_effect=block_before_update):
            s.recover_stuck_agent_deployments()
        self.assertEqual(s.STATE_CACHE['agentTasks'][1]['status'], 'pending')
        self.assertIsNone(s.STATE_CACHE['agentTasks'][1]['claimWaitSince'])

    def test_cancel_between_scan_and_mutation_remains_cancelled(self):
        self.jobs(1)
        execution_id = s.STATE_CACHE['executions'][0]['id']
        mutate = s.mutate_execution
        raced = False

        def cancel_before_update(*args, **kwargs):
            nonlocal raced
            if not raced:
                raced = True
                s.cancel_execution(execution_id, 'admin')
            return mutate(*args, **kwargs)

        with patch.object(s, 'mutate_execution', side_effect=cancel_before_update):
            s.recover_stuck_agent_deployments()
        self.assertEqual(s.STATE_CACHE['agentTasks'][0]['status'], 'cancelled')
        self.assertEqual(s.STATE_CACHE['executions'][0]['status'], 'cancelled')
        self.assertNotIn('超时未领取', s.STATE_CACHE['executions'][0]['logs'][-1]['message'])

    def test_timeout_is_logged_once_and_persisted_outside_state_lock(self):
        self.jobs(1)
        persist = s.persist_queued_state

        def verify(snapshot, revision):
            self.assertFalse(s.STATE_LOCK._is_owned())
            persist(snapshot, revision)

        with patch.object(s, 'persist_queued_state', side_effect=verify) as save:
            s.recover_stuck_agent_deployments()
            save.assert_called_once()
        execution = s.STATE_CACHE['executions'][0]
        self.assertEqual(execution['status'], 'failed')
        self.assertEqual(execution['clusterResults']['cluster'], 'failed')
        self.assertEqual(sum('超时未领取' in log['message'] for log in execution['logs']), 1)
        self.assertIn('心跳过期', execution['logs'][-1]['message'])
        self.assertEqual(s.read_raw_state()['executions'][0]['status'], 'failed')
        with patch.object(s, 'mutate_execution', side_effect=AssertionError('repeated failure write')):
            s.recover_stuck_agent_deployments()

    def test_online_agent_is_not_reported_as_offline(self):
        self.jobs(1)
        s.STATE_CACHE['agentHeartbeats'] = [{'cluster': 'cluster', 'time': s.now_text(), 'instanceId': 'agent'}]
        s.recover_stuck_agent_deployments()
        message = s.STATE_CACHE['executions'][0]['logs'][-1]['message']
        self.assertIn('心跳正常但未领取', message)
        self.assertIn('/api/agent/tasks', message)
        self.assertNotIn('未在线', message)

    def test_one_timed_out_cluster_does_not_end_running_command_or_release_target(self):
        self.jobs(1)
        execution_id = s.STATE_CACHE['executions'][0]['id']
        running = {**s.STATE_CACHE['agentTasks'][0], 'id': 'running', 'clusterName': 'other',
                   'status': 'running', 'assignedAgent': 'owner', 'resourceHeld': True, 'logs': []}
        s.STATE_CACHE['agentTasks'].append(running)
        s.STATE_CACHE['executions'][0]['clusterResults']['other'] = 'running'
        s.recover_stuck_agent_deployments()
        current = s.find_by_id(s.STATE_CACHE['agentTasks'], 'running')
        self.assertEqual(current['status'], 'running')
        self.assertTrue(current['resourceHeld'])
        s.update_agent_result('running', 'success', 'done', 'owner')
        self.assertEqual(s.find_by_id(s.STATE_CACHE['executions'], execution_id)['status'], 'partial')
        self.assertFalse(s.find_by_id(s.STATE_CACHE['agentTasks'], 'running')['resourceHeld'])

    def test_timeout_cancels_remaining_pending_clusters_before_later_claim(self):
        self.jobs(1)
        s.STATE_CACHE['agentTasks'].append({**s.STATE_CACHE['agentTasks'][0], 'id': 'other-pending',
            'clusterName': 'other', 'createdAt': s.now_text(), 'logs': []})
        s.STATE_CACHE['executions'][0]['clusterResults']['other'] = 'pending'
        s.recover_stuck_agent_deployments()
        other = s.find_by_id(s.STATE_CACHE['agentTasks'], 'other-pending')
        self.assertEqual(other['status'], 'cancelled')
        self.assertIsNone(s.claim_agent_task('other', 'owner'))

    def test_stale_orphan_or_completed_parent_does_not_block_later_claims(self):
        self.jobs(2)
        s.STATE_CACHE['agentTasks'].insert(0, {**s.STATE_CACHE['agentTasks'][0], 'id': 'orphan', 'executionId': 'missing'})
        s.find_by_id(s.STATE_CACHE['executions'], s.STATE_CACHE['agentTasks'][1]['executionId'])['status'] = 'failed'
        self.assertEqual(s.claim_agent_task('cluster', 'owner')['id'], 'a-1')

    def test_idle_agent_poll_does_not_scan_execution_history(self):
        class UnreadableHistory(list):
            def __iter__(self):
                raise AssertionError('idle poll scanned execution history')

        s.STATE_CACHE['executions'] = UnreadableHistory([{'id': 'old', 'status': 'success'}])
        self.assertIsNone(s.claim_agent_task('cluster', 'owner'))

    def test_blocking_metadata_never_reads_execution_history_logs(self):
        self.jobs(same_target=True)
        s.claim_agent_task('cluster', 'owner')

        class UnreadableLogs(list):
            def __iter__(self):
                raise AssertionError('read historical logs')

            def __deepcopy__(self, memo):
                raise AssertionError('copied historical logs')

        for execution in s.STATE_CACHE['executions']:
            execution['logs'] = UnreadableLogs(execution['logs'])
        old = s.state_snapshot()
        with patch.object(s, 'read_state', side_effect=AssertionError('full state read')):
            s.recover_stuck_agent_deployments()
        for before, after in zip(old['executions'], s.STATE_CACHE['executions']):
            self.assertIs(before['logs'], after['logs'])


if __name__ == '__main__':
    unittest.main()
