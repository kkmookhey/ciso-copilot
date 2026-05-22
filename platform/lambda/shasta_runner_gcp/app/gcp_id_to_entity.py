"""Parse a GCP resource identifier into an entity-emission shape — the
GCP analog of arn_to_entity.parse_arn / azure_id_to_entity.parse_azure_id.

Handles the two standard GCP identifier forms:
  - selfLink URLs:  https://www.googleapis.com/<svc>/<ver>/projects/<p>/.../<collection>/<name>
  - full resource names:  //<svc>.googleapis.com/projects/<p>/.../<collection>/<name>

Strategy: tokenise the path, find the last known `<collection>` token,
take the token after it as the resource name. Returns
{kind, natural_key, display_name, attributes} for collections in
_KIND_MAP, or None otherwise — the caller keeps the finding and emits no
entity (same contract as parse_arn).

NOTE: the exact resource_id strings Shasta GCP modules emit should be
confirmed against a live scan (see Task 11). _KIND_MAP covers the
standard GCP forms; extend it if a live scan surfaces others.
"""
from __future__ import annotations

# path collection token -> entity kind.
_KIND_MAP = {
    "instances":      "gcp_compute_instance",
    "buckets":        "gcp_storage_bucket",
    "networks":       "gcp_vpc_network",
    "subnetworks":    "gcp_subnetwork",
    "firewalls":      "gcp_firewall",
    "clusters":       "gcp_gke_cluster",
    "serviceAccounts": "gcp_service_account",
    "keyRings":       "gcp_kms_keyring",
    "services":       "gcp_cloud_run_service",
}


def parse_gcp_id(resource_id: str | None) -> dict | None:
    """Return {kind, natural_key, display_name, attributes} or None."""
    if not resource_id or not isinstance(resource_id, str):
        return None
    raw = resource_id.strip()
    # Strip the scheme / leading marker, keep the path.
    path = raw
    for prefix in ("https://", "http://", "//"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    else:
        return None  # not a selfLink or full resource name

    tokens = [t for t in path.split("/") if t]
    project = None
    if "projects" in tokens:
        idx = tokens.index("projects")
        if idx + 1 < len(tokens):
            project = tokens[idx + 1]

    # Find the last collection token that we recognise.
    for i in range(len(tokens) - 2, -1, -1):
        kind = _KIND_MAP.get(tokens[i])
        if kind is not None:
            name = tokens[i + 1]
            return {
                "kind":         kind,
                "natural_key":  raw,
                "display_name": name,
                "attributes": {
                    "service":    "gcp",
                    "project":    project,
                    "collection": tokens[i],
                },
            }
    return None
