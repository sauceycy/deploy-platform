from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

import agent
from agent_store import AgentStore


class AgentQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'agent/state.db'
        self.store = AgentStore(self.path)
        self.addCleanup(lambda: self.store.close())

    def test_result_is_retried_after_restart_without_executing_again(self):
        worker = agent.AgentWorker(self.store)
        with patch.object(agent, 'api_get', return_value={'task': {'id': 'a', 'status': 'running'}}), \
                patch.object(agent, 'execute_task', return_value=('success', 'complete')) as execute, \
                patch.object(agent, 'api_post', side_effect=OSError('network unavailable')):
            with self.assertRaises(OSError):
                worker.tick()
            execute.assert_called_once()
        self.store.close()
        self.store = AgentStore(self.path)
        recovered = queue.Queue()
        for record in self.store.recover():
            recovered.put(record)
        with patch.object(agent, 'api_get', side_effect=AssertionError('reclaimed completed command')), \
                patch.object(agent, 'execute_task', side_effect=AssertionError('replayed completed command')), \
                patch.object(agent, 'api_post', return_value={'ok': True}) as post:
            self.assertTrue(agent.AgentWorker(self.store, recovered).tick())
            self.assertEqual(post.call_args.args[1]['status'], 'success')
        self.assertEqual(self.store.recover(), [])

    def test_interrupted_command_reports_uncertainty_without_replay(self):
        self.store.begin('r')
        self.store.received('r', {'id': 'a'})
        recovered = queue.Queue()
        for record in self.store.recover():
            recovered.put(record)
        with patch.object(agent, 'execute_task', side_effect=AssertionError('replayed')), patch.object(agent, 'api_post') as post:
            agent.AgentWorker(self.store, recovered).tick()
        payload = post.call_args.args[1]
        self.assertEqual(payload['status'], 'failed')
        self.assertIn('uncertain', payload['logs'])

    def test_lost_claim_response_reuses_persisted_request_id_after_restart(self):
        worker = agent.AgentWorker(self.store)
        with patch.object(agent, 'api_get', side_effect=OSError('lost response')) as get:
            with self.assertRaises(OSError):
                worker.tick()
            request_id = get.call_args.args[1]['requestId']
        self.store.close()
        self.store = AgentStore(self.path)
        recovered = queue.Queue()
        for record in self.store.recover():
            recovered.put(record)
        with patch.object(agent, 'api_get', return_value={'task': {'id': 'a'}}) as get, \
                patch.object(agent, 'execute_task', return_value=('success', 'ok')), patch.object(agent, 'api_post'):
            agent.AgentWorker(self.store, recovered).tick()
        self.assertEqual(get.call_args.args[1]['requestId'], request_id)

    def test_local_disk_failure_before_execution_prevents_kubectl(self):
        worker = agent.AgentWorker(self.store)
        with patch.object(agent, 'api_get', return_value={'task': {'id': 'a'}}), \
                patch.object(self.store, 'received', side_effect=OSError('disk full')), \
                patch.object(agent, 'execute_task') as execute:
            with self.assertRaises(OSError):
                worker.tick()
            execute.assert_not_called()

    def test_local_result_write_failure_does_not_execute_twice(self):
        worker = agent.AgentWorker(self.store)
        with patch.object(agent, 'api_get', return_value={'task': {'id': 'a'}}), \
                patch.object(agent, 'execute_task', return_value=('success', 'ok')) as execute, \
                patch.object(self.store, 'result', side_effect=OSError('disk full')), patch.object(agent, 'api_post') as post:
            with self.assertRaises(OSError):
                worker.tick()
            post.assert_not_called()
        with patch.object(agent, 'execute_task', side_effect=AssertionError('replayed')), patch.object(agent, 'api_post'):
            worker.tick()
        execute.assert_called_once()

    def test_five_agent_deployments_overlap_and_persist_results(self):
        barrier = threading.Barrier(5)
        active = set()
        lock = threading.Lock()
        errors = []
        def get(_path, query):
            key = query['requestId']
            return {'task': {'id': key, 'status': 'running'}}
        def execute(task):
            with lock:
                active.add(task['id'])
            barrier.wait(3)
            return 'success', 'ok'
        def run():
            try:
                agent.AgentWorker(self.store).tick()
            except Exception as exc:
                errors.append(exc)
        with patch.object(agent, 'api_get', side_effect=get), patch.object(agent, 'execute_task', side_effect=execute), \
                patch.object(agent, 'api_post') as post:
            threads = [threading.Thread(target=run) for _ in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(len(active), 5)
            self.assertEqual(post.call_count, 5)
        self.assertEqual(self.store.recover(), [])

    def test_agent_identity_survives_pod_hostname_change(self):
        self.assertEqual(self.store.identity('pod-1'), 'pod-1')
        self.store.close()
        self.store = AgentStore(self.path)
        self.assertEqual(self.store.identity('pod-2'), 'pod-1')

    def test_second_agent_cannot_recover_a_live_agents_ledger(self):
        with self.assertRaisesRegex(RuntimeError, 'already owns'):
            AgentStore(self.path)
