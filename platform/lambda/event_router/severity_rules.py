"""Deterministic severity rule table for drift events.

Each rule = (action, predicate(after) → severity). First match wins.
Actions not in the table default to 'low' (drift on uninteresting resources).
Actions in the table whose predicates don't match default to 'medium' (the
action is interesting but the specific change isn't load-bearing).
"""
from __future__ import annotations
from typing import Callable


# === Predicates over the `after_state` JSON ===

def _ipranges_include_world(after: dict) -> bool:
    for perm in after.get("ipPermissions", []) or []:
        for r in perm.get("ipRanges", []) or []:
            if r.get("cidrIp") in ("0.0.0.0/0", "::/0"):
                return True
    return False


def _has_db_port(after: dict) -> bool:
    DB_PORTS = {1433, 1521, 3306, 5432, 5984, 6379, 9200, 27017}
    for perm in after.get("ipPermissions", []) or []:
        fp, tp = perm.get("fromPort", 0), perm.get("toPort", -1)
        # AWS uses -1/-1 for "all ports" — that covers every DB port too.
        if fp == -1 and tp == -1:
            return True
        if tp < fp:
            continue
        for p in range(fp, tp + 1):
            if p in DB_PORTS:
                return True
    return False


def _is_root_login(after: dict) -> bool:
    return ((after.get("userIdentity") or {}).get("type") == "Root")


def _attaches_admin_policy(after: dict) -> bool:
    arn = after.get("policyArn", "")
    return arn.endswith("/AdministratorAccess") or arn.endswith("/PowerUserAccess")


def _bucket_public_grant(after: dict) -> bool:
    grants = ((after.get("accessControlPolicy") or {}).get("grants")) or []
    for g in grants:
        uri = (g.get("grantee") or {}).get("uri", "")
        if "AllUsers" in uri or "AuthenticatedUsers" in uri:
            return True
    return False


# === Rule table — order matters (first match wins within an action) ===

_RULES: dict[str, list[tuple[Callable[[dict], bool], str]]] = {
    "AuthorizeSecurityGroupIngress": [
        (lambda a: _ipranges_include_world(a) and _has_db_port(a), "critical"),
        (_ipranges_include_world,                                  "high"),
    ],
    "AuthorizeSecurityGroupEgress": [
        (_ipranges_include_world, "high"),
    ],
    "DeactivateMFADevice":    [(lambda a: True, "critical")],
    "DeleteVirtualMFADevice": [(lambda a: True, "critical")],
    "ConsoleLogin":           [(_is_root_login, "critical")],
    "CreateLoginProfile":     [(lambda a: True, "high")],
    "UpdateLoginProfile":     [(lambda a: True, "high")],
    "CreateAccessKey":        [(lambda a: True, "high")],
    "AttachUserPolicy":       [(_attaches_admin_policy, "high")],
    "AttachRolePolicy":       [(_attaches_admin_policy, "high")],
    "PutUserPolicy":          [(lambda a: True, "medium")],
    "PutRolePolicy":          [(lambda a: True, "medium")],
    "PutBucketAcl":           [(_bucket_public_grant, "high")],
    "PutBucketPolicy":        [(lambda a: True, "medium")],
    "DeletePublicAccessBlock":[(lambda a: True, "high")],
    "DisableKey":             [(lambda a: True, "high")],
    "ScheduleKeyDeletion":    [(lambda a: True, "high")],
}


def drift_severity(*, action: str, after: dict) -> str:
    """Look up severity for a drift action. See module docstring for fallback semantics."""
    rules = _RULES.get(action)
    if rules is None:
        return "low"
    for predicate, sev in rules:
        if predicate(after):
            return sev
    return "medium"
