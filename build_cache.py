"""Local build caches. Cached Git objects never authorize a release by themselves."""
import fcntl
import hashlib
import json
import time
from contextlib import contextmanager


@contextmanager
def cache_lock(path, check_active=lambda: None):
    """Cross-process lock with cancellation while waiting (Linux/macOS workers)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        while True:
            check_active()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def source_cache_key(task, branch):
    # Credentials and organization scopes must not share a source cache.
    identity = [task.get("repo"), task.get("gitCredentialId"),
                task.get("organizationIds"), task.get("organizationId"), branch]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def checkout_source(s, execution_id, task, branch, src_dir, repo, env, secret):
    redact = lambda text: s.redact_secret_text(text, secret)

    def run(args, cwd=None):
        code, _output, _elapsed = s.run_command_stream(args, execution_id, cwd=cwd, env=env, redact=redact)
        if code:
            raise RuntimeError("代码拉取失败，请检查分支、凭据、网络和 Git 缓存目录")

    started = time.monotonic()
    # Validate before interpolating a branch into a refspec or an option position.
    code, _ = s.run_command(["git", "check-ref-format", f"refs/heads/{branch}"])
    if code or branch.startswith("-"):
        raise ValueError("无效的发布分支")
    if not s.GIT_SOURCE_CACHE:
        run(["git", "clone", "--depth", "1", "--single-branch", "--branch", branch, "--", repo, str(src_dir)])
        return time.monotonic() - started

    key = source_cache_key(task, branch)
    root = s.DATA_DIR / "cache" / "git"
    cached = root / f"{key}.git"
    s.append_log(execution_id, "准备 Git 对象缓存，校验远端分支后复用本地对象")
    lock_started = time.monotonic()
    with cache_lock(root / f"{key}.lock", lambda: s.ensure_execution_active(execution_id)):
        s.append_log(execution_id, f"等待 Git 缓存锁耗时: {s.format_duration(time.monotonic() - lock_started)}")
        if not (cached / "HEAD").exists():
            run(["git", "init", "--bare", str(cached)])
        # Do not configure an authenticated remote URL in the persistent cache.
        # Fetch must succeed every time, including after credential revocation.
        run(["git", "-C", str(cached), "fetch", "--depth", "1", "--no-tags", "--no-write-fetch-head", "--", repo,
             f"+refs/heads/{branch}:refs/heads/{branch}"])
        run(["git", "-C", str(cached), "symbolic-ref", "HEAD", f"refs/heads/{branch}"])
        # Copy into an isolated workspace: no alternates pointing outside the SDK
        # container and no shared working tree/node_modules between releases.
        # A local transport clone transfers only reachable shallow objects;
        # copying the bare objects directory would also copy expired commits.
        run(["git", "clone", "--no-local", "--depth", "1", "--single-branch", "--branch", branch,
             "--", cached.resolve().as_uri(), str(src_dir)])
        run(["git", "remote", "set-url", "origin", task["repo"]], cwd=src_dir)
    return time.monotonic() - started
