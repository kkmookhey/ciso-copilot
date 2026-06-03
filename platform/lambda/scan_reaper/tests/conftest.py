"""Put scan_reaper/ on sys.path and set the env vars the module reads at
import time, so `import main` works in test collection without AWS config."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DB_CLUSTER_ARN", "arn:cluster")
os.environ.setdefault("DB_SECRET_ARN", "arn:secret")
os.environ.setdefault("DB_NAME", "ciso_copilot")
os.environ.setdefault("SCAN_CLUSTER_ARN", "arn:aws:ecs:us-east-1:0:cluster/scan")
os.environ.setdefault("GRACE_MINUTES", "20")
