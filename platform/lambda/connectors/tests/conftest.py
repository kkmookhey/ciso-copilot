"""Put connectors/ and _shared/ on sys.path so `from connectors...` and
`from mcp_oauth...` imports resolve in tests, matching the runtime layout
(connectors/main.py + _shared bundle on PYTHONPATH).

Env vars below are not used in unit tests but main.py's submodules read them
at import-or-call time."""
import os
import sys
from pathlib import Path

_LAMBDA_DIR = Path(__file__).resolve().parent.parent.parent  # platform/lambda
sys.path.insert(0, str(_LAMBDA_DIR))                          # makes `connectors.*` importable
sys.path.insert(0, str(_LAMBDA_DIR / "_shared"))              # makes `mcp_oauth.*` importable

os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "ciso_copilot")

# Short-circuit connectors._secrets — it would otherwise call SSM at module
# import time, which fails without AWS creds in CI. Tests that need real
# values monkeypatch.setenv these to overrides per test.
os.environ.setdefault("SLACK_CLIENT_ID",     "test-client-id")
os.environ.setdefault("SLACK_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("STATE_JWT_SECRET",    "x" * 32)
