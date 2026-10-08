"""Collect local checks and comparable HTTP probes without executing release tasks."""

import argparse
import getpass
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies

from agent_check import CheckError, check_runtime, load_config


TIMEOUT = 10
BODY_LIMIT = 65536
AGENT_UA = 'Python-urllib/' + '.'.join(map(str, sys.version_info[:2]))


def safe_url(value):
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        if ':' in host:
            host = '[' + host + ']'
        if url.port:
            host += ':' + str(url.port)
        return urlunsplit((url.scheme, host, url.path, '', ''))
    except ValueError:
        return '[invalid URL]'


def scrub(value, config):
    secrets = [config.get('agentToken')]
    secrets += [os.environ.get(key) for key in ('WINDOWS_AGENT_TOKEN', 'CF_ACCESS_CLIENT_ID', 'CF_ACCESS_CLIENT_SECRET')]
    for app in config.get('applications', {}).values():
        if isinstance(app, dict) and isinstance(app.get('Environment'), dict):
            secrets += [str(item) for item in app['Environment'].values()]
    text = str(value)
    for secret in sorted((item for item in secrets if isinstance(item, str) and item), key=len, reverse=True):
        text = text.replace(secret, '***')
    return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '?', text)


def scrub_fields(value, config):
    if isinstance(value, str):
        return scrub(value, config)
    if isinstance(value, dict):
        return {key: scrub_fields(item, config) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_fields(item, config) for item in value]
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Capture redirects without forwarding Agent credentials to a login site.
        return None


def describe_response(status, headers, body):
    headers = {key.lower(): value for key, value in headers.items()}
    kind = headers.get('content-type', '').split(';', 1)[0].lower().strip()
    result = {'status': status, 'contentType': kind, 'jsonOk': False,
              'cloudflare': bool(headers.get('cf-ray')), 'challenge': headers.get('cf-mitigated', '').lower() == 'challenge'}
    ray = headers.get('cf-ray', '')
    if re.fullmatch(r'[a-fA-F0-9]{8,64}-[A-Z]{3}', ray):
        result['cfRay'] = ray
    if headers.get('location'):
        result['redirect'] = safe_url(headers['location'])
        location = urlsplit(result['redirect'])
        result['accessLogin'] = bool((location.hostname or '').endswith('.cloudflareaccess.com') or location.path.startswith('/cdn-cgi/access/'))
    try:
        data = json.loads(body)
        result['jsonOk'] = isinstance(data, dict) and data.get('ok') is True
    except (ValueError, UnicodeError):
        pass
    # Raw bodies, cookies, arbitrary headers and request credentials are never reported.
    result['html'] = kind == 'text/html' or body.lstrip().lower().startswith((b'<!doctype html', b'<html'))
    return result


def network_error(error):
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, socket.gaierror):
        return 'DNS resolution failed'
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'TLS certificate verification failed'
    if isinstance(reason, ssl.SSLError):
        return 'TLS handshake failed'
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return 'Connection or response timed out'
    if isinstance(reason, ConnectionRefusedError):
        return 'TCP connection refused'
    return 'Network request failed (' + type(reason).__name__ + ')'


def python_probe(url, headers, body=None):
    request = Request(url, data=body, headers=headers)
    try:
        response = build_opener(NoRedirect()).open(request, timeout=TIMEOUT)
    except HTTPError as error:
        response = error
    except (OSError, URLError, ValueError) as error:
        return {'error': network_error(error)}
    try:
        with response:
            return describe_response(response.code, response.headers, response.read(BODY_LIMIT))
    except (OSError, ValueError) as error:
        return {'error': network_error(error)}


def curl_quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'


