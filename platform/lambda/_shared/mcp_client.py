# platform/lambda/_shared/mcp_client.py
"""Python MCP client wrapper.

Shasta talks to existing upstream MCP servers (Anthropic-reference Slack,
Atlassian official, GitHub reference). This module hides the async stdio
plumbing behind a synchronous call(tool_name, args) that Lambda handlers
can use directly.

Configuration: per-server transport (stdio command OR http URL) is read
from environment variables at module import time. See README in
platform/lambda/tools/ for the env-var contract.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@dataclass
class ToolRegistryEntry:
    server: str                                      # logical server name
    tool: str                                        # tool name on the server
    args_mapping: Callable[[dict], dict]             # Shasta args -> MCP args


class MCPClient:
    def __init__(self) -> None:
        self._registry: dict[str, ToolRegistryEntry] = {}

    def register(self, shasta_tool_name: str, entry: ToolRegistryEntry) -> None:
        self._registry[shasta_tool_name] = entry

    def resolve(self, shasta_tool_name: str) -> ToolRegistryEntry:
        if shasta_tool_name not in self._registry:
            raise KeyError(f"Unknown MCP-mediated tool: {shasta_tool_name}")
        return self._registry[shasta_tool_name]

    def call(self, shasta_tool_name: str, args: dict) -> dict:
        entry = self.resolve(shasta_tool_name)
        mcp_args = entry.args_mapping(args)
        return _invoke_mcp_tool(server=entry.server, tool=entry.tool, args=mcp_args)


# Module-level cache: one ClientSession per server (created lazily, reused
# across Lambda invocations within the same container).
_sessions: dict[str, ClientSession] = {}


def _invoke_mcp_tool(*, server: str, tool: str, args: dict) -> dict:
    """Synchronous bridge to async MCP. Spins up an asyncio loop per call —
    Lambda invocations are single-threaded so this is safe."""
    return asyncio.run(_async_invoke(server=server, tool=tool, args=args))


async def _async_invoke(*, server: str, tool: str, args: dict) -> dict:
    params = _server_params_from_env(server)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            # mcp returns a CallToolResult with .content (list of TextContent
            # or ImageContent). For tool calls returning JSON, the first
            # TextContent's .text is the JSON-encoded result.
            return _extract_result(result)


def _extract_result(result) -> dict:
    """Pull a JSON dict out of an MCP CallToolResult."""
    if not result.content:
        return {}
    first = result.content[0]
    text = getattr(first, "text", None)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _server_params_from_env(server: str) -> StdioServerParameters:
    """Read transport config from env. Convention:
       MCP_SLACK_COMMAND='npx -y @modelcontextprotocol/server-slack'
       MCP_SLACK_TOKEN_ENV='SLACK_BOT_TOKEN'  (env var name to forward)
    """
    cmd_env = f"MCP_{server.upper()}_COMMAND"
    cmd = os.environ.get(cmd_env)
    if not cmd:
        raise RuntimeError(f"{cmd_env} not set — cannot reach MCP server '{server}'")
    parts = cmd.split()
    # Forward any tokens listed in MCP_<SERVER>_FORWARD_ENV (comma-separated).
    forward = os.environ.get(f"MCP_{server.upper()}_FORWARD_ENV", "")
    forwarded_env = {k: os.environ[k] for k in forward.split(",") if k and k in os.environ}
    return StdioServerParameters(command=parts[0], args=parts[1:], env=forwarded_env or None)
