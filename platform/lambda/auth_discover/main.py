"""POST /auth/discover-tenant — email → Cognito IdP routing.

Unauthed. Called by iOS + web BEFORE the OAuth flow so we can route the user
directly to the correct per-tenant Microsoft IdP (Cognito cannot federate
multi-tenant Microsoft via a single IdP — id_token issuer is per-tenant).

Flow:
  1. Extract domain from email.
  2. If Google domain → return idp_name="Google".
  3. Else → Microsoft. Resolve tenant ID via
     https://login.microsoftonline.com/<domain>/v2.0/.well-known/openid-configuration
     (returns issuer that contains the tenant GUID).
  4. Ensure a Cognito IdP named "Microsoft-<tenant-id>" exists; create it +
     add it to the user pool client's SupportedIdentityProviders if not.
  5. Return the authorize URL with identity_provider=<name> so the client
     skips the Cognito Hosted UI picker.

Lazy provisioning so onboarding a new customer's Microsoft tenant is just:
  - Their admin grants consent to our multi-tenant app reg
  - Their first user enters their email here → IdP gets created on-the-fly
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error

import boto3
from botocore.exceptions import ClientError

USER_POOL_ID         = os.environ["USER_POOL_ID"]
USER_POOL_CLIENT_ID  = os.environ["USER_POOL_CLIENT_ID"]      # iOS
WEB_POOL_CLIENT_ID   = os.environ.get("WEB_POOL_CLIENT_ID", "")
COGNITO_DOMAIN       = os.environ["COGNITO_DOMAIN"]           # ciso-copilot.auth.us-east-1.amazoncognito.com
MICROSOFT_CLIENT_ID  = os.environ["MICROSOFT_CLIENT_ID"]      # our multi-tenant app reg
MICROSOFT_SECRET_ARN = os.environ["MICROSOFT_CLIENT_SECRET_ARN"]
IOS_REDIRECT_URI     = os.environ.get("IOS_REDIRECT_URI",     "cisocopilot://auth/callback")
WEB_REDIRECT_URI     = os.environ.get("WEB_REDIRECT_URI",     "https://dil1ztnjosz43.cloudfront.net/callback")

cidp = boto3.client("cognito-idp")
sm   = boto3.client("secretsmanager")

GOOGLE_DOMAINS = {"gmail.com", "googlemail.com"}
TENANT_GUID_RE = re.compile(
    r"login\.microsoftonline\.com/([0-9a-fA-F-]{36})/v2\.0",
)

_ms_client_secret: str | None = None


def handler(event: dict, _ctx) -> dict:
    body  = _parse_body(event)
    email = (body.get("email") or "").strip().lower()
    platform = (body.get("platform") or "ios").lower()

    if not email or "@" not in email:
        return _resp(400, {"error": "invalid_email"})

    domain = email.split("@", 1)[1]

    if domain in GOOGLE_DOMAINS:
        return _resp(200, _payload("Google", platform, idp_provider="google"))

    # Microsoft path.
    try:
        tenant_id = _resolve_ms_tenant(domain)
    except _NoTenantError as e:
        return _resp(404, {"error": "tenant_not_found", "message": str(e)})

    # Cognito ProviderName must be ≤32 chars and may not contain underscores.
    # Strip the GUID's dashes (32 hex chars) and prefix with "MS-".
    idp_name = f"MS-{tenant_id.replace('-', '')[:29]}"

    if not _idp_exists(idp_name):
        try:
            _create_microsoft_idp(idp_name, tenant_id)
        except ClientError as e:
            print(f"CreateIdentityProvider failed: {e}")
            return _resp(500, {"error": "idp_create_failed", "detail": str(e)[:200]})

    try:
        _ensure_idp_on_client(idp_name)
    except ClientError as e:
        print(f"UpdateUserPoolClient failed: {e}")
        return _resp(500, {"error": "idp_attach_failed", "detail": str(e)[:200]})

    return _resp(200, _payload(idp_name, platform, idp_provider="microsoft", tenant_id=tenant_id))


# ============================================================================
# Microsoft tenant resolution
# ============================================================================

class _NoTenantError(Exception):
    pass


def _resolve_ms_tenant(domain: str) -> str:
    """Returns the tenant GUID for a Microsoft-managed email domain."""
    url = f"https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            doc = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise _NoTenantError(f"domain '{domain}' is not registered with Microsoft Entra (HTTP {e.code})")
    except Exception as e:
        raise _NoTenantError(f"could not resolve Microsoft tenant for '{domain}': {e}")

    issuer = doc.get("issuer", "")
    m = TENANT_GUID_RE.search(issuer)
    if not m:
        raise _NoTenantError(f"issuer did not contain a tenant GUID: {issuer!r}")
    return m.group(1)


# ============================================================================
# Cognito IdP lifecycle
# ============================================================================

def _idp_exists(name: str) -> bool:
    try:
        cidp.describe_identity_provider(UserPoolId=USER_POOL_ID, ProviderName=name)
        return True
    except cidp.exceptions.ResourceNotFoundException:
        return False


def _create_microsoft_idp(name: str, tenant_id: str) -> None:
    secret = _ms_secret()
    cidp.create_identity_provider(
        UserPoolId=USER_POOL_ID,
        ProviderName=name,
        ProviderType="OIDC",
        ProviderDetails={
            "client_id":     MICROSOFT_CLIENT_ID,
            "client_secret": secret,
            "attributes_request_method": "GET",
            "oidc_issuer":   f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            "authorize_scopes": "openid email profile",
        },
        AttributeMapping={
            "email":           "email",
            "given_name":      "given_name",
            "family_name":     "family_name",
            "name":            "name",
            "preferred_username": "preferred_username",
        },
    )
    print(f"Created Cognito IdP: {name}")


def _ensure_idp_on_client(name: str) -> None:
    """Idempotently add `name` to SupportedIdentityProviders on EVERY app client
    (iOS + web). If you only attach it to one, the other client's authorize
    endpoint will reject `identity_provider=<name>` with "invalid_request".
    """
    for client_id in (USER_POOL_CLIENT_ID, WEB_POOL_CLIENT_ID):
        if not client_id:
            continue
        _ensure_idp_on_single_client(client_id, name)


def _ensure_idp_on_single_client(client_id: str, name: str) -> None:
    desc = cidp.describe_user_pool_client(
        UserPoolId=USER_POOL_ID,
        ClientId=client_id,
    )["UserPoolClient"]

    current = set(desc.get("SupportedIdentityProviders") or [])
    if name in current:
        return

    updated = sorted(current | {name})
    # update_user_pool_client requires us to pass all the fields we want to
    # keep — anything omitted gets reset to default. Carefully echo back.
    args = {
        "UserPoolId":                       USER_POOL_ID,
        "ClientId":                         client_id,
        "ClientName":                       desc["ClientName"],
        "SupportedIdentityProviders":       updated,
        "CallbackURLs":                     desc.get("CallbackURLs") or [],
        "LogoutURLs":                       desc.get("LogoutURLs") or [],
        "AllowedOAuthFlows":                desc.get("AllowedOAuthFlows") or [],
        "AllowedOAuthScopes":               desc.get("AllowedOAuthScopes") or [],
        "AllowedOAuthFlowsUserPoolClient":  desc.get("AllowedOAuthFlowsUserPoolClient", False),
        "EnableTokenRevocation":            desc.get("EnableTokenRevocation", True),
    }
    if (flows := desc.get("ExplicitAuthFlows")):
        args["ExplicitAuthFlows"] = flows
    if (rv := desc.get("RefreshTokenValidity")):
        args["RefreshTokenValidity"] = rv
    if (av := desc.get("AccessTokenValidity")):
        args["AccessTokenValidity"] = av
    if (iv := desc.get("IdTokenValidity")):
        args["IdTokenValidity"] = iv
    if (tv := desc.get("TokenValidityUnits")):
        args["TokenValidityUnits"] = tv
    if (rcc := desc.get("ReadAttributes")):
        args["ReadAttributes"] = rcc
    if (wcc := desc.get("WriteAttributes")):
        args["WriteAttributes"] = wcc
    if (prc := desc.get("PreventUserExistenceErrors")):
        args["PreventUserExistenceErrors"] = prc

    cidp.update_user_pool_client(**args)
    print(f"Attached IdP {name} to client {client_id}")


def _ms_secret() -> str:
    global _ms_client_secret
    if _ms_client_secret is None:
        v = sm.get_secret_value(SecretId=MICROSOFT_SECRET_ARN)
        raw = v["SecretString"]
        # Stored either as raw or as {"client_secret": "..."}
        if raw.startswith("{"):
            _ms_client_secret = json.loads(raw).get("client_secret") or raw
        else:
            _ms_client_secret = raw
    return _ms_client_secret


# ============================================================================
# Response shaping
# ============================================================================

def _payload(idp_name: str, platform: str, *, idp_provider: str, tenant_id: str | None = None) -> dict:
    if platform == "web":
        redirect_uri = WEB_REDIRECT_URI
        client_id    = WEB_POOL_CLIENT_ID or USER_POOL_CLIENT_ID
    else:
        redirect_uri = IOS_REDIRECT_URI
        client_id    = USER_POOL_CLIENT_ID
    authorize_url = (
        f"https://{COGNITO_DOMAIN}/oauth2/authorize"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&scope=openid+email+profile"
        f"&redirect_uri={redirect_uri}"
        f"&identity_provider={idp_name}"
    )
    return {
        "idp_name":      idp_name,
        "idp_provider":  idp_provider,
        "tenant_id":     tenant_id,
        "authorize_url": authorize_url,
    }


# ============================================================================
# Helpers
# ============================================================================

def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers":    {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body":       json.dumps(body),
    }
