"""Config-level concurrency with durable queue and task summaries kept intact."""
from contextlib import closing
from datetime import datetime, timedelta
import threading
import unittest
from unittest.mock import patch

import server as s
import state_store
import test_parallel_execution as parallel
import windows_deploy
from execution_pool import ExecutionPool


class ConfigReleaseTests(unittest.TestCase):
    def setUp(self):
        parallel.ParallelStateTests.setUp(self)
        self.state['tasks'][0]['deployConfigs'] = [
            {'id': f'cfg-{i}', 'name': f'环境 {i}', 'env': f'env-{i}',
             'deploymentName': f'app-{i}', 'organizationIds': ['default'],
             'clusters': [{'name': 'cluster', 'namespace': f'env-{i}'}]}
            for i in range(5)]

    def release(self, config=0, **kwargs):
        return s.create_execution('0', 'admin', 'main', f'cfg-{config}', **kwargs)[0]

    def test_five_configs_enqueue_concurrently_and_are_durable(self):
        entered = threading.Barrier(6)
        results, errors = [], []

        def release(index):
            try:
                entered.wait(3)
                results.append(self.release(index))
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=release, args=(i,)) for i in range(5)]
        for worker in workers:
            worker.start()
        entered.wait(3)
        for worker in workers:
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len({item['id'] for item in results}), 5)
        self.assertEqual({item['deployConfigId'] for item in s.read_raw_state()['executions']},
                         {f'cfg-{i}' for i in range(5)})
        self.assertEqual(len(s.STATE_CACHE['tasks'][0]['configExecutions']), 5)

    def test_same_config_remains_locked_when_newer_other_config_has_finished(self):
        first = self.release(0)
        second = self.release(1)
        s.set_execution_status(second['id'], 'success', stage='发布完成', progress=100)
        with self.assertRaisesRegex(ValueError, '环境 0.*正在执行'):
            self.release(0)
        task = s.STATE_CACHE['tasks'][0]
        self.assertEqual(task['status'], 'queued')
        self.assertEqual(task['lastDeployConfigId'], 'cfg-0')
        self.assertEqual(task['configExecutions']['cfg-1']['status'], 'success')
        s.cancel_execution(first['id'], 'admin')
        self.assertEqual(s.STATE_CACHE['tasks'][0]['status'], 'success')
        self.assertNotEqual(first['id'], self.release(0)['id'])

    def test_five_configs_really_execute_at_the_same_time(self):
        pool = ExecutionPool(5, 5)
        entered = threading.Barrier(6)
        release = threading.Event()
        completed = threading.Barrier(6)
        active = []
        def build(execution_id):
            execution = s.find_by_id(s.state_snapshot()['executions'], execution_id)
            self.assertIsNotNone(s.find_by_id(s.read_raw_state()['executions'], execution_id))
            s.set_execution_status(execution_id, 'building', stage='compile', progress=20)
            active.append(execution['deployConfigId'])
            entered.wait(5)
            release.wait(5)
            s.set_execution_status(execution_id, 'success', stage='done', progress=100)
            completed.wait(5)
        try:
            with patch.object(s, 'BUILD_EXECUTOR', pool), patch.object(s, 'run_build_safely', side_effect=build):
                s.create_batch_executions([{'taskId': '0', 'deployConfigId': f'cfg-{i}', 'branch': 'main'} for i in range(5)], 'admin')
                entered.wait(5)
                self.assertEqual(set(active), {f'cfg-{i}' for i in range(5)})
                self.assertTrue(all(item['status'] == 'building' for item in s.STATE_CACHE['executions']))
                release.set()
                completed.wait(5)
                self.assertEqual(s.STATE_CACHE['tasks'][0]['status'], 'success')
        finally:
            release.set()
            pool.shutdown()

    def test_simultaneous_same_config_requests_admit_only_one(self):
        entered = threading.Barrier(3)
        results, errors = [], []

        def run():
            entered.wait(3)
            try:
                results.append(self.release(0))
            except ValueError as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        entered.wait(3)
        for thread in threads:
            thread.join(3)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(s.read_raw_state()['executions']), 1)

    def test_batch_accepts_distinct_configs_but_duplicate_config_rolls_back(self):
        items = [{'taskId': '0', 'deployConfigId': f'cfg-{i}', 'branch': 'main', 'requestId': f'once-{i}'}
                 for i in range(5)]
        first, _ = s.create_batch_executions(items, 'admin')
        again, _ = s.create_batch_executions(items, 'admin')
        self.assertEqual([item['id'] for item in first], [item['id'] for item in again])
        for item in first:
            s.set_execution_status(item['id'], 'success')
        before = s.STATE_CACHE
        with self.assertRaisesRegex(ValueError, '正在执行'):
            s.create_batch_executions([{'taskId': '0', 'deployConfigId': 'cfg-0', 'branch': 'main'}] * 2, 'admin')
        self.assertIs(s.STATE_CACHE, before)
        self.assertEqual(len(s.read_raw_state()['executions']), 5)

    def test_request_id_cannot_be_repurposed_for_another_config(self):
        self.release(0, request_id='same-intent')
        with self.assertRaisesRegex(ValueError, 'requestId'):
            self.release(1, request_id='same-intent')

    def test_cancel_is_isolated_and_summary_has_no_logs_or_environment(self):
        first = self.release(0)
        second = self.release(1)
        s.set_execution_status(first['id'], 'building', stage='编译 A', progress=30)
        snapshot = s.STATE_CACHE
        s.cancel_execution(second['id'], 'admin')
        current = s.STATE_CACHE
        self.assertEqual(s.find_by_id(current['executions'], first['id'])['status'], 'building')
        self.assertEqual(current['tasks'][0]['stage'], '编译 A')
        self.assertEqual(snapshot['tasks'][0]['configExecutions']['cfg-1']['status'], 'queued')
        self.assertIs(s.find_by_id(current['executions'], first['id']), s.find_by_id(snapshot['executions'], first['id']))
        summary = current['tasks'][0]['configExecutions']['cfg-0']
        self.assertNotIn('logs', summary)
        self.assertNotIn('deployConfigSnapshot', summary)

    def test_notifications_do_not_depend_on_whole_task_latest_and_use_original_actor(self):
        self.state['users'].append({**self.state['users'][0], 'username': 'bob'})
        first = self.release(0)
        second, _ = s.create_execution('0', 'bob', 'other', 'cfg-1')
        for execution in (first, second):
            task = s.task_for_execution(s.STATE_CACHE['tasks'][0], execution)
            s.dispatch_agent_tasks(execution['id'], task, f'image:{execution["id"]}')
        job = next(item for item in s.STATE_CACHE['agentTasks'] if item['executionId'] == first['id'])
        s.update_agent_result(job['id'], 'success', 'completed A')
        notification = s.send_notification.call_args.args
        self.assertEqual(notification[0]['deployConfigId'], 'cfg-0')
        self.assertEqual(notification[0]['lastActor'], 'admin')
        self.assertEqual(notification[0]['lastBranch'], 'main')
        self.assertEqual(notification[3], first['id'])
        self.assertEqual(s.STATE_CACHE['tasks'][0]['status'], 'deploying')
        self.assertEqual(s.STATE_CACHE['tasks'][0]['lastActor'], 'bob')
        count = s.send_notification.call_count
        s.update_agent_result(job['id'], 'success', 'duplicate')
        self.assertEqual(s.send_notification.call_count, count)

    def test_failure_notification_is_also_independent_of_the_latest_config(self):
        first = self.release(0)
        other = self.release(1)
        s.dispatch_agent_tasks(first['id'], s.task_for_execution(s.STATE_CACHE['tasks'][0], first), 'image:first')
        job = s.STATE_CACHE['agentTasks'][0]
        s.update_agent_result(job['id'], 'failed', 'failed A')
        notification = s.send_notification.call_args.args
        self.assertEqual(notification[0]['deployConfigId'], 'cfg-0')
        self.assertEqual(notification[1], 'DEPLOY_FAILED')
        self.assertEqual(notification[3], first['id'])
        self.assertEqual(s.STATE_CACHE['tasks'][0]['status'], 'queued')
        self.assertEqual(s.find_by_id(s.STATE_CACHE['executions'], other['id'])['status'], 'queued')

    def test_recovery_and_build_caches_use_the_frozen_configuration(self):
        self.state['tasks'][0]['deployConfigs'][0]['repo'] = 'https://example.invalid/one.git'
        self.state['tasks'][0]['deployConfigs'][1]['repo'] = 'https://example.invalid/two.git'
        first = self.release(0)
        second = self.release(1)
        def waiting(state):
            item = s.find_by_id(state['executions'], first['id'])
            item.update(status='building', stage='等待部署', progress=84, image='image:one', updatedAt='2020-01-01 00:00:00')
        s.mutate_execution(first['id'], waiting)
        s.mutate_state(lambda state: state['tasks'][0]['deployConfigs'][0].update(repo='https://example.invalid/edited.git', clusters=[{'name': 'wrong'}]), read_only_history=True)
        with patch.object(s, 'dispatch_agent_tasks') as dispatch:
            s.recover_waiting_deployments()
        recovered = dispatch.call_args.args[1]
        self.assertEqual(recovered['repo'], 'https://example.invalid/one.git')
        self.assertEqual(recovered['clusters'][0]['namespace'], 'env-0')
        other = s.task_for_execution(s.STATE_CACHE['tasks'][0], second)
        self.assertEqual(other['repo'], 'https://example.invalid/two.git')
        self.assertNotEqual(s.build_cache_namespace(recovered), s.build_cache_namespace(other))
        self.assertEqual(s.build_cache_namespace(recovered), s.build_cache_namespace(s.task_for_execution(s.STATE_CACHE['tasks'][0], first)))

    def test_config_schedules_coexist_and_busy_config_waits_without_blocking_others(self):
        when = (datetime.now() + timedelta(days=1)).isoformat(timespec='seconds')
        old, _ = s.schedule_execution('0', 'admin', 'main', when, 'cfg-0')
        other, _ = s.schedule_execution('0', 'admin', 'main', when, 'cfg-1')
        newer, _ = s.schedule_execution('0', 'admin', 'main', when, 'cfg-0')
        plans = s.STATE_CACHE['schedules']
        self.assertEqual(s.find_by_id(plans, old['id'])['status'], 'cancelled')
        self.assertEqual(s.find_by_id(plans, other['id'])['status'], 'pending')
        self.release(0)
        s.mutate_state(lambda state: [item.update(scheduledAt='2020-01-01T00:00:00') for item in state['schedules']], read_only_history=True)
        s.trigger_due_schedules()
        self.assertEqual(s.find_by_id(s.STATE_CACHE['schedules'], newer['id'])['status'], 'pending')
        self.assertEqual(s.find_by_id(s.STATE_CACHE['schedules'], other['id'])['status'], 'triggered')
        s.cancel_execution(s.active_execution_for_task(s.STATE_CACHE, '0', 'cfg-0')['id'], 'admin')
        s.trigger_due_schedules()
        self.assertEqual(s.find_by_id(s.STATE_CACHE['schedules'], newer['id'])['status'], 'triggered')

    def test_same_kubernetes_target_remains_serial_across_configs(self):
        for config in self.state['tasks'][0]['deployConfigs']:
            config.update(deploymentName='shared', clusters=[{'name': 'cluster', 'namespace': 'same'}])
        for index in (0, 1):
            execution = self.release(index)
            s.dispatch_agent_tasks(execution['id'], s.task_for_execution(s.STATE_CACHE['tasks'][0], execution), 'image:test')
        first = s.claim_agent_task('cluster', 'worker-0')
        self.assertIsNotNone(first)
        self.assertIsNone(s.claim_agent_task('cluster', 'worker-1'))
        s.cancel_execution(first['executionId'], 'admin')
        self.assertIsNone(s.claim_agent_task('cluster', 'worker-1'))
        s.update_agent_result(first['id'], 'success', 'stopped', 'worker-0')
        self.assertIsNotNone(s.claim_agent_task('cluster', 'worker-1'))

    def test_windows_rollback_only_locks_its_own_configuration(self):
        task = self.state['tasks'][0]
        task['deployRule'] = 'windows'
        self.release(0)
        def dispatch(_server, state, execution, effective, action):
            self.assertEqual(effective['deployConfigId'], 'cfg-1')
            self.assertEqual(action, 'rollback')
            execution.update(status='deploying', stage='Agent 部署')
            s.refresh_task_execution_summary(state, task['id'])
        with patch.object(windows_deploy, 'dispatch', side_effect=dispatch):
            result, _ = windows_deploy.rollback(s, '0', 'admin', 'cfg-1')
        self.assertEqual(result['deployConfigId'], 'cfg-1')
        with self.assertRaisesRegex(ValueError, '等待该配置'):
            windows_deploy.rollback(s, '0', 'admin', 'cfg-0')

    def test_legacy_unknown_configuration_reserves_all_configs(self):
        self.state['executions'] = [{'id': 'legacy', 'taskId': '0', 'status': 'building', 'logs': []}]
        with self.assertRaisesRegex(ValueError, '正在执行'):
            self.release(3)

    def test_summary_survives_restart_and_history_window_without_copying_logs(self):
        execution = self.release(0)
        s.set_execution_status(execution['id'], 'deploying', stage='Agent 部署')
        def history(state):
            state['agentTasks'].append({'id': 'held', 'executionId': execution['id'], 'taskId': '0', 'status': 'pending'})
            state['executions'][:0] = [{'id': f'old-{i}', 'taskId': '1', 'status': 'success',
                                       'logs': [{'message': 'history' * 1000}]} for i in range(60)]
        s.mutate_state(history, read_only_history=True)
        s.flush_pending_state_write()
        s.STATE_CACHE = None
        s.recover_interrupted_executions()
        compact = s.client_state(s.STATE_CACHE, compact=True)
        self.assertNotIn(execution['id'], {item['id'] for item in compact['executions']})
        self.assertEqual(compact['tasks'][0]['configExecutions']['cfg-0']['status'], 'deploying')
        with self.assertRaisesRegex(ValueError, '正在执行'):
            self.release(0)
        self.release(1)

    def test_runtime_write_volume_does_not_include_unrelated_history(self):
        first = self.release(0)
        second = self.release(1)
        old = s.STATE_CACHE
        unrelated = s.find_by_id(old['executions'], second['id'])
        # A history object that cannot be deep-copied guards the mutation path.
        class HistoricalLog(dict):
            def __deepcopy__(self, memo):
                raise AssertionError('copied unrelated historical logs')
        unrelated['logs'] = [HistoricalLog(message='x' * 1024) for _ in range(1000)]
        s.persist_state_snapshot(old)
        s.set_execution_status(first['id'], 'building', stage='compile', progress=25)
        self.assertIs(s.find_by_id(s.STATE_CACHE['executions'], second['id']), unrelated)
        with closing(s.connect_sqlite()) as connection, connection:
            stats = state_store.save(connection, s.STATE_CACHE, old)
            self.assertLessEqual(stats['rows'], 3)  # revision, task summaries, changed execution
            self.assertLess(stats['bytes'], 25_000)
            restored = state_store.load(connection)
        self.assertEqual(len(s.find_by_id(restored['executions'], second['id'])['logs']), 1000)
        self.assertEqual(restored['tasks'][0]['configExecutions']['cfg-0']['progress'], 25)


if __name__ == '__main__':
    unittest.main()