def curl_probe(executable, url, headers, body, user_agent=None):
    lines = ['url = ' + curl_quote(url), 'request = "POST"', 'data = ' + curl_quote(body.decode('ascii'))]
    for key, value in headers.items():
        lines.append('header = ' + curl_quote(key + ': ' + value))
    if user_agent:
        lines.append('user-agent = ' + curl_quote(user_agent))
    # Credentials go through stdin, never process arguments or temporary files.
    args = [executable, '--disable', '--config', '-', '--silent', '--show-error', '--max-time', str(TIMEOUT),
            '--max-filesize', str(BODY_LIMIT), '--dump-header', '-', '--output', '-', '--write-out', '\nDIAG_STATUS:%{http_code}']
    try:
        process = subprocess.run(args, input='\n'.join(lines).encode('utf-8'), capture_output=True, timeout=TIMEOUT + 5)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'error': 'curl could not run (' + type(error).__name__ + ')'}
    if process.returncode:
        hint = {5: 'Proxy DNS failed', 6: 'DNS resolution failed', 7: 'TCP connection failed',
                28: 'Connection or response timed out', 35: 'TLS handshake failed',
                60: 'TLS certificate verification failed', 63: 'Response exceeded diagnostic size limit'}.get(process.returncode, 'Request failed')
        return {'error': 'curl exit ' + str(process.returncode) + ': ' + hint}
    raw, separator, code = process.stdout.rpartition(b'\nDIAG_STATUS:')
    if not separator or not code.strip().isdigit():
        return {'error': 'curl returned an unrecognized response'}
    # Skip proxy CONNECT / interim response headers, then retain final response body.
    response_headers = {}
    while raw.startswith(b'HTTP/'):
        match = re.search(b'\r?\n\r?\n', raw)
        if not match:
            return {'error': 'curl returned incomplete response headers'}
        block, raw = raw[:match.start()], raw[match.end():]
        response_headers = {}
        for line in block.splitlines()[1:]:
            key, colon, value = line.partition(b':')
            if colon:
                response_headers[key.decode('ascii', 'replace')] = value.strip().decode('utf-8', 'replace')
    return describe_response(int(code.strip()), response_headers, raw[:BODY_LIMIT])


def verdicts(probes):
    messages = []
    heartbeat = probes.get('Python heartbeat', {})
    if heartbeat.get('status') == 200 and heartbeat.get('jsonOk'):
        messages.append('CONFIRMED: platform accepted the Agent heartbeat under this interactive account. Service account connectivity still needs separate verification.')
    elif heartbeat.get('status') == 401:
        messages.append('CHECK: heartbeat returned 401. Compare config agentToken with the registered server Token; also check Access authentication if enabled.')
    elif heartbeat.get('status') == 400:
        messages.append('CHECK: heartbeat returned 400. Verify cluster and instance registration in the platform.')
    elif heartbeat.get('status') == 404:
        messages.append('CHECK: heartbeat route was not found. Verify platform URL, deployed platform version and proxy routing.')
    elif heartbeat.get('status') == 200:
        messages.append('CONFIRMED: heartbeat returned HTTP 200 but not the expected JSON {"ok":true}' + ('; it returned HTML. Check browser-login protection and proxy routing.' if heartbeat.get('html') else '. Check platform version and proxy routing.'))
    elif heartbeat.get('status', 0) >= 500:
        messages.append('CHECK: heartbeat returned a server/gateway error. Inspect platform availability and reverse-proxy origin connectivity.')
    if heartbeat.get('status') == 403:
        if heartbeat.get('challenge'):
            messages.append('CONFIRMED: response is marked as a Cloudflare browser challenge. Locate the matched security rule using the CF-Ray ID.')
        elif heartbeat.get('cloudflare'):
            messages.append('CONFIRMED: 403 passed through Cloudflare. CF-Ray identifies the request in Security Events/Access logs; headers alone cannot prove whether Cloudflare or the origin rejected it.')
        else:
            messages.append('CHECK: 403 denies access to the heartbeat endpoint. Inspect reverse-proxy and access-policy logs; the project heartbeat handler does not return 403.')
    if heartbeat.get('accessLogin'):
        messages.append('CONFIRMED: heartbeat redirects to a Cloudflare Access login. Configure machine-to-machine service authentication for the Agent.')
    elif heartbeat.get('redirect'):
        messages.append('CHECK: heartbeat redirects. Verify the canonical platform URL and login/proxy rules. Diagnostic requests do not follow redirects.')
    if heartbeat.get('error'):
        messages.append('CHECK: Python heartbeat did not receive an HTTP response: ' + heartbeat['error'])
    root = probes.get('Python homepage', {})
    if root.get('status') == 200 and not (heartbeat.get('status') == 200 and heartbeat.get('jsonOk')):
        messages.append('CONFIRMED: homepage GET works while Agent POST heartbeat fails. Homepage access does not validate API access or Agent authentication.')
    curl = probes.get('curl heartbeat', {})
    matched = probes.get('curl with Python User-Agent', {})
    if curl.get('status') == 200 and curl.get('jsonOk') and not (heartbeat.get('status') == 200 and heartbeat.get('jsonOk')):
        messages.append('CONFIRMED: curl succeeds with the same heartbeat URL, headers and body while Python fails. Compare proxy settings, User-Agent and TLS/client filtering.')
        if matched.get('status') == 403:
            messages.append('LIKELY: a User-Agent rule rejects Python-urllib; curl also gets 403 when using that User-Agent. Confirm the matching rule in gateway logs.')
        elif matched.get('status') == 200 and matched.get('jsonOk'):
            messages.append('CHECK: curl still succeeds with the Python User-Agent. Investigate Python vs curl proxy/TLS differences; User-Agent alone does not explain the result.')
    return messages or ['CHECK: use the failed checks and HTTP response metadata below to locate the failing layer.']


