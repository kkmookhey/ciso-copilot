from __future__ import annotations
import base64
import hashlib

from unittest.mock import MagicMock


def test_challenge_is_sha256_of_verifier():
    from mcp_oauth.pkce import generate_pair

    verifier, challenge = generate_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected
    assert len(verifier) >= 43  # RFC 7636 minimum


def test_store_and_fetch(monkeypatch):
    from mcp_oauth import pkce as p

    table = MagicMock()
    table.delete_item.return_value = {
        "Attributes": {"nonce": "n-1", "verifier": "v-1"},
    }
    monkeypatch.setattr(p, "_table", lambda: table)

    p.store_verifier(nonce="n-1", verifier="v-1")
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["nonce"] == "n-1"
    assert item["verifier"] == "v-1"
    assert "ttl" in item

    # fetch_verifier must atomically delete the item to prevent replay.
    assert p.fetch_verifier("n-1") == "v-1"
    table.delete_item.assert_called_once()
    assert table.delete_item.call_args.kwargs["ReturnValues"] == "ALL_OLD"


def test_fetch_returns_none_when_already_consumed(monkeypatch):
    """Second fetch for the same nonce — DDB returns no Attributes because
    the first fetch deleted the item. Prevents PKCE-verifier replay."""
    from mcp_oauth import pkce as p

    table = MagicMock()
    # No Attributes key in the response — the item was already gone.
    table.delete_item.return_value = {}
    monkeypatch.setattr(p, "_table", lambda: table)

    assert p.fetch_verifier("already-used") is None
