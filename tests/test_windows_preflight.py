import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1] / 'windows-agent'


class ReleasePreflightTests(unittest.TestCase):
    def run_new_preflight(self, root, capabilities='', resolver_failure=False, broken_loader=False):
        release = root / 'release'
        package = release / 'src' / 'python_mt5_sidecar'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('', encoding='utf-8')
        (package / 'config_loader.py').write_text(
            'import missing_release_dependency\n' if broken_loader else '''from types import SimpleNamespace as NS
def load_config(path):
    assert path.is_file()
    return NS(nacos=NS(enabled=True), mt5=NS(adapter='vendor', server_ref='env:MT5_MANAGER_SERVER', credential_ref='test'),
              http=NS(host='::', port=8082), callbacks_enabled=True''' + capabilities + ')\n', encoding='utf-8')
        mt5 = package / 'mt5'
        mt5.mkdir()
        (mt5 / '__init__.py').write_text('', encoding='utf-8')
        (mt5 / 'credentials.py').write_text('''class SystemMt5SecretResolver:
    def resolve(self, server_ref, credential_ref):
''' + ("        raise ValueError('private-credential-value')\n" if resolver_failure else
       "        return None\n"), encoding='utf-8')
        config = root / 'bootstrap.yaml'
        config.write_text('test fixture', encoding='utf-8')
        return subprocess.run([sys.executable, str(AGENT_DIR / 'inspect_application.py'),
                               '--release-root', str(release), '--config', str(config)],
                              capture_output=True, text=True, timeout=10)

    def test_new_layout_keeps_streaming_manager_and_journal_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_new_preflight(Path(directory), ", streaming_enabled=True, manager_gateway=NS(journal_path='C:/Persistent/commands.sqlite3')")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'port': 8082, 'healthUrl': 'http://[::1]:8082',
                                                         'streaming': True, 'manager': True,
                                                         'journalPath': 'C:/Persistent/commands.sqlite3'})

    def test_new_layout_can_use_callbacks_flag_without_optional_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_new_preflight(Path(directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            settings = json.loads(result.stdout)
            self.assertTrue(settings['streaming'])
            self.assertFalse(settings['manager'])
            self.assertIsNone(settings['journalPath'])

    def test_new_layout_credential_failure_stops_preflight_without_leaking_values(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_new_preflight(Path(directory), resolver_failure=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('ValueError', result.stderr)
            self.assertNotIn('private-credential-value', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_broken_new_loader_does_not_fall_back_to_old_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_new_preflight(Path(directory), broken_loader=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing_release_dependency', result.stderr)
            self.assertNotIn('query_config', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_preflight_uses_selected_release_despite_stale_package_near_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_dir = root / 'agent'
            agent_dir.mkdir()
            script = agent_dir / 'inspect_application.py'
            shutil.copyfile(AGENT_DIR / script.name, script)
            stale = agent_dir / 'python_mt5_sidecar'
            stale.mkdir()
            (stale / '__init__.py').write_text("raise RuntimeError('stale installation imported')", encoding='utf-8')
            release = root / 'release'
            package = release / 'src' / 'python_mt5_sidecar'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('', encoding='utf-8')
            (package / 'query_config.py').write_text('''from types import SimpleNamespace as NS
def load_query_config(path):
    assert path.is_file()
    return NS(nacos=NS(enabled=True), mt5=NS(adapter='vendor', server_ref='env:MT5_MANAGER_SERVER', credential_ref='test'),
              http=NS(host='0.0.0.0', port=8081), streaming_enabled=False, manager_gateway=None)
''', encoding='utf-8')
            (package / 'security.py').write_text('''class SystemMt5SecretResolver:
    def resolve(self, server_ref, credential_ref):
        return None
''', encoding='utf-8')
            config = root / 'bootstrap.yaml'
            config.write_text('test fixture', encoding='utf-8')
            result = subprocess.run([sys.executable, str(script), '--release-root', str(release), '--config', str(config)],
                                    cwd=root, env={**os.environ, 'PYTHONPATH': str(agent_dir)},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'port': 8081, 'healthUrl': 'http://127.0.0.1:8081',
                                                         'streaming': False, 'manager': False, 'journalPath': None})

    def test_incomplete_release_reports_actionable_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(AGENT_DIR / 'inspect_application.py'),
                                     '--release-root', directory, '--config', str(Path(directory) / 'bootstrap.yaml')],
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('release source is incomplete', result.stderr)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
