import threading
import unittest
from unittest.mock import patch

import agent


class AgentHeartbeatTests(unittest.TestCase):
    def test_heartbeat_continues_while_worker_is_blocked(self):
        worker_started = threading.Event()
        release_worker = threading.Event()
        stop = threading.Event()
        calls = []

        def blocked_kubectl(*args, **kwargs):
            worker_started.set()
            release_worker.wait(3)
            return 0, 'ok'

        class ControlledStop:
            def is_set(self):
                return stop.is_set()

            def wait(self, seconds):
                self.interval = seconds
                return stop.is_set()

        timer = ControlledStop()

        def post(*args):
            calls.append(args)
            self.assertTrue(worker_started.is_set())
            self.assertFalse(release_worker.is_set())
            if len(calls) == 2:
                stop.set()

        task = {'payload': {'manifest': '', 'namespace': 'default', 'deployment': 'demo'}}
        with patch.object(agent, 'run_kubectl', side_effect=blocked_kubectl), patch.object(agent, 'api_post', side_effect=post):
            worker = threading.Thread(target=agent.execute_task, args=(task,))
            worker.start()
            try:
                self.assertTrue(worker_started.wait(2))
                agent.heartbeat_loop(timer)
                self.assertEqual(len(calls), 2)
                self.assertEqual(timer.interval, 30)
                self.assertEqual(calls[0][0], '/api/agent/heartbeat')
            finally:
                release_worker.set()
                worker.join(3)

    def test_heartbeat_failure_is_logged_and_does_not_stop_loop(self):
        with patch.object(agent, 'api_post', side_effect=RuntimeError('HTTP 502')), patch('builtins.print') as log:
            agent.heartbeat()
        self.assertIn('HTTP 502', log.call_args.args[0])

    def test_stopped_loop_does_not_send(self):
        stop = threading.Event()
        stop.set()
        with patch.object(agent, 'api_post') as post:
            agent.heartbeat_loop(stop)
        post.assert_not_called()
