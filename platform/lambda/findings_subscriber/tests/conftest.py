"""Put findings_subscriber/ + _shared/ on sys.path."""
import os, sys
from pathlib import Path

_LAMBDA_DIR = Path(__file__).resolve().parent.parent.parent  # platform/lambda
sys.path.insert(0, str(_LAMBDA_DIR))
sys.path.insert(0, str(_LAMBDA_DIR / "_shared"))

os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "ciso_copilot")
os.environ.setdefault("AUTONOMOUS_BROADCAST_SEEN_TABLE", "test-seen")
os.environ.setdefault("AUTONOMOUS_RULE_SSM_PARAM", "/test/enabled")
os.environ.setdefault("WEB_BASE_URL", "https://app.shasta.io")
os.environ.setdefault("CONNECTOR_TOKENS_KEY_ARN", "arn:aws:kms:us-east-1:0:key/test")
