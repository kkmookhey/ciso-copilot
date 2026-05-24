"""Put ai_summary/ on sys.path + set dummy DB env vars so the Lambda's
modules (main, framework_meta) import by bare name in tests, matching
how they resolve at runtime. The DB ARNs are unused in unit tests
because rds_data is mocked, but main.py reads them at module load."""
import os
import sys
from pathlib import Path

_LAMBDA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LAMBDA))

os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "test_db")
