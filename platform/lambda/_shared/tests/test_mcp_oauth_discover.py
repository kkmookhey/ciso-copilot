from __future__ import annotations
import time
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_discover_caches_per_workspace(monkeypatch):
    """Verify the 5-min cache: identical signature → second call hits
    cache, no second MCP round-trip."""
    import contextlib
    from mcp_oauth.session import _discover_tools_for_user, _tool_cache

    _tool_cache.clear()
    fake_session = AsyncMock()
    fake_session.list_tools.return_value = MagicMock(
        tools=[MagicMock(name="send_message")],
    )

    @contextlib.asynccontextmanager
    async def fake_open(*args, **kwargs):
        yield fake_session

    monkeypatch.setattr("mcp_oauth.session._open_session_for_user", fake_open)
    monkeypatch.setattr("mcp_oauth.session._cache_signature",
                        lambda row: "T0123:hash-x")

    tools1 = await _discover_tools_for_user("u-1", kind="slack",
                                              tenant_id="t", row={"x": 1})
    tools2 = await _discover_tools_for_user("u-1", kind="slack",
                                              tenant_id="t", row={"x": 1})
    assert tools1 == tools2
    assert fake_session.list_tools.call_count == 1
