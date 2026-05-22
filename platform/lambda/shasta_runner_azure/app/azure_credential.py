"""Service-principal credential setup for the Azure scanner.

All selected subscriptions of a connection share ONE service principal,
so the SP credentials are connection-constant. They are injected into
os.environ once at process start; Shasta's AzureClient then picks them
up via DefaultAzureCredential. Because the values are constant for the
whole run, the os.environ write is safe even though the scan units run
in parallel threads.
"""
from __future__ import annotations

import os


def apply_sp_credentials(secret: dict) -> None:
    """Inject the service-principal credentials from the connection
    secret JSON into os.environ for DefaultAzureCredential to consume.
    `secret` must carry `client_id`, `client_secret`, `azure_tenant_id`.
    """
    os.environ["AZURE_CLIENT_ID"]     = secret["client_id"]
    os.environ["AZURE_CLIENT_SECRET"] = secret["client_secret"]
    os.environ["AZURE_TENANT_ID"]     = secret["azure_tenant_id"]