class Report:
    def __init__(self):
        self.config = {}
        self.checks = []
        self.probes = {}

    def add(self, level, name, detail):
        entry = {'level': level, 'check': name, 'detail': scrub(detail, self.config)}
        self.checks.append(entry)
        print('[{level}] {check}: {detail}'.format(**entry), flush=True)

    def probe(self, name, method, url, result):
        result = scrub_fields(result, self.config)
        self.probes[name] = result
        accepted = result.get('status') == 200 and (method == 'GET' or result.get('jsonOk'))
        self.add('PASS' if accepted else 'FAIL', name, method + ' ' + safe_url(url) + ' => ' + json.dumps(result, ensure_ascii=True))

    def save(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        stem = 'environment-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        findings = verdicts(self.probes) if self.probes else ['CHECK: resolve local failures before platform connectivity can be tested.']
        data = scrub_fields({'utcTime': datetime.now(timezone.utc).isoformat(), 'checks': self.checks, 'findings': findings}, self.config)
        text = '\n'.join('[{level}] {check}: {detail}'.format(**entry) for entry in self.checks)
        text += '\n\nDiagnosis:\n' + '\n'.join(findings)
        text += '\n\nOnly heartbeat requests were sent; no deployment tasks were claimed. This is the current interactive account, not a service-account impersonation.\n'
        report_path = directory / (stem + '.txt')
        report_path.write_text(scrub(text, self.config), encoding='utf-8')
        (directory / (stem + '.json')).write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding='utf-8')
        print('\nDiagnosis:', flush=True)
        for finding in findings:
            print(scrub(finding, self.config), flush=True)
        print('\nReport: ' + str(report_path.resolve()), flush=True)


