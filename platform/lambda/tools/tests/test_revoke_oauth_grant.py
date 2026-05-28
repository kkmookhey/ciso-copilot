# platform/lambda/tools/tests/test_revoke_oauth_grant.py
import pytest
from unittest.mock import patch
from tools.revoke_oauth_grant import handle


@patch("tools.revoke_oauth_grant._graph_delete")
@patch("tools.revoke_oauth_grant._find_grant_id")
def test_revokes_successfully(mock_find, mock_delete):
    mock_find.return_value = "grant-id-123"
    mock_delete.return_value = None

    result = handle(
        {"user_object_id": "user-abc", "app_id": "app-xyz"},
        {"sub": "test-user", "email": "kk@x.io"},
    )

    mock_find.assert_called_once_with(user_object_id="user-abc", app_id="app-xyz")
    mock_delete.assert_called_once_with("grant-id-123")
    assert result["revoked"] is True
    assert "speakable" in result
    assert "revoked" in result["speakable"].lower()


@patch("tools.revoke_oauth_grant._find_grant_id")
def test_no_grant_found(mock_find):
    mock_find.return_value = None
    result = handle(
        {"user_object_id": "user-abc", "app_id": "app-xyz"},
        {"sub": "test-user"},
    )
    assert result["revoked"] is False
    assert result["reason"] == "no_grant_found"


def test_missing_args_raises():
    with pytest.raises(KeyError):
        handle({}, {"sub": "test-user"})
