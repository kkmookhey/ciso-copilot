# app/assumed_role.py
"""Auto-refreshing assumed-role credentials for the AWS scanner.

The scanner assumes a customer's IAM role for a bounded duration (1h).
A full multi-region scan can outlast that, so static credentials expire
mid-scan and every later API call fails with RequestExpired. Wrapping
the role in botocore RefreshableCredentials makes every boto3 client
re-assume the role automatically before the credentials expire — a scan
of any length always has valid credentials.
"""
from __future__ import annotations

from typing import Any

import boto3
import botocore.session
from botocore.credentials import RefreshableCredentials

_ROLE_SESSION_NAME = "CISOCopilotScan"
_DURATION_SECONDS = 3600


def build_refreshable_credentials(
    sts_client, role_arn: str, external_id: str,
) -> RefreshableCredentials:
    """A RefreshableCredentials that (re-)assumes `role_arn` on demand.

    botocore calls `refresh_using` whenever the credentials are near
    expiry, so a long scan transparently re-assumes the role.
    """
    def _refresh() -> dict[str, Any]:
        resp = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=_ROLE_SESSION_NAME,
            ExternalId=external_id,
            DurationSeconds=_DURATION_SECONDS,
        )
        c = resp["Credentials"]
        return {
            "access_key":  c["AccessKeyId"],
            "secret_key":  c["SecretAccessKey"],
            "token":       c["SessionToken"],
            "expiry_time": c["Expiration"].isoformat(),
        }

    return RefreshableCredentials.create_from_metadata(
        metadata=_refresh(),
        refresh_using=_refresh,
        method="sts-assume-role",
    )


def session_from_credentials(credentials: RefreshableCredentials,
                             region: str) -> boto3.Session:
    """A boto3 Session for `region` backed by RefreshableCredentials.

    Assigning the credentials onto a botocore session is the documented
    way to give boto3 auto-refreshing credentials.
    """
    bc_session = botocore.session.get_session()
    bc_session._credentials = credentials
    return boto3.Session(botocore_session=bc_session, region_name=region)
