import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import types
import unittest

from build_cache import cache_lock, checkout_source, source_cache_key


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.commands = []
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.commit("one")
        self.task = {"repo": str(self.repo), "gitCredentialId": "credential-a", "organizationId": "one"}

        def command(args, cwd=None, **kwargs):
            self.commands.append(args)
            result = subprocess.run(args, cwd=cwd, env=kwargs.get("env"), capture_output=True, text=True)
            return result.returncode, result.stdout + result.stderr

        self.s = types.SimpleNamespace(DATA_DIR=self.root / "data", GIT_SOURCE_CACHE=True,
            run_command=command, run_command_stream=lambda args, _execution, **kw: (*command(args, **kw), 0),
            ensure_execution_active=lambda _execution: None, append_log=lambda *_args: None,
            format_duration=str, redact_secret_text=lambda text, _secret: text)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, stderr=subprocess.STDOUT, text=True).strip()

    def commit(self, text):
        (self.repo / "app.txt").write_text(text)
        self.git("add", ".")
        self.git("commit", "-m", text)

    def checkout(self, name, branch="main"):
        path = self.root / name
        checkout_source(self.s, name, self.task, branch, path, str(self.repo), os.environ.copy(), None)
        return path

    def test_cache_fetches_new_commits_and_workspaces_are_isolated(self):
        first = self.checkout("first")
        self.commit("two")
        second = self.checkout("second")
        self.assertEqual((first / "app.txt").read_text(), "one")
        self.assertEqual((second / "app.txt").read_text(), "two")
        self.assertFalse((second / ".git/objects/info/alternates").exists())
        self.assertEqual(len(list((self.s.DATA_DIR / "cache/git").glob("*.git"))), 1)
        self.assertEqual(sum("fetch" in cmd for cmd in self.commands), 2)
        cached = next((self.s.DATA_DIR / "cache/git").glob("*.git"))
        self.assertFalse((cached / "FETCH_HEAD").exists())
        clones = [cmd for cmd in self.commands if cmd[:2] == ["git", "clone"]]
        self.assertEqual(len(clones), 2)
        self.assertTrue(all(cmd[-2] == cached.resolve().as_uri() for cmd in clones))
        self.assertTrue(all("--depth" in cmd and "--no-local" in cmd for cmd in clones))
        # Local cache transport must never become a fresh remote clone per run.
        self.assertNotIn(str(self.repo), [cmd[-2] for cmd in clones])
        self.assertNotIn(str(self.repo), (cached / "config").read_text())
        # Forced branch rewrites must also resolve the current remote commit.
        self.git("reset", "--hard", "HEAD~1")
        self.assertEqual((self.checkout("third") / "app.txt").read_text(), "one")

    def test_failed_remote_fetch_never_deploys_cached_source(self):
        self.checkout("first")
        self.repo.rename(self.root / "unavailable")
        with self.assertRaisesRegex(RuntimeError, "代码拉取失败"):
            self.checkout("second")
        self.assertFalse((self.root / "second").exists())

    def test_branch_and_credential_scopes_and_disabled_cache(self):
        key = source_cache_key(self.task)
        for task in ({**self.task, "gitCredentialId": "b"}, {**self.task, "organizationId": "b"},
                     {**self.task, "repo": "https://example.invalid/other.git"}):
            self.assertNotEqual(key, source_cache_key(task))
        with self.assertRaises(ValueError):
            self.checkout("invalid", "--upload-pack=bad")
        self.s.GIT_SOURCE_CACHE = False
        self.assertEqual((self.checkout("plain") / "app.txt").read_text(), "one")

    def test_branches_share_objects_but_always_resolve_their_own_remote_ref(self):
        self.git('branch', 'release')
        self.checkout('main-first')
        cached = next((self.s.DATA_DIR / 'cache/git').glob('*.git'))
        def object_count():
            output = subprocess.check_output(['git', '-C', str(cached), 'count-objects', '-v'], text=True)
            counts = dict(line.split(': ', 1) for line in output.splitlines())
            return int(counts['count']) + int(counts['in-pack'])
        before = object_count()
        second = self.checkout('release-first', 'release')
        self.assertEqual(object_count(), before)
        self.assertEqual((second / 'app.txt').read_text(), 'one')
        self.git('checkout', 'release')
        self.commit('release-only')
        self.assertEqual((self.checkout('release-new', 'release') / 'app.txt').read_text(), 'release-only')
        self.assertEqual((self.checkout('main-again') / 'app.txt').read_text(), 'one')
        self.git('checkout', 'main')
        self.git('branch', '-D', 'release')
        with self.assertRaisesRegex(RuntimeError, '代码拉取失败'):
            self.checkout('deleted-branch', 'release')
        self.assertFalse((self.root / 'deleted-branch').exists())
        self.assertEqual(len(list((self.s.DATA_DIR / 'cache/git').glob('*.git'))), 1)
        self.assertEqual(sum('fetch' in command for command in self.commands), 5)

    def test_five_branches_share_one_cache_and_have_independent_workspaces(self):
        for i in range(5):
            self.git('branch', f'release-{i}')
        results, errors = [], []
        barrier = threading.Barrier(6)
        def checkout(i):
            try:
                barrier.wait(5)
                workspace = self.checkout(f'workspace-{i}', f'release-{i}')
                branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=workspace, text=True).strip()
                results.append((branch, (workspace / 'app.txt').read_text()))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=checkout, args=(i,)) for i in range(5)]
        for thread in threads:
            thread.start()
        barrier.wait(5)
        for thread in threads:
            thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {(f'release-{i}', 'one') for i in range(5)})
        self.assertEqual(len(list((self.s.DATA_DIR / 'cache/git').glob('*.git'))), 1)
        clones = [command for command in self.commands if command[:2] == ['git', 'clone']]
        self.assertEqual(len({command[-2] for command in clones}), 1)
        self.assertEqual(sum('fetch' in command for command in self.commands), 5)

    def legacy_cache(self):
        identity = [self.task.get('repo'), self.task.get('gitCredentialId'), self.task.get('organizationIds'),
                    self.task.get('organizationId'), 'main']
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cached = self.s.DATA_DIR / 'cache/git' / f'{key}.git'
        cached.parent.mkdir(parents=True, exist_ok=True)
        for args in (['git', 'init', '--bare', str(cached)],
                     ['git', '-C', str(cached), 'fetch', '--depth', '1', '--no-write-fetch-head', '--',
                      str(self.repo), '+refs/heads/main:refs/heads/main']):
            subprocess.run(args, check=True, capture_output=True)
        return cached

    def test_old_branch_cache_seeds_new_cache_locally_before_remote_validation(self):
        legacy = self.legacy_cache()
        self.assertEqual((self.checkout('upgrade') / 'app.txt').read_text(), 'one')
        fetches = [command for command in self.commands if 'fetch' in command]
        self.assertEqual(len(fetches), 2)
        self.assertIn(legacy.resolve().as_uri(), fetches[0])
        self.assertIn(str(self.repo), fetches[1])
        self.assertNotEqual(fetches[0][2], str(legacy))
        self.assertTrue(legacy.exists())

    def test_seeded_old_cache_never_authorizes_an_offline_release(self):
        self.legacy_cache()
        self.repo.rename(self.root / 'unavailable')
        with self.assertRaisesRegex(RuntimeError, '代码拉取失败'):
            self.checkout('upgrade-offline')
        self.assertFalse((self.root / 'upgrade-offline').exists())

    def test_cache_lock_wait_is_cancellable(self):
        path = self.root / "cache.lock"
        errors = []
        waiting, cancel = threading.Event(), threading.Event()
        def cancelled():
            waiting.set()
            if cancel.is_set():
                raise RuntimeError("发布已取消")
        def waiter():
            try:
                with cache_lock(path, cancelled):
                    self.fail("entered cancelled cache")
            except RuntimeError as exc:
                errors.append(str(exc))
        with cache_lock(path):
            thread = threading.Thread(target=waiter)
            thread.start()
            self.assertTrue(waiting.wait(1))
            cancel.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["发布已取消"])
