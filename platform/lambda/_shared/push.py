"""Push rule evaluation + SNS Mobile Push call. Shared across event_router,
the AI supply chain matcher, the Entra runner (personal-tier triggers), and
the forensic-scan callback Lambda.
"""
from __future__ import annotations
import json
import boto3


sns = boto3.client("sns")

PUSH_THRESHOLD       = "high"     # severity floor for non-critical pushes
PUSH_RATE_LIMIT_HOUR = 10         # criticals always bypass

_SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def should_push(severity: str, current_hour_count: int) -> bool:
    """True if push should fire. Criticals always push; non-critical respects threshold + rate limit."""
    if severity == "critical":
        return True
    if _SEV_ORDER.get(severity, 0) < _SEV_ORDER[PUSH_THRESHOLD]:
        return False
    return current_hour_count < PUSH_RATE_LIMIT_HOUR


def format_push_body(*, kind: str, severity: str, title: str,
                     resource_arn: str | None, actor: str | None) -> str:
    """Templated one-liner. The AI narrative arrives at the GET; push is deterministic."""
    bits = [kind, severity]
    rid  = (resource_arn or "").split("/")[-1] or (resource_arn or "")
    if rid:
        bits.append(rid)
    bits.append(title)
    if actor:
        # For ARNs like arn:aws:iam::123:user/alice keep "user/alice" (last two slash segments)
        actor_parts = actor.split("/")
        actor_label = "/".join(actor_parts[-2:]) if len(actor_parts) >= 2 else actor_parts[-1]
        bits.append(f"by {actor_label}")
    return " · ".join(bits)


def send_push(*, device_tokens: list[str], platform_app_arn: str, body: str) -> None:
    """Body-only push. Legacy callers (event_router) use this."""
    send_push_with_payload(device_tokens=device_tokens,
                           platform_app_arn=platform_app_arn,
                           body=body, payload={})


def send_push_with_payload(*, device_tokens: list[str], platform_app_arn: str,
                            body: str, payload: dict) -> None:
    """Push with custom user-info payload — needed for the iOS app to deep-link
    into BriefingView with finding_id + speakable_summary."""
    aps_payload = {
        "aps": {"alert": body, "sound": "default"},
        **payload,
    }
    for token in device_tokens:
        ep = sns.create_platform_endpoint(
            PlatformApplicationArn=platform_app_arn,
            Token=token,
        )
        sns.publish(
            TargetArn=ep["EndpointArn"],
            Message=json.dumps({"APNS_SANDBOX": json.dumps(aps_payload)}),
            MessageStructure="json",
        )


def tokens_for_tenant(tenant_id: str, *, rds, db_cluster_arn: str,
                       db_secret_arn: str, db_name: str) -> list[str]:
    """APNs device tokens for all users in a tenant. Returns [] when none are
    registered (graceful no-op for push)."""
    rs = rds.execute_statement(
        resourceArn=db_cluster_arn, secretArn=db_secret_arn, database=db_name,
        sql=("SELECT device_token FROM users WHERE tenant_id = CAST(:t AS UUID) "
             "AND device_token IS NOT NULL"),
        parameters=[{"name": "t", "value": {"stringValue": tenant_id}}],
    )
    return [r[0].get("stringValue", "") for r in rs.get("records", []) if r[0].get("stringValue")]


def notify_tool_completion(*, tenant_id: str, conversation_id: str, body: str,
                            payload: dict, rds, db_cluster_arn: str,
                            db_secret_arn: str, db_name: str,
                            platform_app_arn: str) -> None:
    """Used by forensic-scan callback (and any other agent-initiated tool
    that takes long enough to background)."""
    tokens = tokens_for_tenant(tenant_id, rds=rds,
                                db_cluster_arn=db_cluster_arn,
                                db_secret_arn=db_secret_arn,
                                db_name=db_name)
    full_payload = {"conversation_id": conversation_id, **payload}
    send_push_with_payload(device_tokens=tokens,
                            platform_app_arn=platform_app_arn,
                            body=body, payload=full_payload)
