"""RFC 7636 PKCE helpers + DDB verifier store."""
from __future__ import annotations
import base64
import hashlib
import os
import secrets
import time
import boto3


def generate_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]  # well above 43-char minimum
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def challenge_hash(challenge: str) -> str:
    """Sha256 of the challenge — what we put in state JWT for verification."""
    return hashlib.sha256(challenge.encode("ascii")).hexdigest()


_dynamodb_resource = None


def _table():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    name = os.environ.get("PKCE_VERIFIER_TABLE", "cisocopilot-pkce-verifiers")
    return _dynamodb_resource.Table(name)


def store_verifier(*, nonce: str, verifier: str, ttl_seconds: int = 300) -> None:
    _table().put_item(Item={
        "nonce": nonce,
        "verifier": verifier,
        "ttl": int(time.time()) + ttl_seconds,
    })


def fetch_verifier(nonce: str) -> str | None:
    resp = _table().get_item(Key={"nonce": nonce})
    item = resp.get("Item")
    return item["verifier"] if item else None
