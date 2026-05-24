"""AI sign-in pass for the Entra runner — Slice 2 of AI Visibility v2.

Reads Microsoft Graph audit-log sign-in events, matches each event
against a curated AI-SaaS catalog, and emits finding-shaped param dicts
ready for the existing _insert_findings batch path in main.py.

Pure helpers (load_catalog, match_app, signin_to_params) are unit-tested
against fixture dicts. run_ai_signin_pass is the orchestrator; it imports
the Graph SDK lazily so this module stays importable in test environments
without the SDK installed.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_DEFAULT_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "ai_saas_catalog.json")

# The Microsoft Graph error code returned from /auditLogs/signIns when the
# tenant is on Entra Free tier. Triggers the S2.1 banner via
# cloud_connections.scope.signin_premium_required.
_LICENSING_ERROR_CODE = "Authentication_RequestFromNonPremiumTenantOrB2CTenant"

_CHECK_BY_TIER = {
    "personal": "ai_signin_personal_tier",
    "corp":     "ai_signin_corp_tier",
    "unknown":  "ai_signin_unknown_tier",
}

# Status policy: corp tier passes (sanctioned), others fail (actionable).
_STATUS_BY_TIER = {
    "personal": "fail",
    "corp":     "pass",
    "unknown":  "fail",
}


def load_catalog(path: str = _DEFAULT_CATALOG_PATH) -> dict:
    """Read + parse the AI-SaaS catalog JSON."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def match_app(event: dict, catalog: dict) -> tuple[str | None, str | None, str | None]:
    """Match one sign-in event against the catalog.

    Returns (canonical_name, tier, default_severity) on hit, or
    (None, None, None) on miss. Tier is one of 'personal', 'corp',
    'unknown'.
    """
    app_display = (event.get("appDisplayName") or "").strip()
    app_id      = (event.get("appId") or "").strip()
    app_display_lc = app_display.lower()

    for canonical, entry in catalog.items():
        match = entry.get("match", {})
        names = [n.lower() for n in match.get("app_display_names", [])]
        ids   = match.get("app_ids", [])
        hit = False
        if app_id and app_id in ids:
            hit = True
        elif app_display_lc:
            # Match if any catalog name appears as a substring of the
            # event's display name (case-insensitive). This catches
            # "ChatGPT Enterprise" against catalog name "ChatGPT" while
            # leaving the tier-inference rules to disambiguate tier.
            for n in names:
                if n and n in app_display_lc:
                    hit = True
                    break
        if not hit:
            continue

        # Determine tier.
        tier = "unknown"
        rules = entry.get("tier_inference") or {}
        for keyword, mapped_tier in rules.items():
            if keyword.lower() in app_display_lc:
                tier = mapped_tier
                break

        return canonical, tier, entry.get("default_severity", "medium")

    return None, None, None


def signin_to_params(
    event: dict, *,
    name: str, tier: str, catalog_severity: str,
    tenant_id: str, conn_id: str, scan_id: str, entra_tenant_id: str,
) -> list[dict]:
    """Build the param-list for one finding INSERT, ready for
    _insert_findings in main.py.

    Corp tier downgrades severity to 'low' and status to 'pass' (the
    app is sanctioned). Personal + unknown emit 'fail' at the catalog
    severity. evidence_packet carries `entra_upn` + `is_ai='true'` so
    the /ai per-person query + is_ai_touching predicate pick the
    finding up.
    """
    check_id = _CHECK_BY_TIER[tier]
    status   = _STATUS_BY_TIER[tier]
    severity = "low" if tier == "corp" else catalog_severity

    upn = event.get("userPrincipalName", "") or ""
    created = event.get("createdDateTime", "")

    title = f"{upn or 'unknown user'} signed into {name}"[:500]
    description = (
        f"User {upn or '(unknown)'} authenticated to {name} "
        f"at {created or 'unknown time'}. Tier: {tier}."
    )[:2000]
    remediation = (
        "Review whether the user has access to a corporate-tier instance "
        "of this AI tool with proper data-handling controls."
        if tier == "personal" else
        "Confirm via your AI usage policy whether this sign-in is sanctioned."
    )[:2000]

    evidence_packet = {
        "entra_upn":         upn,
        "is_ai":             "true",
        "app":               name,
        "tier":              tier,
        "signin_id":         event.get("id", ""),
        "created_at":        created,
        "app_display_name":  event.get("appDisplayName", ""),
        "app_id":            event.get("appId", ""),
    }

    return [
        {"name": "fid",             "value": {"stringValue": str(uuid.uuid4())}},
        {"name": "tid",             "value": {"stringValue": tenant_id}},
        {"name": "cid",             "value": {"stringValue": conn_id}},
        {"name": "sid",             "value": {"stringValue": scan_id}},
        {"name": "check_id",        "value": {"stringValue": check_id}},
        {"name": "title",           "value": {"stringValue": title}},
        {"name": "description",     "value": {"stringValue": description}},
        {"name": "severity",        "value": {"stringValue": severity}},
        {"name": "status",          "value": {"stringValue": status}},
        {"name": "resource_arn",    "value": {"stringValue": ""}},
        {"name": "resource_type",   "value": {"stringValue": "ai_signin"}},
        {"name": "region",          "value": {"stringValue": entra_tenant_id[:50]}},
        {"name": "domain",          "value": {"stringValue": "identity"}},
        {"name": "frameworks",      "value": {"stringValue": "{}"}},
        {"name": "remediation",     "value": {"stringValue": remediation}},
        {"name": "evidence_packet", "value": {"stringValue": json.dumps(evidence_packet)}},
    ]


