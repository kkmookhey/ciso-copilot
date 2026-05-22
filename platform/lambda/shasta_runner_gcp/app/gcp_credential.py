"""Workload Identity Federation credential setup for the GCP scanner.

Builds the `external_account` info dict that google-auth's
`aws.Credentials.from_info` consumes. The scanner's AWS task role is the
only "key": google-auth signs an AWS GetCallerIdentity request as the
subject token, GCP STS exchanges it, and the customer's reader service
account is impersonated. No private key on disk anywhere.

Pure: no google-auth import here, so it is unit-testable without the
scanner runtime. main.py calls `google.auth.aws.Credentials.from_info`
on the dict this returns.
"""
from __future__ import annotations

import os


def export_aws_credentials_to_env(frozen_credentials, env=None) -> None:
    """Export resolved AWS credentials into an environment mapping so
    google-auth's AWS external-account credential source can sign the
    GetCallerIdentity subject token.

    google-auth's `aws.Credentials` source reads AWS creds from env vars
    or the EC2 instance metadata server — neither is populated for an ECS
    Fargate task role, which is served by the container credentials
    endpoint instead. The caller resolves the credentials with boto3
    (which supports the container provider) and passes the frozen
    credentials object here. `frozen_credentials` exposes `.access_key`,
    `.secret_key`, and `.token`.

    `env` defaults to `os.environ`; tests pass a plain dict to stay
    isolated. The exported credentials are a point-in-time snapshot —
    google-auth re-signs from these values without refreshing them, so a
    scan that outlives the credential TTL would fail. Fine for the short
    Quick/Medium tiers; revisit for long Deep scans."""
    target = os.environ if env is None else env
    target["AWS_ACCESS_KEY_ID"]     = frozen_credentials.access_key
    target["AWS_SECRET_ACCESS_KEY"] = frozen_credentials.secret_key
    if frozen_credentials.token:
        target["AWS_SESSION_TOKEN"] = frozen_credentials.token


def build_external_account_info(
    wif_project_number: str,
    sa_email: str,
    wif_pool: str,
    wif_provider: str,
) -> dict:
    """Return the external_account info dict for WIF.

    `wif_project_number` is the project that hosts the Workload Identity
    Pool (in single-project onboarding, the scanned project itself; in
    org onboarding, the host project)."""
    audience = (
        f"//iam.googleapis.com/projects/{wif_project_number}"
        f"/locations/global/workloadIdentityPools/{wif_pool}"
        f"/providers/{wif_provider}"
    )
    impersonation_url = (
        f"https://iamcredentials.googleapis.com/v1/projects/-"
        f"/serviceAccounts/{sa_email}:generateAccessToken"
    )
    return {
        "type":                              "external_account",
        "audience":                          audience,
        "subject_token_type":                "urn:ietf:params:aws:token-type:aws4_request",
        "service_account_impersonation_url": impersonation_url,
        "token_url":                         "https://sts.googleapis.com/v1/token",
        "credential_source": {
            "environment_id":                 "aws1",
            "regional_cred_verification_url": "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15",
        },
    }
