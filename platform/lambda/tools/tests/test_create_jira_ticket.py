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


def test_missing_required_arg():
    with pytest.raises(KeyError):
        handle({"summary": "missing project_key"}, {"sub": "x"})
