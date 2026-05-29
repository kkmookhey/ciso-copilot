# platform/lambda/voice_session/tests/test_dynamic_tools.py
"""Dynamic per-user tool registry for the OpenAI Realtime session.

voice_session.main._build_openai_tools merges Shasta-native tools with
per-vendor MCP tool manifests discovered live for the calling user. Names
are namespaced `{kind}__{tool_name}` so the tools-Lambda dispatcher knows
to route them through mcp_oauth.
"""
from __future__ import annotations
import asyncio
from unittest.mock import MagicMock


def test_dynamic_tools_built_from_discover_tools():
    from voice_session.main import _build_openai_tools

    async def fake_discover(*a, **kw):
        slack_tool = MagicMock()
        slack_tool.name = "send_message"
        slack_tool.description = "Send Slack message"
        slack_tool.inputSchema = {"type": "object", "properties": {}}
        return {"slack": [slack_tool]}

    result = asyncio.run(_build_openai_tools(
        subject="s", tenant_id="t",
        discover_fn=fake_discover,
        native_tools=[{"type": "function", "name": "run_forensic_scan"}],
    ))
    names = [t["name"] for t in result]
    assert "slack__send_message" in names
    assert "run_forensic_scan" in names


def test_dynamic_tools_falls_back_to_native_when_discover_raises():
    from voice_session.main import _build_openai_tools

    async def boom(*a, **kw):
        raise RuntimeError("aurora unreachable")

    native = [{"type": "function", "name": "get_top_risks"}]
    result = asyncio.run(_build_openai_tools(
        subject="s", tenant_id="t",
        discover_fn=boom,
        native_tools=native,
    ))
    assert result == native
