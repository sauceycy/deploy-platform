import io
import json
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import agent
import server as s


class AgentNetworkTests(unittest.TestCase):
    def test_read_timeout_names_request_without_token_or_query(self):
        with patch.object(agent, 'urlopen', side_effect=TimeoutError('read timed out')):
            with self.assertRaises(RuntimeError) as failure:
                agent.api_get('/api/agent/tasks', {'requestId': 'private-request'})
        self.assertIn('GET /api/agent/tasks', str(failure.exception))
        self.assertIn('timeout=', str(failure.exception))
        self.assertNotIn('private-request', str(failure.exception))
        self.assertNotIn(agent.AGENT_TOKEN, str(failure.exception))

    def test_proxy_errors_identify_origin_problem_without_response_dump(self):
        for code in (502, 503, 504, 522, 524):
            with self.subTest(code=code):
                error = HTTPError('https://platform', code, 'gateway', {}, io.BytesIO(b'private proxy details'))
                with patch.object(agent, 'urlopen', side_effect=error):
                    with self.assertRaisesRegex(RuntimeError, f'HTTP {code}') as failure:
                        agent.api_get('/api/agent/tasks')
                self.assertIn('源站', str(failure.exception))
                self.assertNotIn('private proxy details', str(failure.exception))
                self.assertTrue(error.closed)

    def test_configured_timeout_is_used_by_all_agent_requests(self):
        with patch.object(agent, 'AGENT_HTTP_TIMEOUT_SECONDS', 7), \
                patch.object(agent, 'urlopen', return_value=io.BytesIO(b'{"ok":true}')) as request:
            agent.api_post('/api/agent/heartbeat', {'cluster': 'cluster'})
        self.assertEqual(request.call_args.kwargs['timeout'], 7)

    def test_heartbeat_advertises_real_worker_capacity_and_version(self):
        with patch.object(agent, 'AGENT_MAX_CONCURRENT_TASKS', 5), patch.object(agent, 'api_post') as post:
            agent.heartbeat()
        body = post.call_args.args[1]
        self.assertEqual(body['maxConcurrentTasks'], 5)
        self.assertEqual(body['version'], '0.2.0')

    def test_check_does_not_claim_work_start_ledger_or_send_heartbeat(self):
        with patch.object(sys, 'argv', ['agent.py', '--check']), \
                patch.object(agent, 'AgentStore', side_effect=AssertionError('opened ledger')), \
                patch.object(agent, 'api_post', side_effect=AssertionError('sent heartbeat')), \
                patch.object(agent, 'api_get', return_value={'status': 'ok'}) as get, \
                patch('sys.stdout', io.StringIO()) as output:
            self.assertEqual(agent.main(), 0)
        get.assert_called_once_with('/api/health')
        self.assertIn('不验证集群 Token', output.getvalue())

    def test_check_fails_on_network_error_or_wrong_health_response(self):
        for response, error in ((None, TimeoutError('timeout')), ({'login': True}, None)):
            with self.subTest(response=response), patch.object(agent, 'api_get', return_value=response, side_effect=error), \
                    patch('sys.stdout', io.StringIO()) as output:
                self.assertEqual(agent.check_platform(), 1)
                self.assertIn('CHECK FAILED', output.getvalue())

    def test_platform_retains_heartbeat_capacity_without_copying_history(self):
        state = s.default_state_copy()

        class UncopyableExecution(dict):
            def __deepcopy__(self, memo):
                raise AssertionError('heartbeat copied history')

        state['executions'] = [UncopyableExecution(id='old', logs=[{'message': 'historical'}])]
        handler = s.Handler.__new__(s.Handler)
        handler.path = '/api/agent/heartbeat'
        handler.require_agent_token = Mock(return_value=True)
        handler.read_json_body = Mock(return_value={'cluster': 'cluster', 'instanceId': 'owner',
                                                   'version': '0.2.0', 'maxConcurrentTasks': 5})
        handler.send_json = Mock()
        with patch.object(s, 'STATE_CACHE', state), patch.object(s, 'WS_CLIENTS', set()), \
                patch.object(s, 'enqueue_state_write'):
            handler.do_POST()
            current = s.state_snapshot()
        self.assertEqual(current['agentHeartbeats'][0]['maxConcurrentTasks'], 5)
        self.assertIs(current['executions'][0], state['executions'][0])
        self.assertEqual(handler.send_json.call_args.args[0], {'ok': True})


if __name__ == '__main__':
    unittest.main()
