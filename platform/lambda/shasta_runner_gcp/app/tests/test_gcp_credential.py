from dataclasses import dataclass

from gcp_credential import build_external_account_info, export_aws_credentials_to_env


@dataclass
class _FrozenCreds:
    access_key: str
    secret_key: str
    token: str | None


def test_builds_audience_from_wif_project_pool_provider():
    info = build_external_account_info(
        wif_project_number="123456789",
        sa_email="ciso-copilot-reader@proj.iam.gserviceaccount.com",
        wif_pool="ciso-copilot-pool",
        wif_provider="ciso-copilot-aws-provider",
    )
    assert info["audience"] == (
        "//iam.googleapis.com/projects/123456789"
        "/locations/global/workloadIdentityPools/ciso-copilot-pool"
        "/providers/ciso-copilot-aws-provider"
    )


def test_builds_impersonation_url_from_sa_email():
    info = build_external_account_info(
        wif_project_number="123456789",
        sa_email="ciso-copilot-reader@proj.iam.gserviceaccount.com",
        wif_pool="pool", wif_provider="provider",
    )
    assert info["service_account_impersonation_url"] == (
        "https://iamcredentials.googleapis.com/v1/projects/-"
        "/serviceAccounts/ciso-copilot-reader@proj.iam.gserviceaccount.com"
        ":generateAccessToken"
    )


def test_static_fields_are_aws_external_account_shape():
    info = build_external_account_info("1", "sa@x.iam", "p", "pr")
    assert info["type"] == "external_account"
    assert info["subject_token_type"] == "urn:ietf:params:aws:token-type:aws4_request"
    assert info["token_url"] == "https://sts.googleapis.com/v1/token"
    assert info["credential_source"]["environment_id"] == "aws1"
    assert "GetCallerIdentity" in info["credential_source"]["regional_cred_verification_url"]


def test_export_aws_credentials_sets_env_vars():
    env: dict[str, str] = {}
    export_aws_credentials_to_env(_FrozenCreds("AKIA123", "secret456", "token789"), env)
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA123"
    assert env["AWS_SECRET_ACCESS_KEY"] == "secret456"
    assert env["AWS_SESSION_TOKEN"] == "token789"


def test_export_aws_credentials_omits_session_token_when_absent():
    env: dict[str, str] = {}
    export_aws_credentials_to_env(_FrozenCreds("AKIA123", "secret456", None), env)
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA123"
    assert "AWS_SESSION_TOKEN" not in env
