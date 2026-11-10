import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import server as s
from execution_pool import ExecutionPool, ExecutionQueueFull, stage_slot


class ExecutionPoolTests(unittest.TestCase):
    def test_five_jobs_really_overlap_and_queue_is_bounded(self):
        pool = ExecutionPool(5, 2)
        release = threading.Event()
        entered = threading.Barrier(6)
        active = set()
        lock = threading.Lock()

        def work(job):
            if job < 5:
                with lock:
                    active.add(job)
                entered.wait(3)
                release.wait(3)
            return job

        try:
            jobs = [pool.submit(work, i) for i in range(7)]
            entered.wait(3)
            self.assertEqual(active, set(range(5)))
            self.assertIs(pool.submit(work, 0), jobs[0])
            with self.assertRaises(ExecutionQueueFull):
                pool.submit(work, 7)
            self.assertTrue(pool.cancel(6))
            replacement = pool.submit(work, 7)
            release.set()
            self.assertEqual([future.result(3) for future in jobs[:6]], list(range(6)))
            self.assertEqual(replacement.result(3), 7)
        finally:
            release.set()
            pool.shutdown()

    def test_stage_slots_are_released_on_failure_and_wait_is_cancellable(self):
        semaphore = threading.BoundedSemaphore(1)
        with self.assertRaises(ValueError):
            with stage_slot(semaphore, lambda: None):
                raise ValueError('command failed')
        self.assertTrue(semaphore.acquire(blocking=False))
        def cancelled():
            raise RuntimeError('cancelled')
        with self.assertRaises(RuntimeError):
            with stage_slot(semaphore, cancelled):
                self.fail('entered cancelled stage')
        semaphore.release()


class ParallelStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = s.default_state_copy()
        self.state['clusters'] = [{'id': 'c', 'name': 'cluster', 'organizationId': 'default'}]
        self.state['tasks'] = [dict(id=str(i), name=f'app-{i}', repo='https://example.invalid/repo.git',
                                    language='java', sdk='jdk17', status='pending', organizationId='default') for i in range(10)]
        self.pool = Mock()
        self.pool.available.return_value = 105
        for item in [patch.object(s, 'STATE_CACHE', self.state), patch.object(s, 'WS_CLIENTS', set()),
                     patch.object(s, 'BUILD_EXECUTOR', self.pool), patch.object(s, 'DB_PATH', Path(self.temp.name) / 'state.db'),
                     patch.object(s, 'use_postgres', return_value=False), patch.object(s, 'PENDING_STATE_PAYLOAD', None),
                     patch.object(s, 'LAST_PERSISTED_STATE', None), patch.object(s, 'LAST_PERSISTED_TARGET', None),
                     patch.object(s, 'LAST_PERSISTED_REVISION', -1), patch.object(s, 'STATE_WRITE_EVENT', threading.Event()),
                     patch.object(s, 'send_notification'), patch.object(s, 'append_publish_commit_log')]:
            item.start()
            self.addCleanup(item.stop)

    def agent_jobs(self, count=5, same_target=False):
        state = s.STATE_CACHE
        for i in range(count):
            execution = s.create_execution_record(state, state['tasks'][i], 'admin', 'main')
            execution.update(status='deploying', stage='Agent 部署')
            state['agentTasks'].append({'id': f'a-{i}', 'executionId': execution['id'], 'taskId': str(i),
                'clusterName': 'cluster', 'status': 'pending', 'logs': [], 'createdAt': '2020-01-01 00:00:00',
                'payload': {'namespace': 'default', 'deployment': 'shared' if same_target else f'app-{i}'}})

    def test_batch_can_exceed_concurrency_and_is_durable_before_dispatch(self):
        def verify(_function, execution_id):
            saved = s.read_raw_state()
            self.assertIsNotNone(s.find_by_id(saved['executions'], execution_id))
        self.pool.submit.side_effect = verify
        executions, _ = s.create_batch_executions([{'taskId': str(i), 'branch': 'main'} for i in range(7)], 'admin')
        self.assertEqual(len(executions), 7)
        self.assertEqual(self.pool.submit.call_count, 7)
        self.assertEqual(len(s.read_raw_state()['executions']), 7)

    def test_database_failure_does_not_start_uncommitted_work(self):
        with patch.object(s, 'persist_state_snapshot', side_effect=OSError('database offline')):
            with self.assertRaises(OSError):
                s.create_execution('0', 'admin', 'main', request_id='retry-after-network')
        self.pool.submit.assert_not_called()
        # Once storage recovers, the same request returns one durable execution.
        execution, _ = s.create_execution('0', 'admin', 'main', request_id='retry-after-network')
        self.assertEqual(len(s.read_raw_state()['executions']), 1)
        self.assertEqual(execution['requestId'], 'retry-after-network')

    def test_batch_request_ids_prevent_duplicates_after_lost_ack(self):
        items = [{'taskId': str(i), 'branch': 'main', 'requestId': f'once-{i}'} for i in range(5)]
        first, _ = s.create_batch_executions(items, 'admin')
        for execution in first:
            s.set_execution_status(execution['id'], 'success')
        self.pool.available.return_value = 0
        second, _ = s.create_batch_executions(items, 'admin')
        self.assertEqual([item['id'] for item in first], [item['id'] for item in second])
        self.assertEqual(len(s.STATE_CACHE['executions']), 5)

    def test_full_queue_rejects_atomically_and_due_schedules_wait(self):
        self.pool.available.return_value = 0
        with self.assertRaises(ExecutionQueueFull):
            s.create_batch_executions([{'taskId': '0', 'branch': 'main'}], 'admin')
        self.assertEqual(s.STATE_CACHE['executions'], [])
        s.STATE_CACHE['schedules'] = [{'id': 'schedule', 'taskId': '0', 'branch': 'main',
                                     'status': 'pending', 'scheduledAt': '2020-01-01T00:00:00'}]
        s.trigger_due_schedules()
        self.assertEqual(s.STATE_CACHE['schedules'][0]['status'], 'pending')
        self.assertEqual(s.STATE_CACHE['executions'], [])

    def test_queue_survives_restart_and_running_agent_work_is_preserved(self):
        self.agent_jobs(1)
        running = s.create_execution_record(s.STATE_CACHE, s.STATE_CACHE['tasks'][1], 'admin', 'main')
        running.update(status='building')
        queued = s.create_execution_record(s.STATE_CACHE, s.STATE_CACHE['tasks'][2], 'admin', 'main')
        s.persist_state_snapshot(s.STATE_CACHE)
        s.STATE_CACHE = None
        s.recover_interrupted_executions()
        snapshot = s.state_snapshot()
        self.assertEqual(s.find_by_id(snapshot['executions'], queued['id'])['status'], 'queued')
        self.assertEqual(s.find_by_id(snapshot['executions'], running['id'])['status'], 'failed')
        self.assertEqual(snapshot['agentTasks'][0]['status'], 'pending')
        s.dispatch_queued_executions()
        self.pool.submit.assert_called_once_with(s.run_build_safely, queued['id'])

    def test_single_request_id_is_idempotent_even_after_completion(self):
        first, _ = s.create_execution('0', 'admin', 'main', request_id='once')
        s.set_execution_status(first['id'], 'success')
        second, _ = s.create_execution('0', 'admin', 'main', request_id='once')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(s.STATE_CACHE['executions']), 1)
        with self.assertRaisesRegex(ValueError, 'requestId'):
            s.create_execution('0', 'admin', 'other', request_id='once')

    def test_five_agent_claims_are_atomic_unique_and_have_a_cluster_limit(self):
        self.agent_jobs(6)
        claims = []
        gate = threading.Barrier(9)
        def claim(i):
            gate.wait(3)
            claims.append(s.claim_agent_task('cluster', f'worker-{i}', f'request-{i}'))
        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        gate.wait(3)
        for thread in threads:
            thread.join(3)
        ids = [item['id'] for item in claims if item]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(set(ids)), 5)
        held = next(item for item in claims if item)
        self.assertEqual(s.claim_agent_task('cluster', held['assignedAgent'], held['claimRequestId'])['id'], held['id'])

    def test_same_target_waits_through_cancel_until_agent_acknowledges(self):
        self.agent_jobs(2, same_target=True)
        first = s.claim_agent_task('cluster', 'owner', 'request')
        self.assertIsNone(s.claim_agent_task('cluster', 'other', 'next'))
        s.cancel_execution(first['executionId'], 'admin')
        self.assertIsNone(s.claim_agent_task('cluster', 'other', 'next'))
        s.update_agent_result(first['id'], 'success', 'finished', 'wrong-owner')
        self.assertIsNone(s.claim_agent_task('cluster', 'other', 'next'))
        s.update_agent_result(first['id'], 'success', 'finished', 'owner')
        second = s.claim_agent_task('cluster', 'other', 'next')
        self.assertNotEqual(first['id'], second['id'])

    def test_target_wait_is_not_misclassified_as_agent_timeout(self):
        self.agent_jobs(2, same_target=True)
        first = s.claim_agent_task('cluster', 'owner')
        s.recover_stuck_agent_deployments()
        other = next(item for item in s.STATE_CACHE['agentTasks'] if item['id'] != first['id'])
        self.assertEqual(other['status'], 'pending')

    def test_manual_release_requires_permission_cancellation_and_confirmation(self):
        self.agent_jobs(2, same_target=True)
        first = s.claim_agent_task('cluster', 'owner')
        with self.assertRaises(ValueError):
            s.release_agent_reservation(first['id'], 'admin')
        with self.assertRaises(ValueError):
            s.release_agent_reservation(first['id'], 'admin', True)
        s.cancel_execution(first['executionId'], 'admin')
        s.release_agent_reservation(first['id'], 'admin', True)
        self.assertIsNotNone(s.claim_agent_task('cluster', 'new-owner'))

    def test_writable_build_caches_are_isolated_between_tasks(self):
        with patch.object(s, 'DATA_DIR', Path(self.temp.name)), patch.object(s, 'HOST_DATA_DIR', Path(self.temp.name)):
            one, _, _ = s.build_cache_config(self.state['tasks'][0])
            two, _, _ = s.build_cache_config(self.state['tasks'][1])
            self.assertNotEqual(one, two)
            self.assertEqual(one, s.build_cache_config(copy.deepcopy(self.state['tasks'][0]))[0])


if __name__ == '__main__':
    unittest.main()
