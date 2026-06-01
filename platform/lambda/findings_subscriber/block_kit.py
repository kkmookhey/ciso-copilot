"""Slack Block Kit template for the autonomous CRITICAL broadcast.

Goal: 4-6 visual lines. Channel members opted in; respect attention.
Deliberately NOT included: full evidence (sensitive), authoritative
remediation steps (canonical in platform UI), @mentions (no paging),
batched findings (one finding = one message).
"""
from __future__ import annotations
import os


def _escape(text: str | None) -> str:
    """Slack mrkdwn escape — only the three required chars per Slack's docs.
    `\\`, `_`, `*` are pass-through (legal in mrkdwn).
    """
    if text is None:
        return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


_TITLE_MAX = 150


def format_finding_card(f: dict) -> list[dict]:
    title = (f.get("title") or "")[:_TITLE_MAX]
    resource = f.get("resource_arn") or f.get("subject_ref") or "(unknown)"
    scanner = f.get("scanner") or "unknown"
    frameworks = ", ".join(f.get("frameworks_list") or []) or "—"
    created_at_epoch = int(f.get("created_at_epoch") or 0)
    web_base = os.environ["WEB_BASE_URL"]

    return [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"🚨 *CRITICAL — {_escape(title)}*"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*Resource:* `{_escape(resource)}`\n"
                    f"*Scanner:* {_escape(scanner)} · *Frameworks:* {_escape(frameworks)}\n"
                    f"*Detected:* <!date^{created_at_epoch}^"
                    f"{{date_short}} {{time}}|just now>"}},
        {"type": "actions", "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "View full details and remediation"},
            "url": f"{web_base}/risks/{f['finding_id']}",
            "style": "primary",
        }]},
    ]
