import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server as s


TRANSFER_ERROR = (
    'error: RPC failed; curl 18 transfer closed with outstanding read data remaining\n'
    'error: 53629 bytes of body are still expected\n'
    'fetch-pack: unexpected disconnect while reading sideband packet\n'
    'fatal: early EOF\nfatal: fetch-pack: invalid index-pack output\n'
)


class GitCloneTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work_dir = Path(directory.name)
        self.src_dir = self.work_dir / 'src'
        self.logs = []
        for name, options in (
            ('append_log', {'side_effect': lambda _, message: self.logs.append(message)}),
            ('is_execution_cancelled', {'return_value': False}),
        ):
            mock = patch.object(s, name, **options)
            mock.start()
            self.addCleanup(mock.stop)

    def test_transfer_failure_cleans_checkout_preserves_key_and_retries(self):
        key = self.work_dir / '.ssh' / 'id_deploy'
        key.parent.mkdir()
        key.write_text('key')
        commands = []
        env = {'GIT_TERMINAL_PROMPT': '0'}

        def run(command, execution_id, **kwargs):
            commands.append(command)
            self.assertFalse(self.src_dir.exists())
            self.assertTrue(key.exists())
            self.assertIs(kwargs['env'], env)
            self.src_dir.mkdir()
            (self.src_dir / 'partial-pack').write_text('partial')
            if len(commands) == 1:
                return 128, TRANSFER_ERROR, 1
            return 0, 'done', 1

        with patch.object(s, 'run_command_stream', side_effect=run), \
                patch.object(s.time, 'sleep') as sleep:
            elapsed = s.clone_repository('test', 'https://git.example/app.git', 'release', self.src_dir, env)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][1], 'clone')
        self.assertEqual(commands[1][1:3], ['-c', 'http.version=HTTP/1.1'])
        self.assertEqual(commands[1][-6:], ['--depth', '1', '--branch', 'release', 'https://git.example/app.git', str(self.src_dir)])
        self.assertEqual(sleep.call_count, 4)
        self.assertGreaterEqual(elapsed, 0)

    def test_transfer_failure_stops_after_three_attempts(self):
        with patch.object(s, 'run_command_stream', return_value=(128, TRANSFER_ERROR, 1)) as run, \
                patch.object(s.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, '已尝试 3 次'):
                s.clone_repository('test', 'http://git.example/app.git', 'main', self.src_dir)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 12)
        self.assertTrue(any('第 3/3 次尝试失败' in log for log in self.logs))

    def test_configuration_errors_do_not_retry(self):
        for error in (
            'fatal: Authentication failed', 'fatal: HTTP Basic: Access denied',
            'fatal: Remote branch missing not found in upstream origin',
            'fatal: repository not found', 'fatal: SSL certificate problem',
            'fatal: Permission denied (publickey).', 'fatal: No space left on device',
        ):
            with self.subTest(error=error), \
                    patch.object(s, 'run_command_stream', return_value=(128, error, 1)) as run, \
                    patch.object(s.time, 'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, '代码拉取失败'):
                    s.clone_repository('test', 'https://git.example/app.git', 'main', self.src_dir)
                run.assert_called_once()
                sleep.assert_not_called()
                self.assertFalse(s.is_retryable_git_transfer_error(error + '\nunexpected disconnect'))

    def test_cancellation_during_backoff_prevents_next_attempt(self):
        cancelled = False

        def cancel(_seconds):
            nonlocal cancelled
            cancelled = True

        with patch.object(s, 'is_execution_cancelled', side_effect=lambda _: cancelled), \
                patch.object(s, 'run_command_stream', return_value=(128, TRANSFER_ERROR, 1)) as run, \
                patch.object(s.time, 'sleep', side_effect=cancel):
            with self.assertRaisesRegex(RuntimeError, '发布已取消'):
                s.clone_repository('test', 'https://git.example/app.git', 'main', self.src_dir)
        run.assert_called_once()

    def test_cancelled_stream_is_not_retried(self):
        with patch.object(s, 'run_command_stream', side_effect=RuntimeError('发布已取消')) as run:
            with self.assertRaisesRegex(RuntimeError, '发布已取消'):
                s.clone_repository('test', 'https://git.example/app.git', 'main', self.src_dir)
        run.assert_called_once()

    def test_ssh_retry_does_not_set_http_options(self):
        with patch.object(s, 'run_command_stream', side_effect=[
            (128, 'Connection reset by peer', 1), (0, '', 1),
        ]) as run, patch.object(s.time, 'sleep'):
            s.clone_repository('test', 'git@git.example:app.git', 'main', self.src_dir)
        self.assertTrue(all(call.args[0][1] == 'clone' for call in run.call_args_list))

    def test_credentials_are_redacted_in_stream_and_failure(self):
        secret = {'username': 'deployer', 'secret': 'token/value'}
        output = 'fatal: Authentication failed for http://deployer:token%2Fvalue@git.example/app.git'

        def run(*args, **kwargs):
            self.assertNotIn('token', kwargs['redact'](output))
            return 128, output, 1

        with patch.object(s, 'run_command_stream', side_effect=run):
            with self.assertRaises(RuntimeError) as error:
                s.clone_repository('test', 'http://git.example/app.git', 'main', self.src_dir, secret=secret)
        self.assertNotIn('token', str(error.exception))
        self.assertIn('Git 认证失败', str(error.exception))
        self.assertEqual(s.redact_secret_text('http://user:password@host/app.git'), 'http://***:***@host/app.git')

    def test_only_transient_errors_are_classified_for_retry(self):
        for error in (TRANSFER_ERROR, 'curl 28 timeout', 'curl 56 recv failure',
                      'curl 92 HTTP/2 stream closed', 'Connection reset by peer',
                      'Could not resolve hostname git.example',
                      'fatal: unable to access URL: The requested URL returned error: 503'):
            with self.subTest(error=error):
                self.assertTrue(s.is_retryable_git_transfer_error(error))
        for error in ('invalid index-pack output', 'curl 60 certificate error',
                      'The requested URL returned error: 403', 'unknown failure'):
            with self.subTest(error=error):
                self.assertFalse(s.is_retryable_git_transfer_error(error))

    def test_real_clone_checks_out_selected_branch_after_partial_transfer(self):
        origin = self.work_dir / 'origin'
        subprocess.run(['git', 'init', '-q', str(origin)], check=True)
        (origin / 'app.txt').write_text('selected branch')
        subprocess.run(['git', '-C', str(origin), 'add', 'app.txt'], check=True)
        commit_env = {**os.environ, 'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.com',
                      'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.com'}
        subprocess.run(['git', '-C', str(origin), '-c', 'commit.gpgsign=false', 'commit', '-qm', 'initial'],
                       check=True, env=commit_env)
        subprocess.run(['git', '-C', str(origin), 'branch', 'release'], check=True)
        real_run = s.run_command_stream
        attempts = 0

        def run(command, execution_id, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                self.src_dir.mkdir()
                (self.src_dir / 'partial-pack').write_text('incomplete')
                return 128, TRANSFER_ERROR, 1
            return real_run(command, execution_id, **kwargs)

        with patch.object(s, 'run_command_stream', side_effect=run), patch.object(s.time, 'sleep'):
            s.clone_repository('test', origin.as_uri(), 'release', self.src_dir, env=s.clone_environment(self.work_dir, None))
        self.assertEqual(attempts, 2)
        self.assertEqual((self.src_dir / 'app.txt').read_text(), 'selected branch')
        self.assertFalse((self.src_dir / 'partial-pack').exists())
        branch = subprocess.run(['git', '-C', str(self.src_dir), 'branch', '--show-current'],
                                check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, 'release')
        self.assertTrue((self.src_dir / '.git' / 'shallow').exists())
        self.assertTrue(s.read_git_commit_info(self.src_dir).get('hash'))


if __name__ == '__main__':
    unittest.main()
