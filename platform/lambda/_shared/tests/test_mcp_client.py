# platform/lambda/_shared/tests/test_mcp_client.py
import pytest
from unittest.mock import MagicMock, patch

from _shared.mcp_client import MCPClient, ToolRegistryEntry


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
