"""Startup checks without claiming deployment tasks or resetting Agent state."""

import argparse
import json
import os
import socket
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path, PureWindowsPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


class CheckError(Exception):
    pass


def load_config(path):
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise CheckError(
            f"config.json JSON error at line {error.lineno}, column {error.colno}: {error.msg}. "
            "Use forward slashes in paths (C:/Users/Administrator/...)."
        ) from None
    except OSError as error:
        raise CheckError(f"Cannot read configuration ({type(error).__name__}): {path}") from None
    except UnicodeError:
        raise CheckError("config.json must be saved as UTF-8") from None
    if not isinstance(config, dict):
        raise CheckError("config.json must contain a JSON object")
    for name in ("platformUrl", "cluster", "stateDirectory"):
        if not isinstance(config.get(name), str) or not config[name].strip():
            raise CheckError(f"config.json is missing a non-empty {name}")
    try:
        parsed = urlparse(config["platformUrl"])
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise CheckError("platformUrl has an invalid hostname or port") from None
    if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CheckError("platformUrl must be an HTTP(S) address without credentials, query or fragment")
    if not hostname:
        raise CheckError("platformUrl must include a hostname")
    if hostname == "deploy.example.com":
        raise CheckError("Replace the example platformUrl with your actual platform address")
    token = config.get("agentToken") or os.environ.get("WINDOWS_AGENT_TOKEN")
    if not isinstance(token, str) or not token.strip() or token.startswith("REPLACE_"):
        raise CheckError("Set agentToken to the Token registered in the platform")
    applications = config.get("applications")
    if not isinstance(applications, dict) or len(applications) != 1:
        raise CheckError("applications must contain exactly one MT5 application")
    application = next(iter(applications.values()))
    if not isinstance(application, dict):
        raise CheckError("Application settings must be a JSON object")
    if "Environment" in application and not isinstance(application["Environment"], dict):
        raise CheckError("Application Environment must be a JSON object")
    for name in ("InstallRoot", "Python", "Uv", "ServiceWrapper"):
        value = application.get(name)
        if not isinstance(value, str) or not PureWindowsPath(value).is_absolute():
            raise CheckError(f"Application {name} must be an absolute Windows path")
    state = PureWindowsPath(config["stateDirectory"])
    if not state.is_absolute() or len(state.parts) < 2:
        raise CheckError("stateDirectory must be an absolute application directory, not a drive root")
    return config


def check_runtime(config):
    if sys.version_info < (3, 13):
        raise CheckError("Agent requires Python 3.13 or newer")
    if os.name == "nt":
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            raise CheckError("Python runtime does not provide Windows file locking (msvcrt)") from None
    root = Path(config["stateDirectory"])
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root, prefix="agent-start-check-"):
            pass
    except OSError as error:
        raise CheckError(f"stateDirectory is not writable ({type(error).__name__}): {root}") from None
    database = root / "tasks.sqlite3"
    running = 0
    if database.exists():
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
                running = connection.execute("SELECT count(*) FROM tasks WHERE status='running'").fetchone()[0]
        except sqlite3.Error as error:
            raise CheckError(f"Cannot read Agent task database ({type(error).__name__}); inspect local state before restarting") from None
    return {"ok": True, "runningTasks": running, "pythonVersion": sys.version.split()[0]}


def check_platform(config):
    token = config.get("agentToken") or os.environ.get("WINDOWS_AGENT_TOKEN")
    headers = {"X-Agent-Token": token, "Content-Type": "application/json", "Accept": "application/json"}
    for key, environment in (("CF-Access-Client-Id", "CF_ACCESS_CLIENT_ID"), ("CF-Access-Client-Secret", "CF_ACCESS_CLIENT_SECRET")):
        if os.environ.get(environment):
            headers[key] = os.environ[environment]
    body = {"cluster": config["cluster"], "instanceId": config.get("instanceId") or socket.gethostname()}
    # Send Agent heartbeat, never request or execute a deployment task.
    request = Request(config["platformUrl"].rstrip("/") + "/api/windows-agent/heartbeat", data=json.dumps(body).encode(), headers=headers)
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise CheckError("Platform heartbeat returned an unexpected response; check platformUrl and reverse proxy")
    except HTTPError as error:
        error.close()
        advice = {
            400: "Register this exact cluster name in the platform Cluster Management page",
            401: "agentToken must match the Token registered for this server",
            403: "Check Cloudflare/WAF rules and service-account access to the platform",
            404: "Update the platform to a version with Windows Agent routes and check platformUrl",
        }.get(error.code, "Check the platform service and reverse proxy")
        raise CheckError(f"Platform heartbeat failed (HTTP {error.code}). {advice}.") from None
    except (URLError, TimeoutError, OSError) as error:
        raise CheckError(f"Cannot connect to platform ({type(error).__name__}); check DNS, HTTPS certificate, proxy and firewall") from None
    except (ValueError, UnicodeError):
        raise CheckError("Platform returned non-JSON content; check platformUrl, login redirects and reverse proxy") from None
    return {"ok": True, "cluster": config["cluster"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=("runtime", "platform"), default="runtime")
    arguments = parser.parse_args()
    try:
        config = load_config(arguments.config)
        result = check_runtime(config) if arguments.mode == "runtime" else check_platform(config)
        print(json.dumps(result))
        return 0
    except CheckError as error:
        # Windows PowerShell 5.1 treats redirected native stderr as ErrorRecord.
        # Keep the full diagnostic on stdout and use the exit code for failure.
        print(f"CHECK FAILED: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
