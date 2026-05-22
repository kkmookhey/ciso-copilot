"""Parse an Azure Resource Manager ID into an entity-emission shape —
the Azure analog of arn_to_entity.parse_arn.

ARM IDs look like:
  /subscriptions/<sub>/resourceGroups/<rg>/providers/<ns>/<type>/<name>

Returns {kind, natural_key, display_name, attributes} for resource types
in _KIND_MAP, or None otherwise — caller keeps the finding and emits no
entity (same contract as parse_arn).
"""
from __future__ import annotations

import re

_ARM_RE = re.compile(
    r"^/subscriptions/(?P<sub>[^/]+)"
    r"/resourceGroups/(?P<rg>[^/]+)"
    r"/providers/(?P<ns>[^/]+)/(?P<type>[^/]+)/(?P<name>[^/]+)$",
    re.IGNORECASE,
)

# (provider-namespace lower, resource-type lower) -> entity kind.
_KIND_MAP = {
    ("microsoft.storage", "storageaccounts"):       "azure_storage_account",
    ("microsoft.compute", "virtualmachines"):       "azure_virtual_machine",
    ("microsoft.compute", "disks"):                 "azure_managed_disk",
    ("microsoft.network", "virtualnetworks"):       "azure_virtual_network",
    ("microsoft.network", "networksecuritygroups"): "azure_network_security_group",
    ("microsoft.network", "publicipaddresses"):     "azure_public_ip",
    ("microsoft.keyvault", "vaults"):               "azure_key_vault",
    ("microsoft.sql", "servers"):                   "azure_sql_server",
    ("microsoft.dbforpostgresql", "servers"):       "azure_postgresql_server",
    ("microsoft.web", "sites"):                     "azure_app_service",
}


def parse_azure_id(resource_id: str | None) -> dict | None:
    """Return {kind, natural_key, display_name, attributes} or None."""
    if not resource_id or not isinstance(resource_id, str):
        return None
    m = _ARM_RE.match(resource_id.strip())
    if not m:
        return None
    kind = _KIND_MAP.get((m.group("ns").lower(), m.group("type").lower()))
    if kind is None:
        return None
    return {
        "kind":         kind,
        "natural_key":  resource_id,
        "display_name": m.group("name"),
        "attributes": {
            "service":        "azure",
            "namespace":      m.group("ns"),
            "resource_type":  m.group("type"),
            "subscription":   m.group("sub"),
            "resource_group": m.group("rg"),
        },
    }
