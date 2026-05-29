# MCP Connectors — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the load-bearing infrastructure of the MCP Connectors sub-project end-to-end with one vendor (Slack) connected via per-user OAuth: DB tables, KMS encryption, shared `mcp_oauth` Python package, `/v1/connectors/*` Lambda, tools dispatcher extension, `voice_session` dynamic tool discovery, Settings tabbed shell, Connectors catalog page with the Slack card live, and Slack "act" buttons on Risks page.

**Architecture:** Per-user tokens in Aurora `bytea` columns encrypted via `pgcrypto` + a KMS-derived data key cached in Lambda memory. OAuth 2.1 + PKCE; PKCE verifier in DynamoDB (5-min TTL), signed state JWT for callback validation. MCP via official Anthropic `mcp` Python SDK with Streamable HTTP transport. Tools dispatcher decodes namespaced names (`slack__send_message`) and routes to `mcp_oauth.get_session(subject, "slack")`. Voice agent's OpenAI tool registry built dynamically from `mcp_oauth.discover_tools()` at session bootstrap. Existing wow-demo `_shared/mcp_client.py` stdio wrapper untouched — still used by `create_jira_ticket.py` (Slice 3 will migrate Jira and delete).

**Tech Stack:** Python 3.12 (Lambda), `mcp` SDK ≥1.10, `pyjwt`, `boto3` (KMS + DynamoDB + Aurora Data API), `pgcrypto`. TypeScript + Vite + React 18 + Tailwind (web). pytest (Python tests), vitest (web tests). AWS CDK in TypeScript. Slack OAuth v2 + Slack MCP `https://mcp.slack.com/mcp`.

**Spec reference:** `docs/superpowers/specs/2026-05-28-mcp-connectors-design.md`. Sections 4 (architecture), 5 (data model), 6 (OAuth flow), 7 (runtime), 8 (web UI), 10 Slice 1.

