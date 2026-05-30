from __future__ import annotations
import contextlib
import datetime as dt
from unittest.mock import MagicMock, patch
import pytest


def _now():
    return dt.datetime.now(dt.timezone.utc)


class _FakeTxnDB:
    """A DB fake that captures whether each execute() ran inside a
    transaction (transactionId set) versus auto-commit. Exposed so tests
    can assert the advisory lock + re-read + UPDATE all share one txn."""

    def __init__(self, fetchone_side_effect):
        self.calls = []  # list of (sql, params, txn_id)
        self._side_effect = list(fetchone_side_effect)
        self._txn_active = False

    def execute(self, sql, params=None):
        txn_id = "T1" if self._txn_active else None
        self.calls.append((sql, params, txn_id))
        result = self._side_effect.pop(0) if self._side_effect else None
        class R:
            def fetchone(self_inner):
                return result
        return R()

    @contextlib.contextmanager
    def transaction(self):
        self._txn_active = True
        try:
            yield self
        finally:
            self._txn_active = False


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
        "access_data_key_ct": b"adk1",
        "refresh_token_enc": b"e2",
        "refresh_data_key_ct": b"rdk1",
        "access_expires_at": near_expiry,
        "mcp_server_url": "https://mcp.slack.com/mcp",
    }
    monkeypatch.setattr(sess, "decrypt_token",
                         lambda ct, dk: "old-access" if ct == b"e1" else "old-refresh")
    monkeypatch.setattr(sess, "encrypt_token",
                         lambda t: (("E:" + t).encode(), b"dk-" + t.encode()))
    monkeypatch.setattr(sess, "_provider_refresh", lambda kind, refresh: {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 43200,
    })

    # Sequence: advisory lock (no rows) → re-read row → UPDATE (no rows).
    fake_db = _FakeTxnDB([
        None,
        {
            "conn_id": "c-1",
            "access_token_enc": b"e1",
            "access_data_key_ct": b"adk1",
            "refresh_token_enc": b"e2",
            "refresh_data_key_ct": b"rdk1",
            "access_expires_at": near_expiry,
        },
        None,
    ])
    monkeypatch.setattr(sess, "_db", lambda: fake_db)

    new_access = sess.refresh_if_near_expiry(
        fresh_row, kind="slack", tenant_id="t", user_id="u",
    )
    assert new_access == "new-access"
    sqls = [c[0] for c in fake_db.calls]
    assert any("pg_advisory_xact_lock" in s for s in sqls)
    assert any("UPDATE user_connectors" in s for s in sqls)
    # B3 fix: lock + re-read + UPDATE must ALL run under the same txn,
    # otherwise pg_advisory_xact_lock is a no-op (Data API autocommit
    # releases the lock at the end of each statement).
    in_txn = [c for c in fake_db.calls if c[2] is not None]
    assert any("pg_advisory_xact_lock" in s for s, _, _ in in_txn)
    assert any("UPDATE user_connectors" in s for s, _, _ in in_txn)
    txn_ids = {c[2] for c in fake_db.calls if c[2] is not None}
    assert len(txn_ids) == 1, f"all txn statements must share one id, got {txn_ids}"


def test_refresh_when_access_expires_at_is_null(monkeypatch):
    """NULL expiry — spec §6 NULL-safe predicate. Must trigger refresh,
    not blow up on None arithmetic."""
    from mcp_oauth import session as sess

    null_row = {
        "conn_id": "c-2",
        "access_token_enc": b"e1",
        "access_data_key_ct": b"adk2",
        "refresh_token_enc": b"e2",
        "refresh_data_key_ct": b"rdk2",
        "access_expires_at": None,
        "mcp_server_url": "https://mcp.slack.com/mcp",
    }
    monkeypatch.setattr(sess, "decrypt_token",
                         lambda ct, dk: "x-access" if ct == b"e1" else "x-refresh")
    monkeypatch.setattr(sess, "encrypt_token",
                         lambda t: (("E:" + t).encode(), b"dk-" + t.encode()))
    monkeypatch.setattr(sess, "_provider_refresh", lambda kind, refresh: {
        "access_token": "new", "refresh_token": "nr", "expires_in": 43200,
    })
    fake_db = _FakeTxnDB([
        None,
        {"access_token_enc": b"e1", "access_data_key_ct": b"adk2",
         "refresh_token_enc": b"e2", "refresh_data_key_ct": b"rdk2",
         "access_expires_at": None, "conn_id": "c-2"},
        None,
    ])
    monkeypatch.setattr(sess, "_db", lambda: fake_db)

    new_access = sess.refresh_if_near_expiry(
        null_row, kind="slack", tenant_id="t", user_id="u",
    )
    assert new_access == "new"


def test_refresh_raises_connector_missing_on_concurrent_revoke(monkeypatch):
    """User clicks Disconnect between the outer lookup and the re-read under
    the advisory lock — re-read returns None. The previous code dereferenced
    None and surfaced as a generic 500. Now must raise ConnectorMissingError
    so the caller can return 409 + 'reconnect in Settings'."""
    from mcp_oauth import session as sess

    near_expiry = _now() + dt.timedelta(seconds=10)
    fresh_row = {
        "conn_id": "c-3",
        "access_token_enc": b"e1",
        "access_data_key_ct": b"adk3",
        "refresh_token_enc": b"e2",
        "refresh_data_key_ct": b"rdk3",
        "access_expires_at": near_expiry,
        "mcp_server_url": "https://mcp.slack.com/mcp",
    }
    monkeypatch.setattr(sess, "decrypt_token", lambda ct, dk: "old")

    fake_db = _FakeTxnDB([
        None,   # advisory lock SELECT
        None,   # re-read returns None — row was deleted concurrently
    ])
    monkeypatch.setattr(sess, "_db", lambda: fake_db)

    with pytest.raises(sess.ConnectorMissingError):
        sess.refresh_if_near_expiry(
            fresh_row, kind="slack", tenant_id="t", user_id="u",
        )
