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
        key = source_cache_key(self.task, "main")
        for task, branch in (({**self.task, "gitCredentialId": "b"}, "main"),
                             ({**self.task, "organizationId": "b"}, "main"), (self.task, "other")):
            self.assertNotEqual(key, source_cache_key(task, branch))
        with self.assertRaises(ValueError):
            self.checkout("invalid", "--upload-pack=bad")
        self.s.GIT_SOURCE_CACHE = False
        self.assertEqual((self.checkout("plain") / "app.txt").read_text(), "one")

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
