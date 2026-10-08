"""Read enabled capabilities in the same identity as the Windows business service."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path, required=True)
parser.add_argument("--release-root", type=Path, required=True)
args = parser.parse_args()
try:
    source = args.release_root.resolve() / "src"
    package = source / "python_mt5_sidecar"
    if not (package / "__init__.py").is_file():
        raise SystemExit("Application preflight failed: release source is incomplete; expected src/python_mt5_sidecar/__init__.py. Check the release package.")
    # Use the selected release instead of any old installation or inherited PYTHONPATH.
    sys.path.insert(0, str(source))
    if importlib.util.find_spec("python_mt5_sidecar.config_loader") is not None:
        from python_mt5_sidecar.config_loader import load_config
        from python_mt5_sidecar.mt5.credentials import SystemMt5SecretResolver
    else:
        from python_mt5_sidecar.query_config import load_query_config as load_config
        from python_mt5_sidecar.security import SystemMt5SecretResolver

    config = load_config(args.config)
    if not config.nacos.enabled or config.mt5.adapter != "vendor":
        raise ValueError("Nacos and vendor SDK required")
    SystemMt5SecretResolver().resolve(config.mt5.server_ref, config.mt5.credential_ref)
    address = config.http.host
    if address in {"0.0.0.0", "::"}:
        address = "127.0.0.1" if address == "0.0.0.0" else "::1"
    if ":" in address:
        address = f"[{address}]"
    streaming = getattr(config, "streaming_enabled", None)
    if streaming is None:
        streaming = bool(config.callbacks_enabled or getattr(config, "group_price_enabled", False))
    manager = getattr(config, "manager_gateway", None)
    print(json.dumps({
        "port": config.http.port,
        "healthUrl": f"http://{address}:{config.http.port}",
        "streaming": streaming,
        "manager": manager is not None,
        "journalPath": str(manager.journal_path) if manager is not None else None,
    }))
except ModuleNotFoundError as error:
    raise SystemExit(f"Application preflight failed: missing Python module {error.name}; check this release's source and locked dependencies.") from None
except AttributeError as error:
    raise SystemExit(f"Application preflight failed: release configuration is missing field {error.name}; check the Agent and Sidecar interface versions.") from None
except Exception as error:
    raise SystemExit(f"Application preflight failed ({type(error).__name__}); check local credentials and Nacos.") from None
