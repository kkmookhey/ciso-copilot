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
    # RFC 7636 §4.5: the verifier must be consumed on first use. delete_item
    # with ReturnValues=ALL_OLD does this atomically — concurrent callbacks
    # for the same nonce see at most one verifier each. The DDB TTL is a
    # belt-and-braces fallback if delete is never called (e.g., callback
    # never runs because the user closes the tab).
    resp = _table().delete_item(
        Key={"nonce": nonce},
        ReturnValues="ALL_OLD",
    )
    item = resp.get("Attributes")
    return item["verifier"] if item else None
