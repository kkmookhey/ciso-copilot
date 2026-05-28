# platform/lambda/tools/tests/test_create_jira_ticket.py
import pytest
from unittest.mock import patch
from tools.create_jira_ticket import handle


@patch("tools.create_jira_ticket._mcp_client")
def test_creates_ticket(mock_client):
    mock_client.call.return_value = {
        "key": "ITSEC-3091",
        "self": "https://transilience.atlassian.net/rest/api/2/issue/12345",
    }
    result = handle({
        "project_key":      "ITSEC",
        "summary":          "Provision Sarah on ChatGPT Enterprise",
        "description":      "Personal-tier ChatGPT use detected.",
        "assignee_lookup":  "priya@transilience.ai",
    }, {"sub": "test-user"})
    assert result["key"] == "ITSEC-3091"
    assert "url" in result
    assert "speakable" in result
    assert "ITSEC-3091" in result["speakable"]


@patch("tools.create_jira_ticket._mcp_client")
def test_creates_ticket_wrapped_issue_shape(mock_client):
    # The live mcp-atlassian server wraps the created issue under
    # result["issue"]; verify we handle that shape.
    mock_client.call.return_value = {
        "message": "Issue created successfully",
        "issue": {
            "id":   "10002",
            "key":  "KAN-3",
            "url":  "https://shastabot.atlassian.net/rest/api/2/issue/10002",
            "project": {"key": "KAN", "name": "Bug Tracking"},
        },
    }
    result = handle({
        "project_key": "KAN",
        "summary":     "Wrapped shape test",
    }, {"sub": "test-user"})
    assert result["created"] is True
    assert result["key"] == "KAN-3"
    assert "KAN-3" in result["speakable"]


def test_missing_required_arg():
    with pytest.raises(KeyError):
        handle({"summary": "missing project_key"}, {"sub": "x"})
