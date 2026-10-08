"""Read enabled capabilities in the same identity as the Windows business service."""

import argparse
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
    if not all((package / name).is_file() for name in ("__init__.py", "query_config.py", "security.py")):
        raise SystemExit("Application preflight failed: release source is incomplete; expected src/python_mt5_sidecar/{__init__,query_config,security}.py. Check the release package.")
    # Use the selected release instead of any old installation or inherited PYTHONPATH.
    sys.path.insert(0, str(source))
    from python_mt5_sidecar.query_config import load_query_config
    from python_mt5_sidecar.security import SystemMt5SecretResolver

    config = load_query_config(args.config)
    if not config.nacos.enabled or config.mt5.adapter != "vendor":
        raise ValueError("Nacos and vendor SDK required")
    SystemMt5SecretResolver().resolve(config.mt5.server_ref, config.mt5.credential_ref)
    address = config.http.host
    if address in {"0.0.0.0", "::"}:
        address = "127.0.0.1" if address == "0.0.0.0" else "::1"
    if ":" in address:
        address = f"[{address}]"
    print(json.dumps({
        "port": config.http.port,
        "healthUrl": f"http://{address}:{config.http.port}",
        "streaming": config.streaming_enabled,
        "manager": config.manager_gateway is not None,
        "journalPath": str(config.manager_gateway.journal_path) if config.manager_gateway else None,
    }))
except ModuleNotFoundError as error:
    raise SystemExit(f"Application preflight failed: missing Python module {error.name}; check this release's source and locked dependencies.") from None
except Exception as error:
    raise SystemExit(f"Application preflight failed ({type(error).__name__}); check local credentials and Nacos.") from None
