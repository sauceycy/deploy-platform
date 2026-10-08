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
