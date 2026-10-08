import importlib.util
import io
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request


AGENT_DIR = Path(__file__).resolve().parents[1] / 'windows-agent'
spec = importlib.util.spec_from_file_location('diagnose_environment', AGENT_DIR / 'diagnose_environment.py')
diagnostic = importlib.util.module_from_spec(spec)
with patch.object(sys, 'path', [str(AGENT_DIR), *sys.path]):
    spec.loader.exec_module(diagnostic)


class EnvironmentDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {'agentToken': 'private-agent-value', 'platformUrl': 'https://platform.example',
                       'cluster': 'win-test', 'stateDirectory': str(self.root),
                       'applications': {'mt5': {'Python': str(self.root / 'python.exe'),
                                               'ServiceWrapper': str(self.root / 'winsw.exe'),
                                               'Uv': str(self.root / 'uv.exe'), 'InstallRoot': str(self.root / 'sidecar')}}}
        self.body = b'{"cluster":"win-test","instanceId":"win-01"}'
        self.headers = {'X-Agent-Token': self.config['agentToken'], 'Content-Type': 'application/json'}

    def test_http_metadata_keeps_ray_and_challenge_but_hides_body_cookies_and_headers(self):
        result = diagnostic.describe_response(403, {'CF-Ray': '1234567890abcdef-HKG', 'cf-mitigated': 'challenge',
                                                    'Set-Cookie': 'private-cookie-value', 'Content-Type': 'text/html'},
                                              b'<html>private-agent-value</html>')
        self.assertEqual(result['cfRay'], '1234567890abcdef-HKG')
        self.assertTrue(result['challenge'])
        self.assertTrue(result['html'])
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('Set-Cookie', result)

    def test_redirect_url_drops_credentials_query_and_fragment(self):
        result = diagnostic.describe_response(302, {'Location': 'https://user:private-password@team.cloudflareaccess.com/login?token=private-agent-value#secret'}, b'')
        self.assertEqual(result['redirect'], 'https://team.cloudflareaccess.com/login')
        self.assertTrue(result['accessLogin'])
        request = Request('https://platform.example', headers=self.headers)
        self.assertIsNone(diagnostic.NoRedirect().redirect_request(request, None, 302, 'Found', {}, result['redirect']))

    def test_python_probe_preserves_http_failure_and_closes_response(self):
        body = io.BytesIO(b'<html>private-response</html>')
        error = HTTPError('https://platform.example', 403, 'Forbidden', {'CF-Ray': '1234567890abcdef-HKG'}, body)
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(diagnostic, 'build_opener', return_value=opener):
            result = diagnostic.python_probe('https://platform.example/api/windows-agent/heartbeat', self.headers, self.body)
        self.assertEqual(result['status'], 403)
        self.assertTrue(body.closed)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.data, self.body)
        self.assertNotIn('private-response', json.dumps(result))

    def test_network_failures_are_classified_without_echoing_sensitive_errors(self):
        for error, hint in ((URLError(socket.gaierror('private-detail')), 'DNS'),
                            (URLError(ssl.SSLCertVerificationError('private-detail')), 'certificate'),
                            (TimeoutError('private-detail'), 'timed out'),
                            (ConnectionRefusedError('private-detail'), 'refused')):
            with self.subTest(error=type(error).__name__):
                message = diagnostic.network_error(error)
                self.assertIn(hint, message)
                self.assertNotIn('private-detail', message)

    def test_curl_uses_stdin_for_credentials_and_handles_proxy_interim_headers(self):
        output = b'HTTP/1.1 200 Connection established\r\n\r\nHTTP/1.1 100 Continue\r\n\r\nHTTP/2 200\r\nContent-Type: application/json\r\n\r\n{"ok":true}\nDIAG_STATUS:200'
        process = subprocess.CompletedProcess([], 0, output, b'')
        with patch.object(diagnostic.subprocess, 'run', return_value=process) as run:
            result = diagnostic.curl_probe('curl.exe', 'https://platform.example/api/windows-agent/heartbeat', self.headers, self.body, diagnostic.AGENT_UA)
        self.assertTrue(result['jsonOk'])
        args = run.call_args.args[0]
        self.assertIn('--disable', args)
        self.assertNotIn('--insecure', args)
        self.assertNotIn('--location', args)
        self.assertNotIn(self.config['agentToken'], ' '.join(args))
        stdin = run.call_args.kwargs['input'].decode()
        self.assertIn('X-Agent-Token: private-agent-value', stdin)
        self.assertIn('user-agent = "' + diagnostic.AGENT_UA + '"', stdin)
        self.assertEqual(result['contentType'], 'application/json')

    def test_curl_failure_reports_code_not_raw_stderr_or_body(self):
        process = subprocess.CompletedProcess([], 60, b'private-agent-value', b'private-agent-value')
        with patch.object(diagnostic.subprocess, 'run', return_value=process):
            result = diagnostic.curl_probe('curl.exe', 'https://platform.example', self.headers, self.body)
        self.assertIn('certificate', result['error'])
        self.assertNotIn('private-agent-value', json.dumps(result))

    def test_user_agent_difference_is_a_hypothesis_and_cloudflare_origin_is_not_assumed(self):
        findings = diagnostic.verdicts({'Python homepage': {'status': 200},
                                       'Python heartbeat': {'status': 403, 'cloudflare': True},
                                       'curl heartbeat': {'status': 200, 'jsonOk': True},
                                       'curl with Python User-Agent': {'status': 403}})
        self.assertTrue(any('LIKELY:' in item and 'User-Agent' in item for item in findings))
        self.assertTrue(any('cannot prove' in item for item in findings))
        self.assertTrue(any('homepage GET works' in item for item in findings))

    def test_matching_user_agent_success_points_to_client_or_proxy_differences(self):
        findings = diagnostic.verdicts({'Python heartbeat': {'status': 403},
                                       'curl heartbeat': {'status': 200, 'jsonOk': True},
                                       'curl with Python User-Agent': {'status': 200, 'jsonOk': True}})
        self.assertTrue(any('User-Agent alone does not explain' in item for item in findings))

    def test_200_html_is_not_accepted_as_a_successful_heartbeat(self):
        result = diagnostic.describe_response(200, {'Content-Type': 'text/html'}, b'<html>Login</html>')
        self.assertFalse(result['jsonOk'])
        findings = diagnostic.verdicts({'Python heartbeat': result})
        self.assertFalse(any('accepted' in item for item in findings))
        self.assertTrue(any('HTML' in item for item in findings))

    def test_reports_remain_valid_json_and_redact_escaped_credentials(self):
        report = diagnostic.Report()
        report.config = {**self.config, 'agentToken': 'private-"quoted"-\\value'}
        with patch('sys.stdout', io.StringIO()) as output:
            report.probe('Python heartbeat', 'POST', 'https://platform.example',
                         {'status': 403, 'contentType': report.config['agentToken']})
            report.save(self.root)
        data = json.loads(next(self.root.glob('*.json')).read_text())
        text = next(self.root.glob('*.txt')).read_text()
        self.assertNotIn('private-', json.dumps(data))
        self.assertNotIn('private-', text)
        self.assertNotIn('private-', output.getvalue())
        self.assertIn('***', data['checks'][0]['detail'])

    def test_missing_config_still_generates_report_without_network_requests(self):
        with patch.object(diagnostic.sys, 'argv', ['diagnose_environment.py', '--config', str(self.root / 'missing.json'), '--output', str(self.root / 'reports')]), patch('sys.stdout', io.StringIO()), patch.object(diagnostic, 'python_probe') as probe:
            self.assertEqual(diagnostic.main(), 1)
        probe.assert_not_called()
        report = json.loads(next((self.root / 'reports').glob('*.json')).read_text())
        self.assertTrue(any(item['check'] == 'Configuration' and item['level'] == 'FAIL' for item in report['checks']))

    def test_collection_compares_same_heartbeat_without_claiming_tasks(self):
        report = diagnostic.Report()
        success = {'status': 200, 'jsonOk': True}
        with patch.object(diagnostic, 'load_config', return_value=self.config), patch.object(diagnostic, 'check_runtime', return_value={'runningTasks': 0}), patch.object(diagnostic, 'check_services'), patch.object(diagnostic, 'getproxies', return_value={'https': 'http://user:private-proxy@proxy.example:8080'}), patch.object(diagnostic.socket, 'getaddrinfo', side_effect=socket.gaierror), patch.object(diagnostic.socket, 'create_connection', side_effect=TimeoutError), patch.object(diagnostic, 'python_probe', return_value=success) as python, patch.object(diagnostic, 'curl_probe', return_value=success) as curl, patch.object(diagnostic.shutil, 'which', return_value='curl.exe'), patch('sys.stdout', io.StringIO()):
            diagnostic.collect(report, self.root / 'config.json')
        homepage, heartbeat = python.call_args_list
        self.assertNotIn('X-Agent-Token', homepage.args[1])
        self.assertTrue(heartbeat.args[0].endswith('/api/windows-agent/heartbeat'))
        for call in curl.call_args_list:
            self.assertEqual(call.args[1], heartbeat.args[0])
            self.assertEqual(call.args[2], heartbeat.args[1])
            self.assertEqual(call.args[3], heartbeat.args[2])
        self.assertNotIn('private-proxy', json.dumps(report.checks))
        self.assertTrue(any(item['check'] == 'Uv' and item['level'] == 'WARN' for item in report.checks))
        self.assertTrue(any(item['check'] == 'Sidecar directory' and item['level'] == 'WARN' for item in report.checks))

    @unittest.skipUnless(shutil.which('curl.exe' if os.name == 'nt' else 'curl'), 'curl not installed')
    def test_real_clients_send_same_post_and_do_not_follow_redirects(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                requests.append((self.path, self.headers.get('X-Agent-Token'), data))
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/must-not-be-called?private-query')
                else:
                    self.send_response(403)
                    self.send_header('Content-Type', 'text/html')
                    self.send_header('CF-Ray', '1234567890abcdef-HKG')
                self.end_headers()
                self.wfile.write(b'<html>private-response</html>')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        worker.start()
        try:
            url = 'http://127.0.0.1:' + str(server.server_port)
            curl = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
            with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}):
                python = diagnostic.python_probe(url + '/heartbeat', self.headers, self.body)
                native = diagnostic.curl_probe(curl, url + '/heartbeat', self.headers, self.body)
                redirect = diagnostic.python_probe(url + '/redirect', self.headers, self.body)
                curl_redirect = diagnostic.curl_probe(curl, url + '/redirect', self.headers, self.body)
            self.assertEqual(python['status'], 403)
            self.assertEqual(native['status'], 403)
            self.assertEqual(python['cfRay'], native['cfRay'])
            self.assertEqual(requests[0], requests[1])
            self.assertEqual(requests[0][1], self.config['agentToken'])
            self.assertEqual(requests[0][2], self.body)
            self.assertEqual(redirect['status'], 302)
            self.assertEqual(curl_redirect['status'], 302)
            self.assertEqual(len(requests), 4)
            self.assertFalse(any('private-query' in item['redirect'] for item in (redirect, curl_redirect)))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