**Slice 1 is NOT:** the autonomous broadcast rule (Slice 2), Atlassian (Slice 3), Google Workspace (Slice 4), Microsoft 365 (Slice 5), the `<DeepLinkGate>` wrapper (Slice 2 — it's only needed for autonomous deep links), the customer-defined rule builder (next sub-project).

---

## Phase 1 — Database + KMS infra

### Task 1: SQL migration for `user_connectors` and `tenant_bot_connectors`

**Files:**
- Create: `platform/sql/015_mcp_connectors.sql`

**Why:** Spec §5. These tables are the foundation; every later task reads from or writes to them. Schema also includes the `autonomous_rule_enabled` column on `tenant_bot_connectors` (used in Slice 2 but harmless to land now — keeps the schema migration whole).

- [ ] **Step 1: Write the migration file**

```sql
-- platform/sql/015_mcp_connectors.sql
-- MCP Connectors Slice 1 — per-user and per-tenant OAuth tokens for productivity tools.
-- Refs: docs/superpowers/specs/2026-05-28-mcp-connectors-design.md §5

-- Per-analyst, per-tool tokens. One active row per (tenant, user, provider).
CREATE TABLE IF NOT EXISTS user_connectors (
  conn_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            UUID NOT NULL REFERENCES tenants(tenant_id),
  user_id              UUID NOT NULL REFERENCES users(user_id),
  oauth_provider       TEXT NOT NULL,
  mcp_server_url       TEXT NOT NULL,
  vendor_user_id       TEXT NOT NULL,
  vendor_workspace_id  TEXT,
  access_token_enc     BYTEA NOT NULL,
  refresh_token_enc    BYTEA NOT NULL,
  access_expires_at    TIMESTAMPTZ NOT NULL,
  scopes               TEXT[] NOT NULL,
  status               TEXT NOT NULL DEFAULT 'active',
  last_error           TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at         TIMESTAMPTZ,
  revoked_at           TIMESTAMPTZ,
  UNIQUE (tenant_id, user_id, oauth_provider)
);

CREATE INDEX IF NOT EXISTS ix_user_connectors_lookup
  ON user_connectors (tenant_id, user_id, oauth_provider) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_user_connectors_refresh
  ON user_connectors (access_expires_at) WHERE status = 'active';

-- Admin-installed workspace bots. One bot per (tenant, provider).
-- Slice 1 lands the schema; the install flow + autonomous rule ship in Slice 2.
CREATE TABLE IF NOT EXISTS tenant_bot_connectors (
  bot_id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id                    UUID NOT NULL REFERENCES tenants(tenant_id),
  oauth_provider               TEXT NOT NULL,
  mcp_server_url               TEXT NOT NULL,
  vendor_workspace_id          TEXT NOT NULL,
  access_token_enc             BYTEA NOT NULL,
  refresh_token_enc            BYTEA,
  access_expires_at            TIMESTAMPTZ,
  scopes                       TEXT[] NOT NULL,
  broadcast_channel_id         TEXT,
  broadcast_channel_name       TEXT,
  autonomous_rule_enabled      BOOLEAN NOT NULL DEFAULT true,
  installed_by_user_id         UUID NOT NULL REFERENCES users(user_id),
  status                       TEXT NOT NULL DEFAULT 'active',
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at                 TIMESTAMPTZ,
  revoked_at                   TIMESTAMPTZ,
  UNIQUE (tenant_id, oauth_provider)
);

-- pgcrypto is already enabled (used by gen_random_uuid). Verify just in case.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

- [ ] **Step 2: Apply the migration via Data API**

Source `.env` first, then:

```bash
set -a && . platform/.env && set +a
aws rds-data execute-statement \
  --resource-arn "$DB_CLUSTER_ARN" \
  --secret-arn "$DB_SECRET_ARN" \
  --database ciso_copilot \
  --sql "$(cat platform/sql/015_mcp_connectors.sql)"
```

Expected: returns `{"numberOfRecordsUpdated": 0}` (DDL doesn't update rows but doesn't error).

- [ ] **Step 3: Verify tables exist**

```bash
aws rds-data execute-statement \
  --resource-arn "$DB_CLUSTER_ARN" \
  --secret-arn "$DB_SECRET_ARN" \
  --database ciso_copilot \
  --sql "SELECT table_name FROM information_schema.tables WHERE table_name IN ('user_connectors','tenant_bot_connectors') ORDER BY table_name"
```

Expected: two rows — `tenant_bot_connectors` and `user_connectors`.

- [ ] **Step 4: Commit**

```bash
git add platform/sql/015_mcp_connectors.sql
git commit -m "feat(db): add user_connectors + tenant_bot_connectors tables for MCP connectors

Slice 1 of MCP Connectors sub-project. Per-user OAuth tokens
(KMS-encrypted bytea) keyed by (tenant_id, user_id, oauth_provider).
Companion tenant_bot_connectors table lands the schema for Slice 2's
admin-installed Slack bot + autonomous broadcast rule.

Refs: docs/superpowers/specs/2026-05-28-mcp-connectors-design.md §5"
```

---

### Task 2: CDK — KMS key for connector tokens + IAM grants

**Files:**
- Modify: `platform/lib/data-stack.ts`

**Why:** Per-row column encryption needs a CMK. Lambda generates a data key once per cold start via `kms:GenerateDataKey` and caches the plaintext key in memory. The connectors Lambda + tools Lambda + voice_session Lambda all need decrypt; only the connectors Lambda needs encrypt (it's the only writer).

- [ ] **Step 1: Read current `data-stack.ts` to find the insertion point**

```bash
grep -n "Key\|kms\|export class\|aurora" platform/lib/data-stack.ts | head -30
```

Locate the constructor body where Aurora cluster + DDB tables are defined.

- [ ] **Step 2: Add the KMS key to `data-stack.ts`**

Add after Aurora cluster definition, exposing as a readonly property:

```typescript
// Inside DataStack class, alongside other readonly props
readonly connectorTokensKey: kms.Key;

// Inside constructor, after Aurora setup
this.connectorTokensKey = new kms.Key(this, "ConnectorTokensKey", {
  alias: "cisocopilot-connector-tokens",
  description: "Envelope key for MCP connector OAuth tokens (per-row pgcrypto)",
  enableKeyRotation: true,
  removalPolicy: cdk.RemovalPolicy.RETAIN,
});
```

Make sure `import * as kms from "aws-cdk-lib/aws-kms";` is at the top.

- [ ] **Step 3: Deploy the data stack**

```bash
cd platform
npx cdk deploy CisoCopilotData --require-approval never
```

Expected: CloudFormation update completes; new KMS key alias appears at
`alias/cisocopilot-connector-tokens` in the console.

- [ ] **Step 4: Verify key exists**

```bash
aws kms describe-key --key-id alias/cisocopilot-connector-tokens \
  --query 'KeyMetadata.{Arn:Arn,Enabled:Enabled,KeyState:KeyState}'
```

Expected: `Enabled=true`, `KeyState=Enabled`.

- [ ] **Step 5: Commit**

```bash
git add platform/lib/data-stack.ts
git commit -m "feat(cdk): add KMS key for MCP connector token encryption

Envelope key (alias/cisocopilot-connector-tokens) used by the new
mcp_oauth shared package to encrypt per-row OAuth refresh tokens in
user_connectors.access_token_enc / refresh_token_enc."
```

---

### Task 3: CDK — DynamoDB table for PKCE verifiers

**Files:**
- Modify: `platform/lib/data-stack.ts`

**Why:** OAuth state JWT carries the PKCE verifier hash; the actual verifier secret lives in DDB keyed by nonce, TTL 5 min. Separated so the state parameter sent through the browser doesn't carry the verifier.

- [ ] **Step 1: Add the table to `data-stack.ts`**

Below the KMS key, inside DataStack constructor:

```typescript
readonly pkceVerifierTable: dynamodb.Table;

// Inside constructor
this.pkceVerifierTable = new dynamodb.Table(this, "PkceVerifierTable", {
  tableName: "cisocopilot-pkce-verifiers",
  partitionKey: { name: "nonce", type: dynamodb.AttributeType.STRING },
  billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
  timeToLiveAttribute: "ttl",
  removalPolicy: cdk.RemovalPolicy.DESTROY,
});
```

Make sure `import * as dynamodb from "aws-cdk-lib/aws-dynamodb";` is at the top.

- [ ] **Step 2: Deploy**

```bash
cd platform
npx cdk deploy CisoCopilotData --require-approval never
```

- [ ] **Step 3: Verify table exists**

```bash
aws dynamodb describe-table --table-name cisocopilot-pkce-verifiers \
  --query 'Table.{Status:TableStatus,TTL:TimeToLiveDescription}'
```

Expected: `Status=ACTIVE`.

- [ ] **Step 4: Put the Slack OAuth state JWT signing secret in SSM**

This is a 32-byte random value. Generate locally:

```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
aws ssm put-parameter \
  --name /cisocopilot/connectors/state-jwt-secret \
  --type SecureString \
  --value "$SECRET" \
  --overwrite
```

Verify:

```bash
aws ssm get-parameter --name /cisocopilot/connectors/state-jwt-secret \
  --with-decryption --query 'Parameter.Type'
```

Expected: `"SecureString"`.

- [ ] **Step 5: Commit**

```bash
git add platform/lib/data-stack.ts
git commit -m "feat(cdk): add DynamoDB pkce-verifiers table for connector OAuth flow

5-min TTL store keyed by nonce. State JWT sent through the browser
carries only the verifier hash; the verifier itself lives here so it
never leaves backend systems. SSM parameter for state JWT signing key
was put out-of-band (see plan task 3)."
```

---

## Phase 2 — Shared `mcp_oauth` Python package

### Task 4: Token encryption helpers (TDD)

**Files:**
- Create: `platform/lambda/_shared/mcp_oauth/__init__.py`
- Create: `platform/lambda/_shared/mcp_oauth/crypto.py`
- Create: `platform/lambda/_shared/tests/test_mcp_oauth_crypto.py`

**Why:** Encrypt token bytes via `pgcrypto`'s `pgp_sym_encrypt` using a KMS-derived data key cached in module memory. Avoids per-row Secrets Manager cost.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/_shared/tests/test_mcp_oauth_crypto.py
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_crypto.py -v
```

Expected: ImportError on `from mcp_oauth.crypto import ...`.

- [ ] **Step 3: Create the package + crypto module**

```python
# platform/lambda/_shared/mcp_oauth/__init__.py
"""Per-user OAuth + remote-MCP client wrapper for Shasta connectors.

Public API:
  get_session(subject, kind, *, tenant_id) -> async context manager
  get_admin_session(tenant_id, kind)       -> async context manager (Slice 2)
  discover_tools(subject, *, tenant_id)    -> dict[kind, list[Tool]]

See docs/superpowers/specs/2026-05-28-mcp-connectors-design.md §7.
"""
from .crypto import encrypt_token, decrypt_token  # noqa: F401
```

```python
# platform/lambda/_shared/mcp_oauth/crypto.py
"""KMS-envelope encryption helpers for connector tokens.

We derive a 256-bit data key from KMS once per Lambda cold start and cache
it in module memory. Each token is encrypted with that key using
Fernet (symmetric authenticated encryption). The bytea written into
Aurora is the Fernet token; Aurora doesn't decrypt — we read the bytes,
decrypt in Lambda, and inject as Bearer header.

Note: spec §5 mentions pgp_sym_encrypt, but Fernet is simpler, doesn't
need pg-side keys, and round-trips cleanly through bytea. We keep the
KMS+envelope shape so the security posture is identical.
"""
from __future__ import annotations
import base64
import os
import threading
from cryptography.fernet import Fernet
import boto3

_kms = boto3.client("kms")
_cached_data_key: bytes | None = None
_cache_lock = threading.Lock()


def _get_data_key() -> bytes:
    global _cached_data_key
    if _cached_data_key is not None:
        return _cached_data_key
    with _cache_lock:
        if _cached_data_key is not None:
            return _cached_data_key
        key_arn = os.environ["CONNECTOR_TOKENS_KEY_ARN"]
        resp = _kms.generate_data_key(KeyId=key_arn, KeySpec="AES_256")
        _cached_data_key = resp["Plaintext"]
        return _cached_data_key


def _wrap_with_envelope(plaintext: bytes, data_key: bytes) -> bytes:
    f = Fernet(base64.urlsafe_b64encode(data_key))
    return f.encrypt(plaintext)


def _unwrap_envelope(ciphertext: bytes, data_key: bytes) -> bytes:
    f = Fernet(base64.urlsafe_b64encode(data_key))
    return f.decrypt(ciphertext)


def encrypt_token(token: str) -> bytes:
    return _wrap_with_envelope(token.encode("utf-8"), _get_data_key())


def decrypt_token(ciphertext: bytes) -> str:
    return _unwrap_envelope(ciphertext, _get_data_key()).decode("utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_crypto.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/mcp_oauth/ platform/lambda/_shared/tests/test_mcp_oauth_crypto.py
git commit -m "feat(mcp_oauth): KMS-envelope encryption helpers for connector tokens

Module-cached data key (one KMS call per cold start) + Fernet
authenticated symmetric encryption. encrypt_token/decrypt_token are
the only public surface used by writers/readers."
```

---

### Task 5: State JWT helpers (TDD)

**Files:**
- Create: `platform/lambda/_shared/mcp_oauth/state.py`
- Create: `platform/lambda/_shared/tests/test_mcp_oauth_state.py`

**Why:** OAuth `state` parameter ferries `tenant_id + user_id + provider + pkce_verifier_hash + nonce` through the browser. We sign it with `HS256` so the callback validates without a DB roundtrip. 5-min expiry.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/_shared/tests/test_mcp_oauth_state.py
from __future__ import annotations
import os
import time

import pytest


def test_state_round_trips(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state

    token = sign_state(
        tenant_id="tenant-1",
        user_id="user-1",
        provider="slack",
        pkce_verifier_hash="hash-abc",
    )
    claims = verify_state(token)
    assert claims["tenant_id"] == "tenant-1"
    assert claims["provider"] == "slack"
    assert claims["pkce_verifier_hash"] == "hash-abc"
    assert "nonce" in claims


def test_state_rejects_expired(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state

    token = sign_state(
        tenant_id="t", user_id="u", provider="slack",
        pkce_verifier_hash="h", ttl_seconds=1,
    )
    time.sleep(2)
    with pytest.raises(Exception):  # jwt.ExpiredSignatureError
        verify_state(token)


def test_state_rejects_tampered(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    from mcp_oauth.state import sign_state, verify_state

    token = sign_state(
        tenant_id="t", user_id="u", provider="slack",
        pkce_verifier_hash="h",
    )
    # Flip the last char of the signature segment
    head, payload, sig = token.split(".")
    bad = ".".join([head, payload, sig[:-1] + ("A" if sig[-1] != "A" else "B")])
    with pytest.raises(Exception):
        verify_state(bad)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_state.py -v
```

Expected: ImportError on `mcp_oauth.state`.

- [ ] **Step 3: Write the module**

```python
# platform/lambda/_shared/mcp_oauth/state.py
"""Signed-JWT state parameter for OAuth callbacks."""
from __future__ import annotations
import os
import secrets
import time
import jwt


def _secret() -> str:
    s = os.environ["STATE_JWT_SECRET"]
    if len(s) < 32:
        raise RuntimeError("STATE_JWT_SECRET must be at least 32 bytes")
    return s


def sign_state(*, tenant_id: str, user_id: str, provider: str,
               pkce_verifier_hash: str, ttl_seconds: int = 300) -> str:
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "provider": provider,
        "pkce_verifier_hash": pkce_verifier_hash,
        "nonce": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_state(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=["HS256"])
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_state.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/mcp_oauth/state.py platform/lambda/_shared/tests/test_mcp_oauth_state.py
git commit -m "feat(mcp_oauth): signed state-JWT helpers for OAuth callbacks

HS256 with 5-min default expiry. Carries tenant_id, user_id, provider,
pkce_verifier_hash, and a nonce that doubles as the DynamoDB key for
verifier lookup."
```

---

### Task 6: PKCE helpers + DynamoDB verifier store (TDD)

**Files:**
- Create: `platform/lambda/_shared/mcp_oauth/pkce.py`
- Create: `platform/lambda/_shared/tests/test_mcp_oauth_pkce.py`

**Why:** RFC 7636 challenge/verifier pair. The verifier is a high-entropy random string; the challenge is its SHA256 hash, base64url-no-pad. Store the verifier in DDB keyed by nonce so the callback can fetch it.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/_shared/tests/test_mcp_oauth_pkce.py
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
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_pkce.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the module**

```python
# platform/lambda/_shared/mcp_oauth/pkce.py
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
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_pkce.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/mcp_oauth/pkce.py platform/lambda/_shared/tests/test_mcp_oauth_pkce.py
git commit -m "feat(mcp_oauth): PKCE generate_pair + DDB verifier store

RFC 7636 challenge/verifier with the verifier stored in DynamoDB keyed
by nonce. 5-min TTL via DDB's built-in TTL attribute."
```

---

### Task 7: Slack OAuth provider config (TDD)

**Files:**
- Create: `platform/lambda/_shared/mcp_oauth/providers/__init__.py`
- Create: `platform/lambda/_shared/mcp_oauth/providers/slack.py`
- Create: `platform/lambda/_shared/tests/test_mcp_oauth_providers_slack.py`

**Why:** Each provider has a small config module: authorize URL, token URL, scopes, identity-extraction logic from token response, refresh-on-rotation behavior. Future providers (Atlassian, Google, MS) follow the same shape.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/_shared/tests/test_mcp_oauth_providers_slack.py
from __future__ import annotations
from unittest.mock import patch, MagicMock


def test_build_authorize_url():
    from mcp_oauth.providers.slack import build_authorize_url

    url = build_authorize_url(
        client_id="abc123",
        redirect_uri="https://app.shasta.io/v1/connectors/callback/slack",
        state="state-token",
        code_challenge="challenge-string",
    )
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=abc123" in url
    assert "state=state-token" in url
    assert "code_challenge=challenge-string" in url
    assert "code_challenge_method=S256" in url
    assert "user_scope=" in url  # per-user scopes, not bot scopes


def test_exchange_code_for_token(monkeypatch):
    from mcp_oauth.providers import slack as s

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "ok": True,
        "access_token": "xoxp-real-token",
        "refresh_token": "xoxe-1-...",
        "expires_in": 43200,
        "scope": "chat:write,im:write,search:read",
        "authed_user": {"id": "U0123"},
        "team": {"id": "T0123"},
    }
    fake_response.raise_for_status = MagicMock()
    monkeypatch.setattr(s.requests, "post", lambda *a, **kw: fake_response)

    result = s.exchange_code(code="auth-code", code_verifier="verifier",
                             client_id="cid", client_secret="csec",
                             redirect_uri="https://x/callback")
    assert result["access_token"] == "xoxp-real-token"
    assert result["refresh_token"] == "xoxe-1-..."
    assert result["vendor_user_id"] == "U0123"
    assert result["vendor_workspace_id"] == "T0123"
    assert result["expires_in"] == 43200
    assert "chat:write" in result["scopes"]
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_providers_slack.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the provider module**

```python
# platform/lambda/_shared/mcp_oauth/providers/__init__.py
"""Per-vendor OAuth provider configs. Each provider exposes:

  build_authorize_url(client_id, redirect_uri, state, code_challenge) -> str
  exchange_code(code, code_verifier, client_id, client_secret, redirect_uri) -> dict
  refresh_token(refresh_token, client_id, client_secret) -> dict

The dict shape is consistent across providers — see slack.py for the keys.
"""
```

```python
# platform/lambda/_shared/mcp_oauth/providers/slack.py
"""Slack OAuth v2 provider config."""
from __future__ import annotations
import urllib.parse
import requests

# Per-user (user-token) scopes — analyst acts as themselves.
USER_SCOPES = "chat:write,im:write,im:history,search:read,users:read"

MCP_SERVER_URL = "https://mcp.slack.com/mcp"
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.access"


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str,
                         code_challenge: str) -> str:
    qs = {
        "client_id": client_id,
        "user_scope": USER_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(qs)


def exchange_code(*, code: str, code_verifier: str, client_id: str,
                   client_secret: str, redirect_uri: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack oauth: {body.get('error', 'unknown_error')}")
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_in": body.get("expires_in", 43200),
        "scopes": body.get("scope", "").split(","),
        "vendor_user_id": body["authed_user"]["id"],
        "vendor_workspace_id": body["team"]["id"],
        "mcp_server_url": MCP_SERVER_URL,
    }


def refresh_token(*, refresh_token: str, client_id: str, client_secret: str) -> dict:
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"slack oauth refresh: {body.get('error', 'unknown_error')}")
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", refresh_token),  # may rotate
        "expires_in": body.get("expires_in", 43200),
    }
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_providers_slack.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/mcp_oauth/providers/ platform/lambda/_shared/tests/test_mcp_oauth_providers_slack.py
git commit -m "feat(mcp_oauth): Slack OAuth v2 provider config

build_authorize_url + exchange_code + refresh_token. User-token scopes
(chat:write, im:write, etc.) so the agent acts as the analyst, not as
a shared bot. MCP server URL pinned to mcp.slack.com/mcp."
```

---

### Task 8: `mcp_oauth.get_session()` + JIT refresh + advisory lock (TDD)

**Files:**
- Create: `platform/lambda/_shared/mcp_oauth/session.py`
- Create: `platform/lambda/_shared/tests/test_mcp_oauth_session.py`

**Why:** The runtime entry point. Reads the user's token row, refreshes if expired (with a Postgres advisory lock to prevent concurrent-refresh race per spec §12), opens an `mcp.ClientSession` against the provider's MCP URL with bearer auth.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/_shared/tests/test_mcp_oauth_session.py
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

    fresh_row = {
        "conn_id": "c-1",
        "access_token_enc": b"e1",
        "refresh_token_enc": b"e2",
        "access_expires_at": _now() + dt.timedelta(seconds=10),  # < 60s threshold
        "mcp_server_url": "https://mcp.slack.com/mcp",
    }
    monkeypatch.setattr(sess, "decrypt_token", lambda b: "old-access" if b == b"e1" else "old-refresh")
    monkeypatch.setattr(sess, "encrypt_token", lambda t: ("E:" + t).encode())

    monkeypatch.setattr(sess, "_provider_refresh", lambda kind, refresh: {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_in": 43200,
    })

    fake_db = MagicMock()
    monkeypatch.setattr(sess, "_db", lambda: fake_db)

    new_access = sess.refresh_if_near_expiry(fresh_row, kind="slack", tenant_id="t", user_id="u")
    assert new_access == "new-access"
    # Verify advisory lock acquired AND row updated
    sqls = [c.args[0] for c in fake_db.execute.call_args_list]
    assert any("pg_advisory_xact_lock" in s for s in sqls)
    assert any("UPDATE user_connectors" in s for s in sqls)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_session.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the session module**

```python
# platform/lambda/_shared/mcp_oauth/session.py
"""Runtime entry point: resolve user's connector row, JIT-refresh, open MCP session.

This module is sync at the resolution/refresh layer (Aurora Data API is
synchronous via boto3), and async at the MCP layer (mcp SDK is asyncio).
get_session() is an async context manager.
"""
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import os
import boto3
from contextlib import asynccontextmanager
from typing import Literal

from .crypto import encrypt_token, decrypt_token
from .providers import slack as slack_provider

ProviderKind = Literal["slack", "atlassian", "google", "microsoft"]


class ConnectorMissingError(RuntimeError):
    pass


class ConnectorRevokedError(RuntimeError):
    pass


# ---- DB helpers --------------------------------------------------------

_rds_client = None


def _db():
    """Returns the Aurora Data API client wrapper. _db().execute(sql, params)."""
    global _rds_client
    if _rds_client is None:
        _rds_client = _DataAPIWrapper()
    return _rds_client


class _DataAPIWrapper:
    def __init__(self):
        self._client = boto3.client("rds-data")
        self._cluster_arn = os.environ["DB_CLUSTER_ARN"]
        self._secret_arn = os.environ["DB_SECRET_ARN"]
        self._database = os.environ.get("DB_NAME", "ciso_copilot")

    def execute(self, sql: str, parameters: list | None = None):
        resp = self._client.execute_statement(
            resourceArn=self._cluster_arn,
            secretArn=self._secret_arn,
            database=self._database,
            sql=sql,
            parameters=parameters or [],
            includeResultMetadata=True,
        )
        return _Result(resp)


class _Result:
    def __init__(self, resp: dict):
        self._resp = resp

    def fetchone(self) -> dict | None:
        records = self._resp.get("records") or []
        if not records:
            return None
        meta = self._resp.get("columnMetadata") or []
        return _zip_record(meta, records[0])


def _zip_record(meta, record) -> dict:
    out = {}
    for col, cell in zip(meta, record):
        out[col["name"]] = next(iter(cell.values()))
    return out


# ---- Public API --------------------------------------------------------

def lookup_user_connector(*, tenant_id: str, user_id: str, kind: ProviderKind) -> dict:
    db = _db()
    sql = """
        SELECT conn_id, access_token_enc, refresh_token_enc,
               access_expires_at, mcp_server_url
        FROM user_connectors
        WHERE tenant_id = :tid AND user_id = :uid
          AND oauth_provider = :provider AND status = 'active'
    """
    row = db.execute(sql, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
        {"name": "provider", "value": {"stringValue": kind}},
    ]).fetchone()
    if not row:
        raise ConnectorMissingError(f"no active {kind} connector for {user_id}")
    return row


def _provider_refresh(kind: ProviderKind, refresh_token_plaintext: str) -> dict:
    cid = os.environ[f"{kind.upper()}_CLIENT_ID"]
    csec = os.environ[f"{kind.upper()}_CLIENT_SECRET"]
    if kind == "slack":
        return slack_provider.refresh_token(
            refresh_token=refresh_token_plaintext,
            client_id=cid, client_secret=csec,
        )
    raise NotImplementedError(f"refresh not implemented for {kind}")


def refresh_if_near_expiry(row: dict, *, kind: ProviderKind,
                            tenant_id: str, user_id: str,
                            threshold_seconds: int = 60) -> str:
    """Returns plaintext access_token. Refreshes inline if within threshold."""
    expires_at = row["access_expires_at"]
    if isinstance(expires_at, str):
        expires_at = dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    if (expires_at - now).total_seconds() > threshold_seconds:
        return decrypt_token(row["access_token_enc"])

    # Concurrent-refresh race mitigation: Postgres advisory lock keyed by conn_id.
    # Only one Lambda invocation refreshes; others wait and re-read.
    db = _db()
    lock_key = int(hashlib.sha256(str(row["conn_id"]).encode()).hexdigest()[:15], 16)
    db.execute("SELECT pg_advisory_xact_lock(:k)", [
        {"name": "k", "value": {"longValue": lock_key}}
    ])

    # Re-read under the lock — another invocation may have already refreshed.
    refreshed = db.execute("""
        SELECT access_token_enc, refresh_token_enc, access_expires_at
        FROM user_connectors WHERE conn_id = :cid
    """, [{"name": "cid", "value": {"stringValue": str(row["conn_id"])}}]).fetchone()

    re_expires_at = refreshed["access_expires_at"]
    if isinstance(re_expires_at, str):
        re_expires_at = dt.datetime.fromisoformat(re_expires_at.replace("Z", "+00:00"))
    if (re_expires_at - now).total_seconds() > threshold_seconds:
        return decrypt_token(refreshed["access_token_enc"])

    # Still expired — actually refresh.
    refresh_plain = decrypt_token(refreshed["refresh_token_enc"])
    new_tokens = _provider_refresh(kind, refresh_plain)
    new_access_enc = encrypt_token(new_tokens["access_token"])
    new_refresh_enc = encrypt_token(new_tokens["refresh_token"])
    new_expires_at = now + dt.timedelta(seconds=int(new_tokens["expires_in"]))

    db.execute("""
        UPDATE user_connectors
        SET access_token_enc = :a, refresh_token_enc = :r,
            access_expires_at = :e, last_used_at = now()
        WHERE conn_id = :cid
    """, [
        {"name": "a", "value": {"blobValue": new_access_enc}},
        {"name": "r", "value": {"blobValue": new_refresh_enc}},
        {"name": "e", "value": {"stringValue": new_expires_at.isoformat()}},
        {"name": "cid", "value": {"stringValue": str(row["conn_id"])}},
    ])
    return new_tokens["access_token"]


@asynccontextmanager
async def get_session(subject: str, kind: ProviderKind, *, tenant_id: str):
    """Open an MCP session for THIS user against THIS provider."""
    user_id = _resolve_user_id(subject, tenant_id=tenant_id)
    row = lookup_user_connector(tenant_id=tenant_id, user_id=user_id, kind=kind)
    access_token = refresh_if_near_expiry(row, kind=kind, tenant_id=tenant_id, user_id=user_id)

    # Lazy import to keep cold-start light if MCP isn't used in this path.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(row["mcp_server_url"], headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _resolve_user_id(subject: str, *, tenant_id: str) -> str:
    """Look up users.user_id from sso_subject."""
    row = _db().execute("""
        SELECT user_id FROM users
        WHERE tenant_id = :tid AND sso_subject = :sub
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "sub", "value": {"stringValue": subject}},
    ]).fetchone()
    if not row:
        raise ConnectorMissingError(f"no users row for subject={subject} tenant={tenant_id}")
    return str(row["user_id"])
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_session.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/mcp_oauth/session.py platform/lambda/_shared/tests/test_mcp_oauth_session.py
git commit -m "feat(mcp_oauth): get_session + JIT refresh with advisory lock

lookup_user_connector + refresh_if_near_expiry + get_session async
context manager. Advisory lock keyed by hashtext(conn_id) prevents
concurrent-refresh races that would invalidate Slack's rotating
refresh tokens."
```

---

### Task 9: `mcp_oauth.discover_tools()` with module-level cache (TDD)

**Files:**
- Modify: `platform/lambda/_shared/mcp_oauth/__init__.py` (add export)
- Modify: `platform/lambda/_shared/mcp_oauth/session.py` (add function)
- Create: `platform/lambda/_shared/tests/test_mcp_oauth_discover.py`

**Why:** Voice agent's tool registry built dynamically at session bootstrap. Cache by `(vendor_workspace_id, scopes_hash)` for 5 min so warm invocations skip the round-trip.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/_shared/tests/test_mcp_oauth_discover.py
from __future__ import annotations
import time
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_discover_caches_per_workspace(monkeypatch):
    from mcp_oauth.session import _discover_tools_for_user, _tool_cache

    _tool_cache.clear()
    fake_session = AsyncMock()
    fake_session.list_tools.return_value = MagicMock(tools=[
        MagicMock(name="send_message"),
    ])
    monkeypatch.setattr("mcp_oauth.session._open_session_for_user",
                        AsyncMock(return_value=fake_session))
    # Force the same workspace+scopes signature for two calls.
    monkeypatch.setattr("mcp_oauth.session._cache_signature",
                        lambda row: "T0123:hash-x")

    tools1 = await _discover_tools_for_user("u-1", kind="slack", tenant_id="t", row={"x": 1})
    tools2 = await _discover_tools_for_user("u-1", kind="slack", tenant_id="t", row={"x": 1})

    assert tools1 == tools2
    # Only one MCP round-trip
    assert fake_session.list_tools.call_count == 1
```

Note: this test uses `pytest-asyncio`. Add `asyncio_mode = "auto"` to `pytest.ini` if not already set.

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_discover.py -v
```

Expected: ImportError on the new symbols.

- [ ] **Step 3: Extend `session.py`**

Append to `platform/lambda/_shared/mcp_oauth/session.py`:

```python
# ---- Tool discovery cache ---------------------------------------------

import hashlib as _hashlib_dis
_tool_cache: dict[str, tuple[float, list]] = {}
_TOOL_CACHE_TTL = 300  # 5 minutes


def _cache_signature(row: dict) -> str:
    workspace = row.get("vendor_workspace_id") or ""
    scopes_hash = _hashlib_dis.sha256(
        (",".join(sorted(row.get("scopes") or []))).encode()
    ).hexdigest()[:16]
    return f"{workspace}:{scopes_hash}"


async def _open_session_for_user(user_id: str, *, kind: ProviderKind, tenant_id: str, row: dict):
    """Bypass the lookup-row step when caller already has the row."""
    access_token = refresh_if_near_expiry(row, kind=kind, tenant_id=tenant_id, user_id=user_id)
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    headers = {"Authorization": f"Bearer {access_token}"}

    @contextlib.asynccontextmanager
    async def _ctx():
        async with streamablehttp_client(row["mcp_server_url"], headers=headers) as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s

    return _ctx()


async def _discover_tools_for_user(user_id: str, *, kind: ProviderKind,
                                    tenant_id: str, row: dict) -> list:
    sig = _cache_signature(row)
    cached = _tool_cache.get(f"{kind}:{sig}")
    now = __import__("time").time()
    if cached and now - cached[0] < _TOOL_CACHE_TTL:
        return cached[1]

    ctx = await _open_session_for_user(user_id, kind=kind, tenant_id=tenant_id, row=row)
    async with ctx as session:
        result = await session.list_tools()
    tools = list(result.tools)
    _tool_cache[f"{kind}:{sig}"] = (now, tools)
    return tools


async def discover_tools(subject: str, *, tenant_id: str) -> dict[ProviderKind, list]:
    """For each provider the user has connected, return its tool manifest."""
    user_id = _resolve_user_id(subject, tenant_id=tenant_id)
    rows = _db().execute("""
        SELECT conn_id, oauth_provider, access_token_enc, refresh_token_enc,
               access_expires_at, mcp_server_url, vendor_workspace_id, scopes
        FROM user_connectors
        WHERE tenant_id = :tid AND user_id = :uid AND status = 'active'
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
    ])
    # Data API returns one record per row; convert all.
    out: dict[str, list] = {}
    raw = rows._resp.get("records") or []  # type: ignore[attr-defined]
    meta = rows._resp.get("columnMetadata") or []  # type: ignore[attr-defined]
    import asyncio as _asyncio
    tasks = []
    for rec in raw:
        row = _zip_record(meta, rec)
        kind = row["oauth_provider"]
        tasks.append(_discover_tools_for_user(user_id, kind=kind, tenant_id=tenant_id, row=row))
        out[kind] = []  # placeholder
    results = await _asyncio.gather(*tasks, return_exceptions=True)
    for kind, res in zip(list(out.keys()), results):
        if isinstance(res, Exception):
            print(f"[discover_tools] {kind} failed: {res!r}")
            out[kind] = []
        else:
            out[kind] = res
    return out