def check_services(report):
    if os.name != 'nt':
        report.add('WARN', 'Windows services', 'Service inspection requires Windows')
        return
    command = "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); Get-CimInstance Win32_Service -Filter \"Name='deploy-platform-windows-agent' OR Name='python-mt5-http'\" -ErrorAction Stop | Select-Object Name,State,StartName,ExitCode,PathName | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, timeout=15)
        if result.returncode:
            raise ValueError('Service query failed')
        services = json.loads(result.stdout.decode('utf-8-sig')) if result.stdout.strip() else []
        services = [services] if isinstance(services, dict) else services
        for name in ('deploy-platform-windows-agent', 'python-mt5-http'):
            service = next((item for item in services if item['Name'] == name), None)
            if service:
                report.add('PASS' if service['State'] == 'Running' else 'WARN', name, 'State={State}; account={StartName}; Windows ExitCode={ExitCode}'.format(**service))
                if name == 'deploy-platform-windows-agent':
                    match = re.match(r'^"([^"]+)"|^(.+?\.exe)(?:\s|$)', service.get('PathName', ''), re.I)
                    if match:
                        executable = Path(match.group(1) or match.group(2))
                        report.add('PASS' if executable.is_file() else 'FAIL', 'Registered Agent executable', str(executable) + (' exists' if executable.is_file() else ' missing; run Start-Agent.cmd to repair registration'))
                        if executable.parent.resolve() != Path(__file__).resolve().parent:
                            report.add('WARN', 'Registered Agent directory', 'Service points to a different folder than this diagnostic script; verify the service uses the intended config.json')
                    else:
                        report.add('WARN', 'Registered Agent executable', 'Cannot identify service executable; inspect Windows Services ImagePath')
            else:
                report.add('WARN', name, 'Not registered; Sidecar is optional for Agent startup' if name == 'python-mt5-http' else 'Not registered; Start-Agent.cmd can register it')
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        report.add('WARN', 'Windows services', 'Cannot query SCM. Run as Administrator for complete service visibility')


