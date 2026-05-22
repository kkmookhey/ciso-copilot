"""azure_credential.apply_sp_credentials injects the SP credentials from
the connection secret into os.environ."""
import os

from azure_credential import apply_sp_credentials


def test_injects_all_three_env_vars(monkeypatch):
    for k in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"):
        monkeypatch.delenv(k, raising=False)
    apply_sp_credentials({
        "client_id": "appid-1",
        "client_secret": "secret-1",
        "azure_tenant_id": "tenant-1",
    })
    assert os.environ["AZURE_CLIENT_ID"] == "appid-1"
    assert os.environ["AZURE_CLIENT_SECRET"] == "secret-1"
    assert os.environ["AZURE_TENANT_ID"] == "tenant-1"
