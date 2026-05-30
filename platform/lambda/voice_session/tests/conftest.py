"""Put platform/lambda + _shared on sys.path so tests can import
`voice_session.main` (the package) and `mcp_oauth.*` (the shared bundle)
the same way the deployed Lambda does. Env vars below are set so main.py's
module-level reads succeed at import time."""
import os
import sys
from pathlib import Path

# parent.parent.parent → platform/lambda. The earlier version only added
# voice_session/ which made `from voice_session.main import …` unresolvable.
_LAMBDA_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_LAMBDA_DIR))
sys.path.insert(0, str(_LAMBDA_DIR / "_shared"))

os.environ.setdefault("DB_CLUSTER_ARN",     "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",      "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",            "test_db")
os.environ.setdefault("OPENAI_SECRET_NAME", "test-openai-secret")
