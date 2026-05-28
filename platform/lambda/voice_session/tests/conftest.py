"""Put voice_session/ on sys.path + set dummy env vars so the Lambda's
modules import by bare name in tests, matching runtime resolution.
The env vars are not used in unit tests but main.py reads them at import."""
import os
import sys
from pathlib import Path

_LAMBDA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LAMBDA))

os.environ.setdefault("DB_CLUSTER_ARN",     "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",      "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",            "test_db")
os.environ.setdefault("OPENAI_SECRET_NAME", "test-openai-secret")