def collect(report, path):
    report.add('PASS' if os.name == 'nt' else 'WARN', 'Operating system', os.name + '; Python ' + sys.version.split()[0])
    report.add('INFO', 'Diagnostic account', getpass.getuser() + '; no service-account impersonation')
    try:
        config = load_config(path)
    except CheckError as error:
        report.add('FAIL', 'Configuration', str(error))
        return
    report.config = config
    report.add('PASS', 'Configuration', 'Valid JSON; application and Agent fields present; credentials hidden')
    for filename in ('windows_agent.py', 'Invoke-Mt5Release.ps1', 'inspect_application.py'):
        report.add('PASS' if Path(__file__).with_name(filename).is_file() else 'FAIL', 'Agent file', filename)
    for app in config['applications'].values():
        for key in ('Python', 'ServiceWrapper', 'Uv'):
            exists = Path(app[key]).is_file()
            report.add('PASS' if exists else ('WARN' if key == 'Uv' else 'FAIL'), key, app[key] + (' exists' if exists else ' missing'))
        report.add('PASS' if Path(app['InstallRoot']).is_dir() else 'WARN', 'Sidecar directory', app['InstallRoot'] + '; not required for Agent startup')
    try:
        result = check_runtime(config)
        report.add('PASS', 'State directory', config['stateDirectory'] + '; writable; running tasks=' + str(result['runningTasks']))
    except CheckError as error:
        report.add('FAIL', 'Runtime/state directory', str(error))
    check_services(report)
    xml_path = Path(__file__).with_name('deploy-platform-windows-agent.xml')
    if xml_path.is_file():
        try:
            service_xml = ET.parse(xml_path).getroot()
            executable = service_xml.findtext('executable', '')
            report.add('PASS' if executable and Path(executable).is_file() else 'FAIL', 'WinSW Python executable', executable or 'Missing executable in XML')
            match = re.search(r'--config\s+"([^"]+)"', service_xml.findtext('arguments', ''))
            if match and Path(match.group(1)).resolve() != path.resolve():
                report.add('WARN', 'WinSW configuration path', 'XML uses a different configuration file: ' + match.group(1))
        except (OSError, ET.ParseError, ValueError):
            report.add('FAIL', 'WinSW XML', 'Cannot read or parse Agent service XML; Start-Agent.cmd can regenerate it')
    else:
        report.add('WARN', 'WinSW XML', 'Not generated in this folder; Start-Agent.cmd can create it')
    proxies = getproxies()
    visible = {key: safe_url(value) for key, value in proxies.items() if key in ('http', 'https', 'all')}
    report.add('INFO', 'Python proxy settings', json.dumps(visible) + '; NO_PROXY configured=' + str(bool(proxies.get('no'))))
    report.add('INFO', 'curl proxy environment', ', '.join(key + '=' + str(bool(os.environ.get(key))) for key in ('https_proxy', 'HTTPS_PROXY', 'http_proxy', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY')))
    report.add('INFO', 'Cloudflare Access credentials', 'Current process client ID configured=' + str(bool(os.environ.get('CF_ACCESS_CLIENT_ID'))) + '; client secret configured=' + str(bool(os.environ.get('CF_ACCESS_CLIENT_SECRET'))))
    url = config['platformUrl'].rstrip('/')
    parsed = urlsplit(url)
    host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
        report.add('PASS', 'DNS', host + ' => ' + ', '.join(addresses[:8]))
    except OSError as error:
        report.add('WARN', 'Direct DNS', network_error(error) + '; HTTP probes still run because a proxy may resolve remotely')
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT):
            report.add('PASS', 'Direct TCP', host + ':' + str(port) + ' reachable')
    except OSError as error:
        report.add('WARN', 'Direct TCP', network_error(error) + '; raw direct connection, separate from HTTP proxy routing')
    if parsed.scheme == 'https':
        try:
            with socket.create_connection((host, port), timeout=TIMEOUT) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as connection:
                    certificate = connection.getpeercert()
                    report.add('PASS', 'Direct TLS', 'Certificate verified; expires=' + certificate.get('notAfter', 'unknown'))
        except OSError as error:
            report.add('WARN', 'Direct TLS', network_error(error) + '; HTTP probes use their own proxy route')
    headers = {'X-Agent-Token': config.get('agentToken') or os.environ.get('WINDOWS_AGENT_TOKEN'), 'Content-Type': 'application/json', 'Accept': 'application/json'}
    for key, env in (('CF-Access-Client-Id', 'CF_ACCESS_CLIENT_ID'), ('CF-Access-Client-Secret', 'CF_ACCESS_CLIENT_SECRET')):
        if os.environ.get(env):
            headers[key] = os.environ[env]
    body = json.dumps({'cluster': config['cluster'], 'instanceId': config.get('instanceId') or socket.gethostname()}).encode('ascii')
    endpoint = url + '/api/windows-agent/heartbeat'
    # Homepage gets no credentials. Both clients send the exact same Agent POST.
    report.probe('Python homepage', 'GET', url + '/', python_probe(url + '/', {'Accept': 'text/html'}))
    report.probe('Python heartbeat', 'POST', endpoint, python_probe(endpoint, headers, body))
    curl = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
    if curl:
        report.probe('curl heartbeat', 'POST', endpoint, curl_probe(curl, endpoint, headers, body))
        report.probe('curl with Python User-Agent', 'POST', endpoint, curl_probe(curl, endpoint, headers, body, AGENT_UA))
    else:
        report.add('WARN', 'curl comparison', 'curl.exe not found; Python probes and all other checks completed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = Report()
    try:
        collect(report, args.config)
    except Exception as error:
        report.add('FAIL', 'Diagnostic collection', 'Unexpected ' + type(error).__name__ + '; no raw exception or credentials displayed')
    try:
        report.save(args.output)
    except OSError as error:
        print('[FAIL] Report cannot be written (' + type(error).__name__ + '). Set --output to a writable directory.', flush=True)
        return 1
    return 1 if any(item['level'] == 'FAIL' for item in report.checks) else 0


if __name__ == '__main__':
    raise SystemExit(main())
