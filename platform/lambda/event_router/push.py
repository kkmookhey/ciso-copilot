"""Push rule evaluation + SNS Mobile Push call."""
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
    """Create per-device endpoints lazily and publish via SNS Mobile Push."""
    payload = {"aps": {"alert": body, "sound": "default"}}
    for token in device_tokens:
        ep = sns.create_platform_endpoint(
            PlatformApplicationArn=platform_app_arn,
            Token=token,
        )
        sns.publish(
            TargetArn=ep["EndpointArn"],
            Message=json.dumps({"APNS_SANDBOX": json.dumps(payload)}),
            MessageStructure="json",
        )
