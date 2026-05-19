# platform/lambda/chat_session/tests/conftest.py
"""Make modules inside chat_session/ importable by bare name in tests."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Stub out the env vars that _db.py reads at module level so that import
# succeeds without a real AWS environment. Matches the pattern used in
# ai_scanner/tests/test_scan_runner.py (autouse stub_env fixture).
os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "ciso_copilot")
