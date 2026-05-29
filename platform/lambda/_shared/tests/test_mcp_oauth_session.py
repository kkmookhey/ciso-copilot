from __future__ import annotations
import datetime as dt
from unittest.mock import MagicMock, patch
import pytest


def _now():
    return dt.datetime.now(dt.timezone.utc)


def test_lookup_user_connector_returns_row(monkeypatch):
    from mcp_oauth.session import lookup_user_connector

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = {
        "conn_id": "c-1", "access_token_enc": b"enc-access",
        "refresh_token_enc": b"enc-refresh",
        "access_expires_at": _now() + dt.timedelta(hours=2),
        "mcp_server_url": "https://mcp.slack.com/mcp",
    }
    monkeypatch.setattr("mcp_oauth.session._db", lambda: fake_db)

    row = lookup_user_connector(tenant_id="t", user_id="u", kind="slack")
    assert row["conn_id"] == "c-1"
    fake_db.execute.assert_called_once()


def test_lookup_missing_raises(monkeypatch):
    from mcp_oauth.session import lookup_user_connector, ConnectorMissingError

    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.return_value = None
    monkeypatch.setattr("mcp_oauth.session._db", lambda: fake_db)

    with pytest.raises(ConnectorMissingError):
        lookup_user_connector(tenant_id="t", user_id="u", kind="slack")


def test_refresh_if_near_expiry(monkeypatch):
    from mcp_oauth import session as sess

    near_expiry = _now() + dt.timedelta(seconds=10)  # < 60s threshold
    fresh_row = {
        "conn_id": "c-1",
        "access_token_enc": b"e1",
        "refresh_token_enc": b"e2",
        "access_expires_at": near_expiry,
        "mcp_server_url": "https://mcp.slack.com/mcp",
    }
    monkeypatch.setattr(sess, "decrypt_token",
                         lambda b: "old-access" if b == b"e1" else "old-refresh")
    monkeypatch.setattr(sess, "encrypt_token", lambda t: ("E:" + t).encode())
    monkeypatch.setattr(sess, "_provider_refresh", lambda kind, refresh: {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 43200,
    })

    # Sequence: advisory lock (no rows) → re-read row → UPDATE (no rows).
    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.side_effect = [
        None,                       # SELECT pg_advisory_xact_lock returns no rows
        {                            # re-read returns row still near expiry
            "conn_id": "c-1",
            "access_token_enc": b"e1",
            "refresh_token_enc": b"e2",
            "access_expires_at": near_expiry,
        },
        None,                       # UPDATE returns no rows
    ]
    monkeypatch.setattr(sess, "_db", lambda: fake_db)

    new_access = sess.refresh_if_near_expiry(
        fresh_row, kind="slack", tenant_id="t", user_id="u",
    )
    assert new_access == "new-access"
    sqls = [c.args[0] for c in fake_db.execute.call_args_list]
    assert any("pg_advisory_xact_lock" in s for s in sqls)
    assert any("UPDATE user_connectors" in s for s in sqls)


def test_refresh_when_access_expires_at_is_null(monkeypatch):
    """NULL expiry — spec §6 NULL-safe predicate. Must trigger refresh,
    not blow up on None arithmetic."""
    from mcp_oauth import session as sess

    null_row = {
        "conn_id": "c-2",
        "access_token_enc": b"e1",
        "refresh_token_enc": b"e2",
        "access_expires_at": None,
        "mcp_server_url": "https://mcp.slack.com/mcp",
    }
    monkeypatch.setattr(sess, "decrypt_token",
                         lambda b: "x-access" if b == b"e1" else "x-refresh")
    monkeypatch.setattr(sess, "encrypt_token", lambda t: ("E:" + t).encode())
    monkeypatch.setattr(sess, "_provider_refresh", lambda kind, refresh: {
        "access_token": "new", "refresh_token": "nr", "expires_in": 43200,
    })
    fake_db = MagicMock()
    fake_db.execute.return_value.fetchone.side_effect = [
        None,
        {"access_token_enc": b"e1", "refresh_token_enc": b"e2",
         "access_expires_at": None, "conn_id": "c-2"},
        None,
    ]
    monkeypatch.setattr(sess, "_db", lambda: fake_db)

    new_access = sess.refresh_if_near_expiry(
        null_row, kind="slack", tenant_id="t", user_id="u",
    )
    assert new_access == "new"