```

Update `platform/lambda/_shared/mcp_oauth/__init__.py`:

```python
from .session import (
    get_session,
    discover_tools,
    ConnectorMissingError,
    ConnectorRevokedError,
)
from .crypto import encrypt_token, decrypt_token  # noqa: F401
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda/_shared
python -m pytest tests/test_mcp_oauth_discover.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/_shared/mcp_oauth/__init__.py \
        platform/lambda/_shared/mcp_oauth/session.py \
        platform/lambda/_shared/tests/test_mcp_oauth_discover.py
git commit -m "feat(mcp_oauth): discover_tools with module-level cache

Builds per-user tool manifest by calling MCP list_tools per connected
provider in parallel. Caches by (workspace_id, scopes_hash) with 5-min
TTL so warm Lambda invocations skip the round-trip."
```

---

## Phase 3 — Connectors Lambda

### Task 10: Connectors Lambda skeleton + route dispatcher

**Files:**
- Create: `platform/lambda/connectors/__init__.py`
- Create: `platform/lambda/connectors/main.py`
- Create: `platform/lambda/connectors/requirements.txt`
- Create: `platform/lambda/connectors/tests/__init__.py`
- Create: `platform/lambda/connectors/tests/test_main.py`

**Why:** Single Lambda handling all `/v1/connectors/*` routes. Per-route handlers live in side modules registered by URL path.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/connectors/tests/test_main.py
from __future__ import annotations
import json


def _ev(*, method, path, claims=None, body=None, query=None):
    return {
        "httpMethod": method,
        "rawPath": path,
        "queryStringParameters": query or {},
        "body": json.dumps(body) if body else None,
        "requestContext": {"authorizer": {"claims": claims or {"sub": "u-1"}}},
    }


def test_unknown_route_returns_404():
    from connectors.main import handler

    resp = handler(_ev(method="GET", path="/v1/connectors/something-bad"), None)
    assert resp["statusCode"] == 404


def test_no_auth_returns_401():
    from connectors.main import handler

    ev = _ev(method="GET", path="/v1/connectors/me")
    ev["requestContext"]["authorizer"]["claims"] = {}
    resp = handler(ev, None)
    assert resp["statusCode"] == 401
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda
python -m pytest connectors/tests -v
```

Expected: ImportError.

- [ ] **Step 3: Write the Lambda skeleton**

```python
# platform/lambda/connectors/__init__.py
```

```python
# platform/lambda/connectors/main.py
"""Connectors Lambda — OAuth orchestration for MCP integrations.

Routes (all under /v1/connectors):
  POST   /connect/{kind}        initiate OAuth, returns authorize_url
  GET    /callback/{kind}       handle vendor redirect, store tokens
  DELETE /{conn_id}             revoke connection
  GET    /me                    list current user's active connectors

Per-kind specifics live in mcp_oauth.providers.{kind}. This module owns
HTTP shape, auth, route dispatch, and the DB write/delete operations.
"""
from __future__ import annotations
import json
import re
import traceback

# Reuse the canonical subject-extraction helper from the tools Lambda pattern.
def subject_from_claims(claims: dict) -> str | None:
    raw = claims.get("identities")
    if raw:
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(ids, dict):
                ids = [ids]
            if ids:
                return ids[0].get("userId") or claims.get("sub")
        except (TypeError, ValueError):
            pass
    return claims.get("sub")


_ROUTES = []  # list of (method, regex, handler)


def _route(method: str, pattern: str):
    def deco(fn):
        _ROUTES.append((method, re.compile(pattern), fn))
        return fn
    return deco


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    path = event.get("rawPath") or event.get("path") or ""
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}

    subject = subject_from_claims(claims)
    if not subject:
        return _resp(401, {"error": "no_auth"})

    for m, rx, fn in _ROUTES:
        if m != method:
            continue
        match = rx.match(path)
        if not match:
            continue
        try:
            return fn(event, claims, match.groupdict())
        except Exception as e:
            print(f"[connectors] {method} {path} failed: {type(e).__name__}: {e}")
            traceback.print_exc()
            return _resp(500, {"error": "internal", "detail": str(e)[:200]})

    return _resp(404, {"error": "unknown_route", "path": path})


def _resp(status: int, body: dict, *, headers: dict | None = None) -> dict:
    h = {"content-type": "application/json", "access-control-allow-origin": "*"}
    if headers:
        h.update(headers)
    return {"statusCode": status, "headers": h, "body": json.dumps(body)}


# Per-route handlers (decorate with @_route). Slack initiate / callback / etc.
# are registered in task 11+.
from connectors import handlers_slack  # noqa: F401,E402
from connectors import handlers_common  # noqa: F401,E402
```

```text
# platform/lambda/connectors/requirements.txt
# Slim Lambda dependencies — most live in the layer/_shared bundle.
requests==2.32.3
pyjwt==2.9.0
cryptography==43.0.1
mcp>=1.10.0
```

Add stubs so import doesn't break (they get filled in tasks 11 + 12):

```python
# platform/lambda/connectors/handlers_slack.py
"""Slack OAuth handlers — registered with the dispatcher."""
```

```python
# platform/lambda/connectors/handlers_common.py
"""Common handlers — revoke + list."""
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda
python -m pytest connectors/tests -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/connectors/
git commit -m "feat(connectors): Lambda skeleton with route dispatch + auth gate

/v1/connectors/* routes registered via @_route decorator. canonical
subject_from_claims for federated logins. Per-route handlers land in
follow-on commits."
```

---

### Task 11: `POST /connect/slack` handler (initiate flow)

**Files:**
- Modify: `platform/lambda/connectors/handlers_slack.py`
- Create: `platform/lambda/connectors/tests/test_handlers_slack.py`

**Why:** Builds the Slack authorize URL with PKCE challenge, stores the verifier in DynamoDB, signs the state JWT, returns `{ authorize_url }` for the web client to redirect to.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/connectors/tests/test_handlers_slack.py
from __future__ import annotations
import json
import os
from unittest.mock import patch, MagicMock


def test_initiate_returns_authorize_url(monkeypatch):
    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "xyz")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE", "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    with patch("connectors.handlers_slack.pkce.store_verifier") as store, \
         patch("connectors.handlers_slack._resolve_user_id", return_value="u-uuid"):
        from connectors import main as m
        ev = {
            "httpMethod": "POST",
            "rawPath": "/v1/connectors/connect/slack",
            "requestContext": {"authorizer": {"claims": {
                "sub": "subject-1", "custom:tenant_id": "t-uuid"
            }}},
        }
        resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["authorize_url"].startswith("https://slack.com/oauth/v2/authorize?")
    store.assert_called_once()
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_slack.py -v
```

Expected: AttributeError / missing route.

- [ ] **Step 3: Implement the handler**

```python
# platform/lambda/connectors/handlers_slack.py
"""Slack OAuth handlers — registered with the dispatcher."""
from __future__ import annotations
import os
from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth import pkce
from mcp_oauth import state as state_jwt
from mcp_oauth.providers import slack as slack_provider


def _resolve_user_id(subject: str, tenant_id: str) -> str:
    from mcp_oauth.session import _db
    row = _db().execute("""
        SELECT user_id FROM users
        WHERE tenant_id = :tid AND sso_subject = :sub
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "sub", "value": {"stringValue": subject}},
    ]).fetchone()
    if not row:
        raise RuntimeError(f"no users row for subject={subject}")
    return str(row["user_id"])


@_route("POST", r"^/v1/connectors/connect/slack$")
def initiate_slack(event, claims, _params):
    subject = subject_from_claims(claims)
    tenant_id = claims.get("custom:tenant_id")
    if not tenant_id:
        return _resp(400, {"error": "missing_tenant_id"})
    user_id = _resolve_user_id(subject, tenant_id)

    client_id = os.environ["SLACK_CLIENT_ID"]
    redirect_uri = f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack"

    verifier, challenge = pkce.generate_pair()
    state = state_jwt.sign_state(
        tenant_id=tenant_id,
        user_id=user_id,
        provider="slack",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
    )
    # Extract nonce from the signed state so we can key the verifier store
    import jwt as _jwt
    nonce = _jwt.decode(state, options={"verify_signature": False})["nonce"]
    pkce.store_verifier(nonce=nonce, verifier=verifier)

    url = slack_provider.build_authorize_url(
        client_id=client_id, redirect_uri=redirect_uri,
        state=state, code_challenge=challenge,
    )
    return _resp(200, {"authorize_url": url})
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_slack.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/connectors/handlers_slack.py platform/lambda/connectors/tests/test_handlers_slack.py
git commit -m "feat(connectors): POST /connect/slack initiate handler

Generates PKCE pair, stores verifier in DDB keyed by JWT nonce, builds
signed state, returns the Slack authorize URL for the web client to
window.location.href into."
```

---

### Task 12: `GET /callback/slack` handler (token exchange + storage)

**Files:**
- Modify: `platform/lambda/connectors/handlers_slack.py`
- Modify: `platform/lambda/connectors/tests/test_handlers_slack.py`

**Why:** Vendor redirects back with `?code=...&state=...`. Validate state, look up verifier, exchange code, encrypt tokens, INSERT into `user_connectors`. Redirect browser to `/settings/connectors?ok=slack`.

- [ ] **Step 1: Write the failing test**

Append to `platform/lambda/connectors/tests/test_handlers_slack.py`:

```python
def test_callback_inserts_user_connector(monkeypatch):
    import datetime as dt
    monkeypatch.setenv("SLACK_CLIENT_ID", "abc")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "xyz")
    monkeypatch.setenv("CONNECTORS_REDIRECT_BASE", "https://app.shasta.io/v1/connectors")
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)
    monkeypatch.setenv("WEB_BASE_URL", "https://app.shasta.io")

    # Sign a real state JWT that the handler can verify.
    from mcp_oauth import state as st, pkce
    from connectors import handlers_slack as h

    challenge = "ch-1"
    state_tok = st.sign_state(
        tenant_id="t-uuid", user_id="u-uuid", provider="slack",
        pkce_verifier_hash=pkce.challenge_hash(challenge),
    )

    monkeypatch.setattr(h.pkce, "fetch_verifier", lambda nonce: "v-1")
    monkeypatch.setattr(h.slack_provider, "exchange_code", lambda **kw: {
        "access_token": "xoxp-A",
        "refresh_token": "xoxe-R",
        "expires_in": 43200,
        "scopes": ["chat:write", "im:write"],
        "vendor_user_id": "U0X",
        "vendor_workspace_id": "T0X",
        "mcp_server_url": "https://mcp.slack.com/mcp",
    })
    inserted = {}
    class FakeDB:
        def execute(self, sql, params=None):
            if sql.strip().startswith("INSERT"):
                inserted["sql"] = sql
                inserted["params"] = params
            class R:
                def fetchone(self_inner): return None
            return R()
    monkeypatch.setattr(h, "_db", lambda: FakeDB())
    monkeypatch.setattr(h, "encrypt_token", lambda t: f"E:{t}".encode())

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/v1/connectors/callback/slack",
        "queryStringParameters": {"code": "ac-1", "state": state_tok},
        "requestContext": {"authorizer": {"claims": {"sub": "subject-1"}}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 302
    assert resp["headers"]["location"].endswith("/settings?tab=connectors&ok=slack")
    assert "INSERT INTO user_connectors" in inserted["sql"]
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_slack.py::test_callback_inserts_user_connector -v
```

Expected: ImportError on `encrypt_token` / `_db` in handlers_slack.

- [ ] **Step 3: Implement the callback handler**

Append to `platform/lambda/connectors/handlers_slack.py`:

```python
# Top-level imports — add to existing imports at the top of the file:
import datetime as dt
import urllib.parse
import jwt
from mcp_oauth.crypto import encrypt_token
from mcp_oauth.session import _db


@_route("GET", r"^/v1/connectors/callback/slack$")
def callback_slack(event, claims, _params):
    qs = event.get("queryStringParameters") or {}
    code = qs.get("code")
    state = qs.get("state")
    if not code or not state:
        return _resp(400, {"error": "missing_code_or_state"})

    try:
        s = state_jwt.verify_state(state)
    except Exception as e:
        return _resp(400, {"error": "invalid_state", "detail": str(e)[:120]})

    tenant_id = s["tenant_id"]
    user_id = s["user_id"]
    nonce = s["nonce"]
    pkce_hash = s["pkce_verifier_hash"]

    verifier = pkce.fetch_verifier(nonce)
    if not verifier:
        return _resp(400, {"error": "verifier_expired_or_missing"})

    # Defense in depth: verify the challenge we sent matches the one signed in state.
    # (Verifier was generated alongside the challenge; rebuild challenge from verifier.)
    import hashlib as _hashlib, base64 as _b64
    rebuilt_challenge = _b64.urlsafe_b64encode(
        _hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    if pkce.challenge_hash(rebuilt_challenge) != pkce_hash:
        return _resp(400, {"error": "pkce_mismatch"})

    client_id = os.environ["SLACK_CLIENT_ID"]
    client_secret = os.environ["SLACK_CLIENT_SECRET"]
    redirect_uri = f"{os.environ['CONNECTORS_REDIRECT_BASE']}/callback/slack"

    tokens = slack_provider.exchange_code(
        code=code, code_verifier=verifier,
        client_id=client_id, client_secret=client_secret,
        redirect_uri=redirect_uri,
    )

    access_enc = encrypt_token(tokens["access_token"])
    refresh_enc = encrypt_token(tokens["refresh_token"])
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(seconds=int(tokens["expires_in"]))

    db = _db()
    # Upsert: if a revoked/error row exists, overwrite it.
    db.execute("""
        INSERT INTO user_connectors (
            tenant_id, user_id, oauth_provider, mcp_server_url,
            vendor_user_id, vendor_workspace_id,
            access_token_enc, refresh_token_enc, access_expires_at,
            scopes, status
        ) VALUES (
            :tid, :uid, :provider, :mcp,
            :vu, :vw,
            :a, :r, :e,
            :scopes, 'active'
        )
        ON CONFLICT (tenant_id, user_id, oauth_provider) DO UPDATE SET
            access_token_enc = EXCLUDED.access_token_enc,
            refresh_token_enc = EXCLUDED.refresh_token_enc,
            access_expires_at = EXCLUDED.access_expires_at,
            scopes = EXCLUDED.scopes,
            status = 'active',
            last_error = NULL,
            revoked_at = NULL
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": user_id}},
        {"name": "provider", "value": {"stringValue": "slack"}},
        {"name": "mcp", "value": {"stringValue": tokens["mcp_server_url"]}},
        {"name": "vu", "value": {"stringValue": tokens["vendor_user_id"]}},
        {"name": "vw", "value": {"stringValue": tokens["vendor_workspace_id"]}},
        {"name": "a", "value": {"blobValue": access_enc}},
        {"name": "r", "value": {"blobValue": refresh_enc}},
        {"name": "e", "value": {"stringValue": expires_at.isoformat()}},
        {"name": "scopes", "value": {"stringValue": "{" + ",".join(tokens["scopes"]) + "}"}},
    ])

    web_base = os.environ["WEB_BASE_URL"]
    return {
        "statusCode": 302,
        "headers": {"location": f"{web_base}/settings?tab=connectors&ok=slack"},
        "body": "",
    }
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_slack.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/connectors/handlers_slack.py platform/lambda/connectors/tests/test_handlers_slack.py
git commit -m "feat(connectors): GET /callback/slack token exchange + insert

Validates state JWT + PKCE verifier, exchanges code for tokens via the
Slack provider, KMS-encrypts access+refresh, upserts into
user_connectors. 302s the browser back to /settings?tab=connectors&ok=slack."
```

---

### Task 13: `DELETE /{conn_id}` revoke handler

**Files:**
- Modify: `platform/lambda/connectors/handlers_common.py`
- Create: `platform/lambda/connectors/tests/test_handlers_common.py`

**Why:** User-initiated disconnect. Calls Slack `auth.revoke`, then marks row `status='revoked'`.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/connectors/tests/test_handlers_common.py
from __future__ import annotations
import json
from unittest.mock import patch


def test_revoke_marks_row_revoked(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    calls = []
    class FakeDB:
        def execute(self, sql, params=None):
            calls.append((sql.strip()[:60], params))
            class R:
                def fetchone(self_inner):
                    if "FROM user_connectors" in sql:
                        return {
                            "tenant_id": "t-uuid",
                            "oauth_provider": "slack",
                            "access_token_enc": b"E:xoxp-A",
                            "mcp_server_url": "https://mcp.slack.com/mcp",
                        }
                    return None
            return R()
    from connectors import handlers_common as h
    monkeypatch.setattr(h, "_db", lambda: FakeDB())
    monkeypatch.setattr(h, "decrypt_token", lambda b: "xoxp-A")
    monkeypatch.setattr(h.requests, "post", lambda *a, **kw: type("R", (), {
        "json": staticmethod(lambda: {"ok": True}),
        "raise_for_status": staticmethod(lambda: None),
    })())

    from connectors import main as m
    ev = {
        "httpMethod": "DELETE",
        "rawPath": "/v1/connectors/00000000-0000-0000-0000-000000000001",
        "requestContext": {"authorizer": {"claims": {
            "sub": "subject-1", "custom:tenant_id": "t-uuid"
        }}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    # First the SELECT, then the UPDATE
    assert any("UPDATE user_connectors" in s for s, _ in calls)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_common.py -v
```

Expected: route not registered.

- [ ] **Step 3: Implement the revoke handler**

```python
# platform/lambda/connectors/handlers_common.py
"""Common handlers: revoke + list."""
from __future__ import annotations
import datetime as dt
import requests
from connectors.main import _route, _resp, subject_from_claims
from mcp_oauth.crypto import decrypt_token
from mcp_oauth.session import _db


_REVOKE_URLS = {
    "slack": "https://slack.com/api/auth.revoke",
    # atlassian/google/microsoft added in their slices
}


@_route("DELETE", r"^/v1/connectors/(?P<conn_id>[0-9a-f-]{36})$")
def revoke_connection(event, claims, params):
    tenant_id = claims.get("custom:tenant_id")
    if not tenant_id:
        return _resp(400, {"error": "missing_tenant_id"})
    conn_id = params["conn_id"]

    db = _db()
    row = db.execute("""
        SELECT tenant_id, oauth_provider, access_token_enc, mcp_server_url
        FROM user_connectors
        WHERE conn_id = :cid AND tenant_id = :tid AND status = 'active'
    """, [
        {"name": "cid", "value": {"stringValue": conn_id}},
        {"name": "tid", "value": {"stringValue": tenant_id}},
    ]).fetchone()
    if not row:
        return _resp(404, {"error": "connector_not_found"})

    kind = row["oauth_provider"]
    revoke_url = _REVOKE_URLS.get(kind)
    if revoke_url:
        try:
            access = decrypt_token(row["access_token_enc"])
            r = requests.post(revoke_url, data={"token": access}, timeout=5)
            r.raise_for_status()
        except Exception as e:
            # Vendor revoke failure isn't fatal — we still revoke locally.
            print(f"[connectors] vendor revoke failed: {e}; marking locally")

    db.execute("""
        UPDATE user_connectors
        SET status = 'revoked', revoked_at = now()
        WHERE conn_id = :cid
    """, [{"name": "cid", "value": {"stringValue": conn_id}}])

    return _resp(200, {"revoked": True, "conn_id": conn_id})
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_common.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/connectors/handlers_common.py platform/lambda/connectors/tests/test_handlers_common.py
git commit -m "feat(connectors): DELETE /{conn_id} revoke handler

Calls vendor's revoke endpoint (Slack auth.revoke), then marks the row
status='revoked' locally. Vendor failure non-fatal — the token will rot
on its own."
```

---

### Task 14: `GET /me` list-current-user handler

**Files:**
- Modify: `platform/lambda/connectors/handlers_common.py`
- Modify: `platform/lambda/connectors/tests/test_handlers_common.py`

**Why:** Web client queries this on Settings page load to render card state. Returns only non-sensitive metadata (no token bytes).

- [ ] **Step 1: Write the failing test**

Append to `platform/lambda/connectors/tests/test_handlers_common.py`:

```python
def test_list_me_returns_active_connectors(monkeypatch):
    import datetime as dt
    monkeypatch.setenv("STATE_JWT_SECRET", "x" * 32)

    class FakeDB:
        def execute(self, sql, params=None):
            class R:
                def fetchone(self_inner): return {"user_id": "u-1"}
            R._resp = {
                "columnMetadata": [
                    {"name": "conn_id"}, {"name": "oauth_provider"},
                    {"name": "vendor_user_id"}, {"name": "vendor_workspace_id"},
                    {"name": "status"}, {"name": "created_at"}, {"name": "scopes"},
                ],
                "records": [[
                    {"stringValue": "c-1"}, {"stringValue": "slack"},
                    {"stringValue": "U0X"}, {"stringValue": "T0X"},
                    {"stringValue": "active"}, {"stringValue": "2026-05-28T12:00:00+00:00"},
                    {"stringValue": "{chat:write,im:write}"},
                ]]
            }
            return R()
    from connectors import handlers_common as h
    monkeypatch.setattr(h, "_db", lambda: FakeDB())

    from connectors import main as m
    ev = {
        "httpMethod": "GET",
        "rawPath": "/v1/connectors/me",
        "requestContext": {"authorizer": {"claims": {
            "sub": "subject-1", "custom:tenant_id": "t-uuid"
        }}},
    }
    resp = m.handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["connectors"][0]["provider"] == "slack"
    assert body["connectors"][0]["vendor_workspace_id"] == "T0X"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_common.py::test_list_me_returns_active_connectors -v
```

Expected: route not registered.

- [ ] **Step 3: Implement the list handler**

Append to `platform/lambda/connectors/handlers_common.py`:

```python
@_route("GET", r"^/v1/connectors/me$")
def list_me(event, claims, _params):
    tenant_id = claims.get("custom:tenant_id")
    subject = subject_from_claims(claims)
    if not tenant_id:
        return _resp(400, {"error": "missing_tenant_id"})

    db = _db()
    u = db.execute("""
        SELECT user_id FROM users
        WHERE tenant_id = :tid AND sso_subject = :sub
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "sub", "value": {"stringValue": subject}},
    ]).fetchone()
    if not u:
        return _resp(200, {"connectors": []})

    rows = db.execute("""
        SELECT conn_id, oauth_provider, vendor_user_id, vendor_workspace_id,
               status, created_at, scopes
        FROM user_connectors
        WHERE tenant_id = :tid AND user_id = :uid
          AND status IN ('active','error','expired')
        ORDER BY created_at DESC
    """, [
        {"name": "tid", "value": {"stringValue": tenant_id}},
        {"name": "uid", "value": {"stringValue": str(u["user_id"])}},
    ])

    raw = rows._resp.get("records") or []
    meta = rows._resp.get("columnMetadata") or []
    out = []
    for rec in raw:
        from connectors.main import _resp as _  # noqa
        row = {col["name"]: next(iter(cell.values())) for col, cell in zip(meta, rec)}
        out.append({
            "conn_id": str(row["conn_id"]),
            "provider": row["oauth_provider"],
            "vendor_user_id": row["vendor_user_id"],
            "vendor_workspace_id": row.get("vendor_workspace_id"),
            "status": row["status"],
            "created_at": str(row["created_at"]),
            "scopes": str(row.get("scopes") or "").strip("{}").split(",") if row.get("scopes") else [],
        })
    return _resp(200, {"connectors": out})
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda
python -m pytest connectors/tests/test_handlers_common.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/connectors/handlers_common.py platform/lambda/connectors/tests/test_handlers_common.py
git commit -m "feat(connectors): GET /me list-current-user handler

Returns active/error/expired connectors for the calling user with
non-sensitive metadata only (no token bytes). Powers the Settings
Connectors page card state."
```

---

## Phase 4 — API Gateway + CDK wiring

### Task 15: CDK — wire connectors Lambda + IAM grants + env vars + API routes

**Files:**
- Modify: `platform/lib/api-stack.ts`

**Why:** The Lambda exists in code; CDK has to deploy it and grant it the KMS / DDB / Aurora / SSM permissions it needs, plus register the routes with the existing API Gateway.

- [ ] **Step 1: Read relevant section of api-stack.ts**

```bash
grep -n "lambda.Function\|addRoute\|grantInvoke\|requirements.txt\|fromAsset" platform/lib/api-stack.ts | head -30
```

Note the pattern used by an existing zip-asset Lambda (e.g., `voice_session` or `device_token_register`).

- [ ] **Step 2: Add the connectors Lambda + routes**

Inside the api-stack constructor, after similar Lambdas (model after `device_token_register` which is a similarly-shaped zip-asset Python Lambda):

```typescript
// Connectors Lambda — OAuth orchestration for MCP connectors.
const connectorsFn = new lambda.Function(this, "ConnectorsFn", {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: "connectors.main.handler",
  code: lambda.Code.fromAsset("lambda", {
    // Bundle connectors/ and _shared/ together so `from mcp_oauth import …`
    // resolves at cold start. Same trick voice_session uses.
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      command: [
        "bash", "-c",
        "pip install -r connectors/requirements.txt -t /asset-output && " +
        "cp -r connectors /asset-output/ && " +
        "cp -r _shared/mcp_oauth /asset-output/ && " +
        "cp -r _shared/tests /asset-output/_tests || true"
      ],
    },
  }),
  timeout: cdk.Duration.seconds(30),
  memorySize: 512,
  environment: {
    DB_CLUSTER_ARN: this.dbClusterArn,
    DB_SECRET_ARN: this.dbSecretArn,
    DB_NAME: "ciso_copilot",
    PKCE_VERIFIER_TABLE: this.dataStack.pkceVerifierTable.tableName,
    CONNECTOR_TOKENS_KEY_ARN: this.dataStack.connectorTokensKey.keyArn,
    STATE_JWT_SECRET: ssm.StringParameter.valueForStringParameter(
      this, "/cisocopilot/connectors/state-jwt-secret"
    ),
    SLACK_CLIENT_ID: ssm.StringParameter.valueForStringParameter(
      this, "/cisocopilot/connectors/slack/client-id"
    ),
    SLACK_CLIENT_SECRET: ssm.StringParameter.valueForStringParameter(
      this, "/cisocopilot/connectors/slack/client-secret"
    ),
    CONNECTORS_REDIRECT_BASE: `https://${this.apiDomain}/v1/connectors`,
    WEB_BASE_URL: `https://${this.webDomain}`,
  },
});

// Permissions
this.dataStack.connectorTokensKey.grantEncryptDecrypt(connectorsFn);
this.dataStack.pkceVerifierTable.grantReadWriteData(connectorsFn);
this.auroraSecret.grantRead(connectorsFn);
connectorsFn.addToRolePolicy(new iam.PolicyStatement({
  actions: ["rds-data:ExecuteStatement"],
  resources: [this.dbClusterArn],
}));

// Routes — under existing HttpApi `this.api`
const integ = new HttpLambdaIntegration("ConnectorsInteg", connectorsFn);
this.api.addRoutes({
  path: "/v1/connectors/connect/{kind}",
  methods: [HttpMethod.POST],
  integration: integ,
  authorizer: this.cognitoAuthorizer,
});
this.api.addRoutes({
  path: "/v1/connectors/callback/{kind}",
  methods: [HttpMethod.GET],
  integration: integ,
  // Callback is hit by the vendor; auth happens via state JWT, not Cognito.
  // (Open route is fine because the state JWT is the gate.)
});
this.api.addRoutes({
  path: "/v1/connectors/{conn_id}",
  methods: [HttpMethod.DELETE],
  integration: integ,
  authorizer: this.cognitoAuthorizer,
});
this.api.addRoutes({
  path: "/v1/connectors/me",
  methods: [HttpMethod.GET],
  integration: integ,
  authorizer: this.cognitoAuthorizer,
});
```

If `ssm`, `iam`, `HttpLambdaIntegration`, `HttpMethod` aren't imported in `api-stack.ts`, add the imports per the existing convention in that file.

- [ ] **Step 3: Put Slack OAuth app credentials in SSM**

This is a manual step — KK creates a Slack App at https://api.slack.com/apps with the right redirect URI (`https://api.shasta.io/v1/connectors/callback/slack`) and user scopes (`chat:write,im:write,im:history,search:read,users:read`), then:

```bash
aws ssm put-parameter --name /cisocopilot/connectors/slack/client-id \
  --type SecureString --value "<client_id>" --overwrite
aws ssm put-parameter --name /cisocopilot/connectors/slack/client-secret \
  --type SecureString --value "<client_secret>" --overwrite
```

- [ ] **Step 4: Deploy the api stack**

```bash
cd platform
npx cdk deploy CisoCopilotApi --require-approval never
```

Expected: stack update completes; new Lambda visible in console; API Gateway shows the four routes.

- [ ] **Step 5: Smoke-test the auth gate**

```bash
curl -sS "https://api.shasta.io/v1/connectors/me" | jq
```

Expected (no auth): `{"message":"Unauthorized"}` (Cognito authorizer rejecting).

- [ ] **Step 6: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "feat(cdk): deploy connectors Lambda + register /v1/connectors/* routes

KMS encrypt/decrypt + DynamoDB rw + Aurora Data API + SSM-backed env
for Slack OAuth credentials. POST/DELETE/GET routes Cognito-gated;
GET /callback/{kind} is open (state JWT is the gate, vendor hits it)."
```

---

## Phase 5 — Tools dispatcher + voice_session bootstrap

### Task 16: Tools dispatcher `kind__tool` MCP route

**Files:**
- Modify: `platform/lambda/tools/main.py`
- Modify: `platform/lambda/tools/tests/test_tools.py`

**Why:** When voice agent calls `slack__send_message`, the dispatcher needs to recognize the namespace, look up the user's Slack connector, open a session, call the tool.

- [ ] **Step 1: Write the failing test**

Append to `platform/lambda/tools/tests/test_tools.py`:

```python
def test_namespaced_mcp_tool_dispatched_via_mcp_oauth(monkeypatch):
    import json
    from unittest.mock import AsyncMock, MagicMock

    # Build the event the voice agent's dispatch shape uses.
    ev = {
        "pathParameters": {"tool_name": "slack__send_message"},
        "body": json.dumps({"channel": "C0X", "text": "hi"}),
        "requestContext": {"authorizer": {"claims": {
            "sub": "subject-1", "custom:tenant_id": "t-uuid"
        }}},
    }

    fake_session = AsyncMock()
    fake_session.call_tool.return_value = MagicMock(
        content=[MagicMock(text=json.dumps({"ok": True, "ts": "1.0"}))]
    )

    @asyncgen
    async def fake_ctx():
        yield fake_session

    import contextlib
    @contextlib.asynccontextmanager
    async def fake_get_session(*a, **kw):
        yield fake_session

    monkeypatch.setattr("mcp_oauth.get_session", fake_get_session)

    from tools.main import handler
    resp = handler(ev, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ok"] is True
```

(Helper: `asyncgen` decorator — already imported in existing test_tools.py if present; otherwise add `from contextlib import asynccontextmanager as asyncgen`.)

- [ ] **Step 2: Run to verify failure**

```bash
cd platform/lambda
python -m pytest tools/tests/test_tools.py::test_namespaced_mcp_tool_dispatched_via_mcp_oauth -v
```

Expected: dispatcher doesn't handle namespaced names; returns 404 `unknown_tool`.

- [ ] **Step 3: Add the MCP branch to the dispatcher**

Modify `platform/lambda/tools/main.py`. Add after the existing dispatch lookup:

```python
# At the top of the file, add:
import asyncio


_MCP_PROVIDER_KINDS = {"slack", "atlassian", "google", "microsoft"}


def _is_namespaced_mcp(name: str) -> tuple[str | None, str | None]:
    if "__" not in name:
        return None, None
    kind, _, rest = name.partition("__")
    if kind not in _MCP_PROVIDER_KINDS:
        return None, None
    return kind, rest


async def _call_mcp_tool(*, kind: str, tool_name: str, args: dict,
                          subject: str, tenant_id: str) -> dict:
    from mcp_oauth import get_session
    async with get_session(subject, kind, tenant_id=tenant_id) as session:
        result = await session.call_tool(tool_name, args)
    return _extract_mcp_result(result)


def _extract_mcp_result(result) -> dict:
    if not getattr(result, "content", None):
        return {}
    first = result.content[0]
    text = getattr(first, "text", None)
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
```

In the `handler()` body, BEFORE the `_DISPATCH[tool_name]` call, add:

```python
    # MCP-namespaced tool? Route via mcp_oauth.
    kind, mcp_tool = _is_namespaced_mcp(tool_name)
    if kind:
        tenant_id = claims.get("custom:tenant_id")
        if not tenant_id:
            return _resp(400, {"error": "missing_tenant_id"})
        subject = subject_from_claims(claims)
        try:
            result = asyncio.run(_call_mcp_tool(
                kind=kind, tool_name=mcp_tool, args=args,
                subject=subject, tenant_id=tenant_id,
            ))
            return _resp(200, result)
        except Exception as e:
            from mcp_oauth.session import ConnectorMissingError, ConnectorRevokedError
            if isinstance(e, ConnectorMissingError):
                return _resp(409, {
                    "error": "connector_missing",
                    "kind": kind,
                    "message": f"Connect your {kind.title()} in Settings to use this.",
                })
            if isinstance(e, ConnectorRevokedError):
                return _resp(409, {
                    "error": "connector_revoked",
                    "kind": kind,
                    "message": f"Your {kind.title()} connection expired — reconnect in Settings.",
                })
            print(f"[tools] mcp call {tool_name} failed: {type(e).__name__}: {e}")
            return _resp(502, {"error": "mcp_failed", "detail": str(e)[:200]})
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd platform/lambda
python -m pytest tools/tests/test_tools.py::test_namespaced_mcp_tool_dispatched_via_mcp_oauth -v
```

Expected: 1 passed.

- [ ] **Step 5: Build + deploy tools Lambda**

```bash
cd platform/lambda/tools
./build.sh
cd ../..
npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```

- [ ] **Step 6: Commit**

```bash
git add platform/lambda/tools/main.py platform/lambda/tools/tests/test_tools.py
git commit -m "feat(tools): namespaced kind__tool MCP dispatch route

slack__send_message + atlassian__create_issue + google__send_mail +
microsoft__search_outlook all route through mcp_oauth.get_session()
with caller's per-user token. ConnectorMissingError → 409 with a
'connect in Settings' hint."
```

---

### Task 17: voice_session bootstrap dynamic tool registry

**Files:**
- Modify: `platform/lambda/voice_session/main.py`
- Modify: `platform/lambda/voice_session/tests/test_system_prompt.py` (or new test file)

**Why:** Voice agent's OpenAI Realtime session config gets the dynamically-discovered tool list at session start. Per-analyst, scope-aware.

- [ ] **Step 1: Read the existing tool-registry section of voice_session/main.py**

```bash
grep -n "tools\|session_config\|openai_tools\|session.update" platform/lambda/voice_session/main.py | head -30
```

Note where the session config is built and where `tools` is set.

- [ ] **Step 2: Write the failing test**

```python
# Add to platform/lambda/voice_session/tests/test_system_prompt.py or new
# file: tests/test_dynamic_tools.py
from __future__ import annotations
import asyncio
from unittest.mock import patch, MagicMock


def test_dynamic_tools_built_from_discover_tools():
    from voice_session.main import _build_openai_tools

    async def fake_discover(*a, **kw):
        slack_tool = MagicMock()
        slack_tool.name = "send_message"
        slack_tool.description = "Send Slack message"
        slack_tool.inputSchema = {"type": "object", "properties": {}}
        return {"slack": [slack_tool]}

    result = asyncio.run(_build_openai_tools(
        subject="s", tenant_id="t",
        discover_fn=fake_discover,
        native_tools=[{"type": "function", "name": "run_forensic_scan"}],
    ))
    names = [t["name"] for t in result]
    assert "slack__send_message" in names
    assert "run_forensic_scan" in names
```

- [ ] **Step 3: Run to verify failure**

```bash
cd platform/lambda
python -m pytest voice_session/tests/test_system_prompt.py -v
```

Expected: `_build_openai_tools` not defined.

- [ ] **Step 4: Add `_build_openai_tools` to voice_session/main.py**

Add to `platform/lambda/voice_session/main.py` (just before the existing session-config build):

```python
async def _build_openai_tools(*, subject: str, tenant_id: str,
                                discover_fn=None, native_tools=None):
    """Per-session OpenAI tool registry.

    Combines:
      - Shasta-native tools (run_forensic_scan, etc.) — always present
      - Per-vendor MCP tools discovered live from each connected provider
        with namespace prefix `{kind}__{tool_name}`.
    """
    discover_fn = discover_fn or _default_discover
    native_tools = native_tools or _NATIVE_TOOLS

    out = list(native_tools)
    try:
        connected = await discover_fn(subject, tenant_id=tenant_id)
    except Exception as e:
        print(f"[voice_session] discover_tools failed: {e!r}; native tools only")
        return out

    for kind, tools in connected.items():
        for t in tools:
            name = getattr(t, "name", None) or t.get("name")
            desc = getattr(t, "description", None) or t.get("description", "")
            schema = getattr(t, "inputSchema", None) or t.get("inputSchema", {"type": "object"})
            out.append({
                "type": "function",
                "name": f"{kind}__{name}",
                "description": desc,
                "parameters": schema,
            })
    return out


async def _default_discover(subject: str, *, tenant_id: str):
    from mcp_oauth import discover_tools
    return await discover_tools(subject, tenant_id=tenant_id)
```

And in the session-config build (locate the line where `tools=` is set on the OpenAI session), replace the static list with:

```python
# At the call site that builds the session config:
import asyncio as _asyncio
openai_tools = _asyncio.run(_build_openai_tools(
    subject=subject, tenant_id=tenant_id,
))
session_config["tools"] = openai_tools
```

- [ ] **Step 5: Run test to verify pass**

```bash
cd platform/lambda
python -m pytest voice_session/tests/ -v
```

Expected: all tests pass (incl. the new one).

- [ ] **Step 6: Deploy voice_session**

```bash
cd platform
npx cdk deploy CisoCopilotApi --require-approval never --hotswap
```

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/voice_session/main.py platform/lambda/voice_session/tests/
git commit -m "feat(voice_session): dynamic per-user MCP tool registry

Session bootstrap calls mcp_oauth.discover_tools() in parallel for each
connected vendor. OpenAI Realtime tool list now reflects what THIS
analyst has connected, namespaced as slack__send_message etc. Falls
back to native tools only if discovery fails."
```

---

## Phase 6 — Web /settings tabbed shell

### Task 18: Web — `/settings` tabbed route shell

**Files:**
- Create: `web/src/routes/Settings/Settings.tsx`
- Create: `web/src/routes/Settings/ConnectorsTab.tsx`
- Modify: `web/src/routes/Shell.tsx`

**Why:** Memory note: consolidate one-time setup under `/settings`. This task lands the shell; the Connectors tab content lands in tasks 19-21. Profile/Team/Billing tabs are placeholders.

- [ ] **Step 1: Read existing Shell.tsx routing**

```bash
cat web/src/routes/Shell.tsx
```

Note how routes are added (likely `<Route path=... element=...`).

- [ ] **Step 2: Create the Settings shell**

```typescript
// web/src/routes/Settings/Settings.tsx
import { useSearchParams, useNavigate } from "react-router-dom";
import { ConnectorsTab } from "./ConnectorsTab";

type TabKey = "profile" | "cloud" | "connectors" | "team" | "billing";

const TABS: { key: TabKey; label: string }[] = [
  { key: "profile",    label: "Profile" },
  { key: "cloud",      label: "Cloud connections" },
  { key: "connectors", label: "Connectors" },
  { key: "team",       label: "Team" },
  { key: "billing",    label: "Billing" },
];

export function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as TabKey) || "connectors";
  const nav = useNavigate();

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="text-2xl font-semibold mb-1">Settings</h1>
      <nav className="flex gap-6 border-b border-neutral-200 mb-7">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setParams({ tab: t.key })}
            className={
              "py-3 -mb-px text-sm " +
              (tab === t.key
                ? "text-neutral-900 font-semibold border-b-2 border-[#d2552b]"
                : "text-neutral-500 hover:text-neutral-700")
            }
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "connectors" && <ConnectorsTab />}
      {tab === "cloud" && (
        <div className="text-sm text-neutral-500">
          The existing cloud onboarding flow will move into this tab in a follow-on PR.
          For now visit <a className="text-[#d2552b]" href="/connect-clouds">/connect-clouds</a>.
        </div>
      )}
      {tab === "profile" && <Placeholder name="Profile" />}
      {tab === "team" && <Placeholder name="Team" />}
      {tab === "billing" && <Placeholder name="Billing" />}
    </div>
  );
}

function Placeholder({ name }: { name: string }) {
  return <div className="text-sm text-neutral-500">{name} settings — coming later.</div>;
}
```

```typescript
// web/src/routes/Settings/ConnectorsTab.tsx
export function ConnectorsTab() {
  return (
    <div className="text-sm text-neutral-500">
      Connectors catalog landing in task 19.
    </div>
  );
}
```

- [ ] **Step 3: Register the route in Shell.tsx**

In `web/src/routes/Shell.tsx`, add the route. The pattern matches existing entries — something like:

```typescript
import { Settings } from "./Settings/Settings";

// inside <Routes>
<Route path="/settings" element={<Settings />} />
```

- [ ] **Step 4: Run the web app and verify**

```bash
cd web
pnpm dev
```

Open the dev URL, sign in, visit `/settings?tab=connectors`. Expected: tabbed shell renders, "Connectors catalog landing in task 19." placeholder visible.

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/Settings/ web/src/routes/Shell.tsx
git commit -m "feat(web): /settings tabbed shell with Connectors tab placeholder

Profile / Cloud connections / Connectors / Team / Billing tabs.
Cloud connections shows a temp link to /connect-clouds until the
existing flow gets folded in. Connectors tab content lands next."
```

---

### Task 19: Web — extend `api.ts` + `useConnectors` hook

**Files:**
- Modify: `web/src/lib/api.ts` (extend the existing `api` object + add types)
- Create: `web/src/lib/useConnectors.ts`

**Why:** The codebase pattern is one `api` object in `web/src/lib/api.ts` with named methods (e.g., `api.listConnections()`, `api.deleteConnection(id)`) all going through a shared private `call<T>()` helper. We extend that object — not introduce a parallel `get/post/del` shape. The React hook lives in its own file so it doesn't pollute `api.ts`.

- [ ] **Step 1: Add types + methods to `web/src/lib/api.ts`**

Add the types alongside the other `export interface` blocks (anywhere logical, near the cloud-connector types around lines 44–60):

```typescript
// web/src/lib/api.ts — add near the other connection interfaces
export type ProviderKind = "slack" | "atlassian" | "google" | "microsoft";

export interface ConnectorRow {
  conn_id:             string;
  provider:            ProviderKind;
  vendor_user_id:      string;
  vendor_workspace_id: string | null;
  status:              "active" | "revoked" | "expired" | "error";
  created_at:          string;
  scopes:              string[];
}

export interface ListConnectorsResponse {
  connectors: ConnectorRow[];
}

export interface InitiateConnectResponse {
  authorize_url: string;
}
```

Then add the methods inside the existing `export const api = { ... }` object (around line 382). Match the surrounding code style — single-line arrow functions calling the private `call<T>` helper:

```typescript
  // MCP Connectors (Slice 1)
  listConnectors: () =>
    call<ListConnectorsResponse>("/v1/connectors/me"),

  initiateConnectorOAuth: (kind: ProviderKind) =>
    call<InitiateConnectResponse>(`/v1/connectors/connect/${kind}`, { method: "POST", body: "{}" }),

  revokeConnector: (connId: string) =>
    call<{ revoked: boolean }>(`/v1/connectors/${connId}`, { method: "DELETE" }),

  callTool: (toolName: string, args: unknown) =>
    call<Record<string, unknown>>(`/v1/tools/${toolName}`, {
      method: "POST",
      body: JSON.stringify(args),
    }),
```

`callTool` is for the act-button path (Task 21) — same `tools/` Lambda the voice agent calls.

- [ ] **Step 2: Create the `useConnectors` hook**

```typescript
// web/src/lib/useConnectors.ts
import { useEffect, useState, useCallback } from "react";
import { api, type ConnectorRow } from "./api";

export function useConnectors() {
  const [connectors, setConnectors] = useState<ConnectorRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api.listConnectors()
      .then(r => setConnectors(r.connectors))
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  return { connectors, error, reload };
}
```

- [ ] **Step 3: Build + verify**

```bash
cd web
pnpm build
```

Expected: build succeeds, no TS errors. Existing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/useConnectors.ts
git commit -m "feat(web): connector API methods + useConnectors hook

Extends the existing api{} object with listConnectors,
initiateConnectorOAuth, revokeConnector, callTool — same pattern as
listConnections/deleteConnection. useConnectors hook in its own file
keeps api.ts clean."
```

Names locked by this task and used downstream: `api.initiateConnectorOAuth`, `api.revokeConnector`, `api.callTool`, `useConnectors` from `lib/useConnectors`.

---

### Task 20: Web — Connectors catalog grid with Slack card live

**Files:**
- Modify: `web/src/routes/Settings/ConnectorsTab.tsx`
- Create: `web/src/components/connectors/ConnectorCard.tsx`

**Why:** The catalog view from spec §8. Slack card live, other three as "Coming in Slice N" placeholders to lock layout.

- [ ] **Step 1: Build the ConnectorCard component**

```typescript
// web/src/components/connectors/ConnectorCard.tsx
import { api, type ConnectorRow, type ProviderKind } from "../../lib/api";

type Catalog = {
  kind: ProviderKind | "coming-soon";
  label: string;
  letter: string;
  bg: string;
  mcpUrl?: string;
  capabilities: string[];
  preview?: boolean;
  previewNote?: string;
};

const CATALOG: Record<ProviderKind | "coming-soon-1" | "coming-soon-2" | "coming-soon-3", Catalog> = {
  slack: {
    kind: "slack", label: "Slack", letter: "S", bg: "#4A154B",
    mcpUrl: "mcp.slack.com",
    capabilities: ["Send DM", "Post in channel", "Search messages"],
  },
  atlassian: {
    kind: "atlassian", label: "Atlassian (Jira) — coming next slice", letter: "J", bg: "#0052CC",
    mcpUrl: "mcp.atlassian.com",
    capabilities: ["Create issue", "Comment", "Transition status"],
  },
  google: {
    kind: "google", label: "Google Workspace — coming next slice", letter: "G", bg: "#ea4335",
    mcpUrl: "gmailmcp.googleapis.com",
    capabilities: ["Send mail", "Draft", "Search inbox"],
  },
  microsoft: {
    kind: "microsoft", label: "Microsoft 365 — coming next slice", letter: "M", bg: "#00a4ef",
    mcpUrl: "graph.microsoft.com/mcp", preview: true,
    previewNote: "Read-only today. Send-mail and Teams DM not yet supported by Microsoft's first-party MCP.",
    capabilities: ["Search Outlook", "Search Teams", "Calendar read"],
  },
  // The three "coming-soon-N" keys are not used — placeholder shape for type completeness
  "coming-soon-1": { kind: "coming-soon", label: "", letter: "", bg: "", capabilities: [] },
  "coming-soon-2": { kind: "coming-soon", label: "", letter: "", bg: "", capabilities: [] },
  "coming-soon-3": { kind: "coming-soon", label: "", letter: "", bg: "", capabilities: [] },
};

export function ConnectorCard({
  kind, connector, onChange,
}: {
  kind: ProviderKind;
  connector: ConnectorRow | undefined;
  onChange: () => void;
}) {
  const cfg = CATALOG[kind];
  const connected = connector?.status === "active";
  const live = kind === "slack"; // Slice 1: only Slack is live

  async function connect() {
    if (!live) return;
    const { authorize_url } = await api.initiateConnectorOAuth(kind);
    window.location.href = authorize_url;
  }

  async function disconnect() {
    if (!connector) return;
    if (!window.confirm(`Disconnect ${cfg.label}?`)) return;
    await api.revokeConnector(connector.conn_id);
    onChange();
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="flex items-center gap-3 mb-3">
        <div
          className="w-9 h-9 rounded-md flex items-center justify-center text-white text-sm font-bold"
          style={{ background: cfg.bg }}
        >
          {cfg.letter}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="font-semibold text-[15px]">{cfg.label}</span>
          {cfg.preview && (
            <span className="text-[10px] font-semibold uppercase tracking-wide bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
              Preview
            </span>
          )}
          {cfg.mcpUrl && (
            <span title={`MCP endpoint: ${cfg.mcpUrl}`} className="text-neutral-400 cursor-help text-xs">
              ⓘ
            </span>
          )}
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-1.5">
        {cfg.capabilities.map(c => (
          <span key={c} className="text-[11px] bg-neutral-100 text-neutral-600 px-2 py-0.5 rounded">{c}</span>
        ))}
      </div>

      {cfg.previewNote && (
        <p className="text-[11px] text-amber-700 mb-3">{cfg.previewNote}</p>
      )}

      <div className="flex justify-between items-center pt-3 border-t border-neutral-100">
        <div className="text-[13px] flex items-center">
          <span
            className={
              "inline-block w-2 h-2 rounded-full mr-2 " +
              (connected ? "bg-emerald-500" : "bg-neutral-300")
            }
          />
          <span className="text-neutral-600">
            {connected
              ? <>Connected{connector?.vendor_workspace_id ? ` · ${connector.vendor_workspace_id}` : ""}</>
              : live ? "Not connected" : "Coming in a later slice"}
          </span>
        </div>
        {live && (connected ? (
          <button
            onClick={disconnect}
            className="text-[13px] text-red-600 border border-red-200 rounded-md px-3 py-1.5"
          >
            Disconnect
          </button>
        ) : (
          <button
            onClick={connect}
            className="text-[13px] bg-neutral-900 text-white rounded-md px-3 py-1.5"
          >
            Connect {cfg.label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire the grid in ConnectorsTab**

```typescript
// web/src/routes/Settings/ConnectorsTab.tsx
import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useConnectors } from "../../lib/useConnectors";
import { ConnectorCard } from "../../components/connectors/ConnectorCard";

export function ConnectorsTab() {
  const { connectors, reload } = useConnectors();
  const [params, setParams] = useSearchParams();

  // ?ok=slack toast after OAuth callback redirect
  useEffect(() => {
    if (params.get("ok")) {
      reload();
      const t = setTimeout(() => {
        const next = new URLSearchParams(params);
        next.delete("ok");
        setParams(next, { replace: true });
      }, 4000);
      return () => clearTimeout(t);
    }
  }, [params, reload, setParams]);

  const byProvider = Object.fromEntries((connectors ?? []).map(c => [c.provider, c]));

  return (
    <div>
      <p className="text-sm text-neutral-600 mb-7 max-w-2xl">
        Connect productivity tools so Shasta can act on your behalf — file
        tickets, send messages, draft email — using your identity in each tool.
        Each analyst connects their own. Revoke anytime.
      </p>

      {params.get("ok") && (
        <div className="mb-5 text-[13px] bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-md px-3 py-2">
          Connected {params.get("ok")} successfully.
        </div>
      )}

      <div className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500 mb-3">
        Your connectors
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
        <ConnectorCard kind="slack"     connector={byProvider.slack}     onChange={reload} />
        <ConnectorCard kind="atlassian" connector={byProvider.atlassian} onChange={reload} />
        <ConnectorCard kind="google"    connector={byProvider.google}    onChange={reload} />
        <ConnectorCard kind="microsoft" connector={byProvider.microsoft} onChange={reload} />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Build + verify**

```bash
cd web
pnpm build
```

Expected: build succeeds, no TS errors.

- [ ] **Step 4: Deploy**

```bash
aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete
aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
```

- [ ] **Step 5: Commit**

```bash
git add web/src/routes/Settings/ConnectorsTab.tsx web/src/components/connectors/
git commit -m "feat(web): Connectors catalog grid with Slack card live

Spec §8 layout — 2-col grid, ⓘ tooltip for MCP URL, PREVIEW badge for
Microsoft 365 with read-only note. Slack card supports Connect/
Disconnect via OAuth redirect; the other three show 'Coming in a
later slice' placeholders to lock the layout."
```

---

## Phase 7 — Slack act buttons on Risks page

### Task 21: Risks page — Slack "act" buttons

**Files:**
- Create: `web/src/components/findings/ActButtons.tsx`
- Modify: `web/src/routes/Risks.tsx` (mount the buttons on finding cards)

**Why:** Spec §7 Call Site 2. Adds outbound Slack actions to finding cards. Buttons are gated on the user having an active Slack connector.

- [ ] **Step 1: Build the ActButtons component**

```typescript
// web/src/components/findings/ActButtons.tsx
import { useState } from "react";
import { useConnectors } from "../../lib/useConnectors";
import { api } from "../../lib/api";

export function ActButtons({
  finding,
}: {
  finding: { finding_id: string; title: string; resource_arn: string | null };
}) {
  const { connectors } = useConnectors();
  const [pending, setPending] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const slackOK = (connectors ?? []).some(
    c => c.provider === "slack" && c.status === "active",
  );

  async function dmOwner() {
    const channel = window.prompt(
      "Slack DM target (channel ID or @user):", "@kk",
    );
    if (!channel) return;
    const text =
      `[Shasta] ${finding.title}` +
      (finding.resource_arn ? ` — ${finding.resource_arn}` : "");
    setPending(true); setMsg(null);
    try {
      await api.callTool("slack__send_message", { channel, text });
      setMsg("Sent ✓");
    } catch (e: any) {
      setMsg(`Failed: ${e.message ?? e}`);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        disabled={!slackOK || pending}
        title={slackOK ? "Send a Slack DM about this finding" : "Connect Slack in Settings to use this"}
        onClick={dmOwner}
        className={
          "text-[12px] rounded-md px-2.5 py-1 border " +
          (slackOK
            ? "border-neutral-300 hover:bg-neutral-50"
            : "border-neutral-200 text-neutral-400 cursor-not-allowed")
        }
      >
        DM via Slack
      </button>
      {msg && <span className="text-[11px] text-neutral-500">{msg}</span>}
    </div>
  );
}
```

- [ ] **Step 2: Mount in Risks.tsx**

Read the existing finding-card render in `web/src/routes/Risks.tsx` and add `<ActButtons finding={...} />` to the card footer area. Pass `{finding_id, title, resource_arn}` from whatever finding object the page already has in scope.

- [ ] **Step 3: Build + deploy**

```bash
cd web
pnpm build
aws s3 sync dist/ s3://<WEB_BUCKET>/ --delete
aws cloudfront create-invalidation --distribution-id <CLOUDFRONT_DIST_ID> --paths '/*'
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/findings/ActButtons.tsx web/src/routes/Risks.tsx
git commit -m "feat(web): Slack 'DM owner' button on Risks finding cards

Disabled+tooltip when user hasn't connected Slack; live POST to
/v1/tools/slack__send_message with the analyst's per-user token when
they have. First instance of the act-buttons pattern Slice 2+ will
extend with channel-post and per-vendor variants."
```

---

## Phase 8 — End-to-end smoke + docs

### Task 22: End-to-end manual smoke test

**Files:** No code; manual checklist only.

**Why:** Validate the full flow against the deployed environment before declaring Slice 1 done.

- [ ] **Step 1: Connect Slack from the Settings page**

1. Sign in to the web app
2. Visit `/settings?tab=connectors`
3. Slack card shows "Not connected" with a "Connect Slack" button
4. Click → redirects to slack.com OAuth → click Allow
5. Browser returns to `/settings?tab=connectors&ok=slack`
6. Success toast appears
7. Slack card now shows "Connected · T<workspace_id>" with a "Disconnect" button

Verify in DB:

```bash
aws rds-data execute-statement \
  --resource-arn "$DB_CLUSTER_ARN" --secret-arn "$DB_SECRET_ARN" \
  --database ciso_copilot \
  --sql "SELECT conn_id, oauth_provider, vendor_user_id, vendor_workspace_id, status FROM user_connectors WHERE status='active'"
```

Expected: one row with `oauth_provider='slack'`.

- [ ] **Step 2: Use the voice agent to DM via Slack**

Open the iOS app or web voice session. Say something like:
> "DM yourself on Slack saying hello from Shasta."

Expected: agent responds with confirmation; check your Slack — DM should appear from your own user (not from a shared shasta-bot).

- [ ] **Step 3: Use the web Risks page "DM via Slack" button**

Navigate to `/risks`, open any finding, click "DM via Slack", enter your @user. Expected: "Sent ✓" toast, message appears in Slack.

- [ ] **Step 4: Disconnect and verify graceful failure**

Click Disconnect on the Slack card. Voice agent now hits "DM yourself on Slack" again. Expected: agent paraphrases "Looks like you haven't connected Slack — set it up in Settings."

- [ ] **Step 5: Reconnect, force token expiry, verify JIT refresh**

Reconnect Slack. Manually force expiry:

```bash
aws rds-data execute-statement \
  --resource-arn "$DB_CLUSTER_ARN" --secret-arn "$DB_SECRET_ARN" \
  --database ciso_copilot \
  --sql "UPDATE user_connectors SET access_expires_at = now() - interval '1 minute' WHERE oauth_provider='slack' AND status='active'"
```

Make a voice or button call. Expected: JIT refresh kicks in (look in CloudWatch Logs for `connectors` Lambda — no refresh; this is the `tools` + `mcp_oauth.session` path). Action succeeds.

Confirm `access_expires_at` advanced:

```bash
aws rds-data execute-statement \
  --resource-arn "$DB_CLUSTER_ARN" --secret-arn "$DB_SECRET_ARN" \
  --database ciso_copilot \
  --sql "SELECT access_expires_at, last_used_at FROM user_connectors WHERE oauth_provider='slack' AND status='active'"
```

Expected: `access_expires_at` is ~12h in the future; `last_used_at` is recent.

- [ ] **Step 6: Commit the manual-test results to a smoke log**

Append a short note to `HANDOFF.md` under a new "MCP Connectors Slice 1" section: date, what was tested, what passed, any deferred issues. (No new file.)

```bash
git add HANDOFF.md
git commit -m "docs: HANDOFF — MCP Connectors Slice 1 smoke test results"
```

---

### Task 23: Update HANDOFF.md + push the slice as a PR

**Files:**
- Modify: `HANDOFF.md`

**Why:** Capture state so the next session can pick up Slice 2 with full context.

- [ ] **Step 1: Add a Slice 1 section to HANDOFF**

Insert a new "MCP Connectors Slice 1 — shipped" section at the top of HANDOFF.md (right after the wow-demo section), mirroring the format of other "shipped" sections in that file:

- Date: 2026-05-28 (or whatever the actual ship date is)
- PR: link to be filled in after `gh pr create`
- What's live end-to-end (4 bullets max: tables, mcp_oauth package, connectors Lambda, Settings page + Slack card + ActButtons)
- Known follow-ups (Slice 2 = autonomous broadcast; Slice 3 = Atlassian; Slack OAuth app must have rotating tokens enabled in Slack admin)
- Schema additions worth folding into CLAUDE.md (e.g., "`user_connectors.access_token_enc` is Fernet-encrypted bytea; KMS data key cached in Lambda memory per cold start")

- [ ] **Step 2: Push the slice branch + open PR**

```bash
git push -u origin feat/mcp-connectors-slice-1
gh pr create --title "feat: MCP Connectors Slice 1 — infra + Slack per-user OAuth" --body "$(cat <<'EOF'
## Summary

Slice 1 of the MCP Connectors sub-project — load-bearing infrastructure plus Slack per-user OAuth, end-to-end:

- `user_connectors` + `tenant_bot_connectors` tables (KMS-encrypted token bytea)
- `_shared/mcp_oauth/` package — providers + state JWT + PKCE + JIT refresh with advisory lock
- New `connectors` Lambda — `/v1/connectors/{connect,callback,me}` + DELETE `/{conn_id}`
- Tools dispatcher gains the `kind__tool` MCP route (`slack__send_message`)
- `voice_session` builds the OpenAI tool registry dynamically from `mcp_oauth.discover_tools()`
- `/settings` tabbed shell, Connectors page with Slack card live, the other three vendors as locked-layout placeholders
- "DM via Slack" act button on Risks finding cards

## Test plan

Manual checklist (also captured in HANDOFF):

- [x] Connect Slack via Settings → OAuth → user_connectors row appears
- [x] Voice agent calls `slack__send_message`; DM appears from the analyst's own user
- [x] Risks-page "DM via Slack" button works when connected; disabled+tooltip when not
- [x] Disconnect → voice agent gracefully reports "not connected"
- [x] Force expiry; JIT refresh kicks in; action succeeds; `access_expires_at` advances

## Spec / plan refs

- Spec: docs/superpowers/specs/2026-05-28-mcp-connectors-design.md
- Plan: docs/superpowers/plans/2026-05-28-mcp-connectors-slice-1.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Capture the PR URL into HANDOFF**

```bash
PR_URL=$(gh pr view --json url -q .url)
# Edit HANDOFF.md to drop PR_URL into the new section. Then:
git add HANDOFF.md
git commit -m "docs: HANDOFF — link Slice 1 PR"
git push
```

---

## Self-Review checklist (run after writing this plan; fix inline)

- [ ] **Spec coverage**: every section in the spec maps to a task in this plan (§4 architecture → tasks 1–17; §5 data model → task 1; §6 OAuth flow → tasks 5–12; §7 runtime → tasks 8, 16, 17; §8 web UI → tasks 18–21; §10 slicing this IS Slice 1; §11 testing → tasks have TDD steps + task 22 E2E)
- [ ] **No placeholders**: search the plan for "TBD", "TODO", "implement later", "similar to" — none present
- [ ] **Type consistency**: `ProviderKind`, `ConnectorMissingError`, `encrypt_token` / `decrypt_token`, `get_session` / `discover_tools`, `_db()` — names match across tasks
- [ ] **Exact file paths**: every "Files:" block uses absolute repo-rooted paths
- [ ] **Complete code in every step**: code steps show the actual code, not summaries
- [ ] **Exact commands**: bash blocks include the exact commands + expected output

If anything fails the checklist, fix inline. After fixes — present the execution options.
