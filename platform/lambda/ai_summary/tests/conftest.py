"""Put ai_summary/ + scanner_core/ on sys.path + set dummy DB env vars
so the Lambda's modules (main, framework_meta) import by bare name in
tests, matching how they resolve at runtime.

At runtime, framework_meta.py lands in the Lambda's root via CDK
bundling (api-stack.ts `lambdaCodeWithSharedMeta`) which cps it from
scanner_core/. At test time we get the same import resolution by
putting scanner_core/ on sys.path. The DB ARNs are unused in unit
tests because rds_data is mocked, but main.py reads them at module
load."""
import os
import sys
from pathlib import Path

_LAMBDA = Path(__file__).resolve().parent.parent
_SCANNER_CORE = _LAMBDA.parent / "scanner_core"
sys.path.insert(0, str(_LAMBDA))
sys.path.insert(0, str(_SCANNER_CORE))

os.environ.setdefault("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
os.environ.setdefault("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
os.environ.setdefault("DB_NAME",        "test_db")
