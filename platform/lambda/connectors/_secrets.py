"""Lazy SSM-backed secrets for the connectors Lambda.

CloudFormation rejects SecureString SSM parameters as Lambda env vars
(known CFN type-system limitation). Instead the Lambda fetches the
three sensitive values at module-import time and sets them on
os.environ so the rest of the code can continue to read them via the
standard `os.environ["SLACK_CLIENT_ID"]` pattern.

One SSM round-trip per cold start. Cached for the life of the
execution context.
"""
from __future__ import annotations
import os
import boto3

_ssm = boto3.client("ssm")

_SSM_BACKED = {
    "SLACK_CLIENT_ID":     "/cisocopilot/connectors/slack/client-id",
    "SLACK_CLIENT_SECRET": "/cisocopilot/connectors/slack/client-secret",
    "STATE_JWT_SECRET":    "/cisocopilot/connectors/state-jwt-secret",
}


def _load() -> None:
    for env_name, path in _SSM_BACKED.items():
        if os.environ.get(env_name):
            continue  # already populated (test env, manual override)
        resp = _ssm.get_parameter(Name=path, WithDecryption=True)
        os.environ[env_name] = resp["Parameter"]["Value"]


_load()
