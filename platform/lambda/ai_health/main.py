"""Stub /v1/ai/_health endpoint — proves CisoCopilotAi → API Gateway wiring.

Replace with the first real Sub-slice 1.4 route once the stack is verified
in prod. See docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md §3.
"""
import json


def handler(event, _context):
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"ok": True, "stack": "CisoCopilotAi"}),
    }
