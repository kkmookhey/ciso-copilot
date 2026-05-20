# platform/lambda/policies/tests/conftest.py
"""Make policies/main.py importable by bare name in tests."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "ciso_copilot")
os.environ.setdefault("ANTHROPIC_SECRET_NAME", "ciso-copilot/anthropic-api-key")
