# platform/lambda/chat_session/tests/test_stream.py
import json
import time

import pytest

import messages_stream as MS


def test_verify_jwt_rejects_missing_header():
    assert MS._extract_bearer({}) is None


def test_extract_bearer_parses_header():
    assert MS._extract_bearer({"authorization": "Bearer abc.def.ghi"}) == "abc.def.ghi"


def test_extract_bearer_case_insensitive():
    assert MS._extract_bearer({"Authorization": "Bearer xyz"}) == "xyz"


# ---------------------------------------------------------------------------
# _verify_jwt tests — use a self-signed RS256 keypair to avoid hitting Cognito
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_keypair():
    """Generate a one-off RSA key and a matching fake JWKS for testing."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    from jwt.algorithms import RSAAlgorithm

    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    pub_jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    pub_jwk["kid"] = "test-kid-1"
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "RS256"
    return key, [pub_jwk]


def _make_token(key, kid, iss, extra_claims=None, exp_offset=300):
    import jwt as pyjwt
    claims = {
        "sub": "user-abc",
        "token_use": "id",
        "iss": iss,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
    }
    if extra_claims:
        claims.update(extra_claims)
    return pyjwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _patch_jwks(monkeypatch, fake_jwks, pool_id="us-east-1_TESTPOOL"):
    """Point messages_stream at our fake JWKS without touching Cognito."""
    monkeypatch.setenv("USER_POOL_ID", pool_id)
    monkeypatch.setattr(MS, "_jwks", lambda: fake_jwks)
    monkeypatch.setattr(MS, "USER_POOL_ID", pool_id)
    monkeypatch.setattr(MS, "AWS_REGION", "us-east-1")


def test_verify_jwt_valid_token(monkeypatch, rsa_keypair):
    """A correctly-signed token with the right issuer + token_use returns claims."""
    key, fake_jwks = rsa_keypair
    pool_id = "us-east-1_TESTPOOL"
    _patch_jwks(monkeypatch, fake_jwks, pool_id)
    iss = f"https://cognito-idp.us-east-1.amazonaws.com/{pool_id}"
    token = _make_token(key, "test-kid-1", iss)
    claims = MS._verify_jwt(token)
    assert claims is not None
    assert claims["sub"] == "user-abc"
    assert claims["token_use"] == "id"


def test_verify_jwt_expired_token(monkeypatch, rsa_keypair):
    """An expired token returns None."""
    key, fake_jwks = rsa_keypair
    pool_id = "us-east-1_TESTPOOL"
    _patch_jwks(monkeypatch, fake_jwks, pool_id)
    iss = f"https://cognito-idp.us-east-1.amazonaws.com/{pool_id}"
    token = _make_token(key, "test-kid-1", iss, exp_offset=-10)
    assert MS._verify_jwt(token) is None


def test_verify_jwt_wrong_key(monkeypatch, rsa_keypair):
    """A token signed by a different key returns None."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    _, fake_jwks = rsa_keypair
    pool_id = "us-east-1_TESTPOOL"
    _patch_jwks(monkeypatch, fake_jwks, pool_id)
    iss = f"https://cognito-idp.us-east-1.amazonaws.com/{pool_id}"
    other_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    token = _make_token(other_key, "test-kid-1", iss)
    assert MS._verify_jwt(token) is None


def test_verify_jwt_unknown_kid(monkeypatch, rsa_keypair):
    """A token with an unknown kid returns None."""
    key, fake_jwks = rsa_keypair
    pool_id = "us-east-1_TESTPOOL"
    _patch_jwks(monkeypatch, fake_jwks, pool_id)
    iss = f"https://cognito-idp.us-east-1.amazonaws.com/{pool_id}"
    token = _make_token(key, "unknown-kid-99", iss)
    assert MS._verify_jwt(token) is None
