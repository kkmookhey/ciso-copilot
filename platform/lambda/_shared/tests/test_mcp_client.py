# platform/lambda/_shared/tests/test_mcp_client.py
import os
import pytest
from unittest.mock import MagicMock, patch

from _shared.mcp_client import (
    MCPClient,
    ToolRegistryEntry,
    _extract_result,
    _server_params_from_env,
)


class TestToolRegistry:
    def test_register_and_resolve(self):
        client = MCPClient()
        client.register("slack_dm", ToolRegistryEntry(
            server="slack",
            tool="postMessage",
            args_mapping=lambda args: {"channel": args["user_lookup"], "text": args["message"]},
        ))
        entry = client.resolve("slack_dm")
        assert entry.server == "slack"
        assert entry.tool == "postMessage"

    def test_resolve_unknown_raises(self):
        client = MCPClient()
        with pytest.raises(KeyError):
            client.resolve("unknown_tool")


class TestCall:
    @patch("_shared.mcp_client._invoke_mcp_tool")
    def test_call_maps_args_and_invokes(self, mock_invoke):
        mock_invoke.return_value = {"ts": "1234.5678", "channel": "C123"}

        client = MCPClient()
        client.register("slack_dm", ToolRegistryEntry(
            server="slack",
            tool="postMessage",
            args_mapping=lambda args: {"channel": args["user_lookup"], "text": args["message"]},
        ))

        result = client.call("slack_dm", {
            "user_lookup": "sarah.chen@acme.io",
            "message": "Heads up",
        })

        # Args were mapped through args_mapping.
        mock_invoke.assert_called_once_with(
            server="slack",
            tool="postMessage",
            args={"channel": "sarah.chen@acme.io", "text": "Heads up"},
        )
        assert result == {"ts": "1234.5678", "channel": "C123"}


class TestExtractResult:
    def test_empty_content_returns_empty_dict(self):
        result = MagicMock(content=[])
        assert _extract_result(result) == {}

    def test_non_text_first_element_returns_empty_dict(self):
        # Simulates an ImageContent (no .text attribute).
        first = MagicMock(spec=[])  # no attributes; getattr falls back to None
        result = MagicMock(content=[first])
        assert _extract_result(result) == {}

    def test_valid_json_text_parses(self):
        first = MagicMock()
        first.text = '{"ts": "1.2", "channel": "C1"}'
        result = MagicMock(content=[first])
        assert _extract_result(result) == {"ts": "1.2", "channel": "C1"}

    def test_invalid_json_text_returns_raw(self):
        first = MagicMock()
        first.text = "not json at all"
        result = MagicMock(content=[first])
        assert _extract_result(result) == {"raw": "not json at all"}


class TestServerParamsFromEnv:
    def test_missing_command_raises(self, monkeypatch):
        monkeypatch.delenv("MCP_SLACK_COMMAND", raising=False)
        with pytest.raises(RuntimeError, match="MCP_SLACK_COMMAND not set"):
            _server_params_from_env("slack")

    def test_constructs_params_from_command(self, monkeypatch):
        monkeypatch.setenv("MCP_SLACK_COMMAND", "npx -y @modelcontextprotocol/server-slack")
        monkeypatch.delenv("MCP_SLACK_FORWARD_ENV", raising=False)
        params = _server_params_from_env("slack")
        assert params.command == "npx"
        assert params.args == ["-y", "@modelcontextprotocol/server-slack"]
        # env defaults to None when no FORWARD_ENV is set.
        assert params.env is None

    def test_forwards_listed_env_vars(self, monkeypatch):
        monkeypatch.setenv("MCP_SLACK_COMMAND", "npx -y server-slack")
        monkeypatch.setenv("MCP_SLACK_FORWARD_ENV", "SLACK_BOT_TOKEN")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
        params = _server_params_from_env("slack")
        assert params.env == {"SLACK_BOT_TOKEN": "xoxb-secret"}

    def test_missing_forward_token_raises(self, monkeypatch):
        monkeypatch.setenv("MCP_SLACK_COMMAND", "npx -y server-slack")
        monkeypatch.setenv("MCP_SLACK_FORWARD_ENV", "SLACK_BOT_TOKEN")
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="SLACK_BOT_TOKEN"):
            _server_params_from_env("slack")