def run_ai_signin_pass(
    graph_client: Any, *,
    tenant_id: str, conn_id: str, scan_id: str, entra_tenant_id: str,
    last_scan_at: str | None = None,
    catalog_path: str | None = None,
) -> tuple[list[list[dict]], bool]:
    """Page through Graph audit logs, match against catalog, return a
    tuple of (param_lists, premium_required) ready for the caller.

    premium_required is True iff Microsoft returned 403
    Authentication_RequestFromNonPremiumTenantOrB2CTenant — the S2.1
    banner trigger. Other failures leave premium_required=False so the
    banner doesn't fire for unrelated problems.

    Graph SDK is imported lazily so this module stays importable in test
    environments without the SDK installed.
    """
    if graph_client is None:
        from azure.identity import DefaultAzureCredential       # type: ignore
        from msgraph import GraphServiceClient                  # type: ignore
        credential = DefaultAzureCredential()
        graph_client = GraphServiceClient(
            credentials=credential,
            scopes=["https://graph.microsoft.com/.default"],
        )

    catalog = load_catalog(catalog_path or _DEFAULT_CATALOG_PATH)
    events, premium_required = _fetch_signins(graph_client, last_scan_at=last_scan_at)

    out: list[list[dict]] = []
    for event in events:
        name, tier, sev = match_app(event, catalog)
        if name is None:
            continue
        params = signin_to_params(
            event, name=name, tier=tier, catalog_severity=sev,
            tenant_id=tenant_id, conn_id=conn_id, scan_id=scan_id,
            entra_tenant_id=entra_tenant_id,
        )
        out.append(params)

    return out, premium_required


def _fetch_signins(graph_client: Any, *, last_scan_at: str | None) -> tuple[list[dict], bool]:
    """Page through `/auditLogs/signIns`. Returns (events, premium_required).

    premium_required is True only when Microsoft returns 403 with error code
    Authentication_RequestFromNonPremiumTenantOrB2CTenant. All other failure
    modes (auth, scope, network, server) leave premium_required=False so the
    S2.1 banner stays off for non-licensing problems.

    Incremental by `createdDateTime ge last_scan_at` when provided.
    """
    # Build the request configuration if the SDK is importable. In test
    # environments without msgraph/kiota installed we fall back to
    # request_configuration=None so the unit test can still exercise the
    # error-handling path with a fake graph_client.
    cfg: Any = None
    try:
        from kiota_abstractions.base_request_configuration import RequestConfiguration  # type: ignore
        from msgraph.generated.audit_logs.sign_ins.sign_ins_request_builder import SignInsRequestBuilder  # type: ignore

        query_params = SignInsRequestBuilder.SignInsRequestBuilderGetQueryParameters(
            top=1000,
        )
        if last_scan_at:
            query_params.filter = f"createdDateTime ge {last_scan_at}"
        cfg = RequestConfiguration(query_parameters=query_params)
    except ImportError:
        cfg = None

    # The SDK's pagination iterator is async; for v1 we collect a single
    # page synchronously via the underlying request. Production-quality
    # paging across many pages is a follow-on.
    try:
        page = graph_client.audit_logs.sign_ins.get(request_configuration=cfg)
        page = _maybe_await(page)
        if page is None or not getattr(page, "value", None):
            return [], False
        return [_event_to_dict(e) for e in page.value], False
    except Exception as e:
        err_obj = getattr(e, "error", None)
        err_code = getattr(err_obj, "code", None) if err_obj is not None else None
        premium_required = (err_code == _LICENSING_ERROR_CODE)
        if premium_required:
            logger.warning("ai_signin_pass: Graph returned licensing-403 (Entra Free tier)")
        else:
            logger.warning("ai_signin_pass: Graph fetch failed: %s", e)
        return [], premium_required


def _maybe_await(coro: Any) -> Any:
    """The Graph SDK returns coroutines from sync-looking calls.
    Run-to-completion inside a fresh event loop if needed."""
    import asyncio
    import inspect
    if inspect.iscoroutine(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return coro


def _event_to_dict(event: Any) -> dict:
    """Normalize a Graph SignIn object to a plain dict for matching."""
    return {
        "id":                 getattr(event, "id", None),
        "appDisplayName":     getattr(event, "app_display_name", None),
        "appId":              getattr(event, "app_id", None),
        "userPrincipalName":  getattr(event, "user_principal_name", None),
        "createdDateTime":    (getattr(event, "created_date_time", None) or ""),
    }
