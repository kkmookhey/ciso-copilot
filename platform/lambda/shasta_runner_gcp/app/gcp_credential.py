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
