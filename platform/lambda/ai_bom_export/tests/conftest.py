"""Put ai_bom_export/ on sys.path + dummy DB env vars.

At runtime, main.py lands in the Lambda root via CDK bundling.
At test time we get the same import resolution by inserting
the Lambda directory onto sys.path. DB ARNs are unused in
unit tests because rds_data is mocked, but main.py reads them
at module load.
"""
import os
import sys
from pathlib import Path

_LAMBDA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LAMBDA))

os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "test_db")
