# platform/lambda/chat_session/tests/test_messages_update.py
"""Tests for messages.update_content."""
import json
from unittest.mock import patch

import messages as M


# ---------------------------------------------------------------------------
# update_content — success path
# ---------------------------------------------------------------------------

def test_update_content_returns_true_when_row_updated(monkeypatch):
    """update_content returns True when the DB finds and updates the row."""
    calls = []

    def mock_q(sql, params=None):
        calls.append((sql, params))
        # Simulate RETURNING id::text with one record
        return [[{"stringValue": "some-message-id"}]]

    monkeypatch.setattr(M, "_q", mock_q)

    result = M.update_content(
        conversation_id="cid-1",
        message_id="mid-1",
        content={"kind": "approval_card", "current_status": "approved"},
    )
    assert result is True
    assert len(calls) == 1
    sql, params = calls[0]
    assert "UPDATE conversation_messages" in sql
    assert "content" in sql
    assert params["mid"] == "mid-1"
    assert params["cid"] == "cid-1"
    # content param is JSON-serialized
    content_val = json.loads(params["content"])
    assert content_val["current_status"] == "approved"


# ---------------------------------------------------------------------------
# update_content — not found path
# ---------------------------------------------------------------------------

def test_update_content_returns_false_when_no_row(monkeypatch):
    """update_content returns False when no row matches (wrong mid or cid)."""
    def mock_q(sql, params=None):
        return []  # empty RETURNING → no match

    monkeypatch.setattr(M, "_q", mock_q)

    result = M.update_content(
        conversation_id="cid-999",
        message_id="mid-999",
        content={"current_status": "approved"},
    )
    assert result is False
