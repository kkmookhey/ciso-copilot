"""Tests for mcp_oauth.crypto. The KMS client is mocked; pgp_sym_encrypt
is opaque to us — we round-trip through the actual pgcrypto-encoded form
by calling _wrap_with_envelope / _unwrap_envelope without touching pg."""
from __future__ import annotations
import os
from unittest.mock import patch

import pytest


def test_envelope_round_trips_plaintext():
    from mcp_oauth.crypto import _wrap_with_envelope, _unwrap_envelope

    fake_data_key = b"x" * 32  # AES-256 key
    plaintext = b"xoxp-real-refresh-token-bytes"
    enc = _wrap_with_envelope(plaintext, fake_data_key)
    assert enc != plaintext  # actually encrypted
    assert _unwrap_envelope(enc, fake_data_key) == plaintext


def test_kms_data_key_cached_once_per_cold_start(monkeypatch):
    from mcp_oauth import crypto as c

    c._cached_data_key = None  # reset
    calls = {"n": 0}
    def fake_generate_data_key(*, KeyId, KeySpec):
        calls["n"] += 1
        return {"Plaintext": b"y" * 32, "CiphertextBlob": b"ciphered"}

    monkeypatch.setattr(c, "_kms", type("M", (), {"generate_data_key": staticmethod(fake_generate_data_key)})())
    monkeypatch.setenv("CONNECTOR_TOKENS_KEY_ARN", "arn:aws:kms:us-east-1:x:key/abc")

    k1 = c._get_data_key()
    k2 = c._get_data_key()
    assert k1 == k2 == b"y" * 32
    assert calls["n"] == 1  # cache hit on second call
