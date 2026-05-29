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
    table.get_item.return_value = {"Item": {"nonce": "n-1", "verifier": "v-1"}}
    monkeypatch.setattr(p, "_table", lambda: table)

    p.store_verifier(nonce="n-1", verifier="v-1")
    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["nonce"] == "n-1"
    assert item["verifier"] == "v-1"
    assert "ttl" in item

    assert p.fetch_verifier("n-1") == "v-1"
