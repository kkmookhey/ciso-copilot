# AI Security Slice 1a — GitHub App + Repo Picker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first vertical mini-slice of AI-security capabilities — a customer can install the CISO Copilot GitHub App from the web app and land on a paginated list of their authorized repositories. No scanner yet; that's 1b.

**Architecture:** One new Python Lambda `ai_github` handles all five GitHub endpoints with internal routing (matches the `policies/` and `trust/` Lambda patterns). Stdlib HS256 for state JWTs (matches `post_confirmation/main.py:269`). PyJWT[crypto] for GitHub App RS256 JWTs (installed via CDK bundling). Aurora Data API for DB writes. New `ai_connections` table (plus the rest of the AI schema, prepped for 1b/1c). Web app gets a "Connect GitHub" card on `ConnectClouds.tsx`, a `/ai/install/callback` route, and a `/ai/connections/:id/repos` route.

**Tech Stack:** Python 3.12, boto3, PyJWT[crypto] 2.10.1, AWS CDK (TypeScript), Vite + React + TypeScript + Tailwind, Aurora Postgres via Data API, Cognito JWT for end-user auth, AWS Secrets Manager for both the GitHub App private key and the state-JWT signing key.

**Spec:** `docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md` §6 (data model) and §8 (1a scope).

---

## Prerequisites (one-time, manual — do BEFORE Task 1)

These steps are not coded; they are clicked through on `github.com` and `console.aws.amazon.com`. They produce two Secrets Manager secrets the Lambda depends on.

### P1. Register the GitHub App

1. Go to `https://github.com/settings/apps/new` (you, KK, while signed in as `kkmookhey`).
2. Fill in:
   - **GitHub App name:** `CISO Copilot`
   - **Homepage URL:** `https://app.settlingforless.com`
   - **Callback URL:** *(leave blank — we use a Setup URL instead)*
   - **Setup URL (optional):** `https://app.settlingforless.com/ai/install/callback`
   - **Redirect on update:** checked
   - **Webhook → Active:** unchecked (no webhooks in 1a)
3. **Repository permissions:**
   - Contents: Read-only
   - Metadata: Read-only (mandatory)
   - Actions: Read-only
   - Pull requests: Read-only
   - Workflows: Read-only
4. **Account permissions:**
   - Email addresses: Read-only
5. **Where can this GitHub App be installed?** → "Any account."
6. Click **Create GitHub App**.
7. On the resulting page, copy down:
   - **App ID** (numeric, e.g. `123456`)
   - **Client ID** (string, e.g. `Iv1.abc123...`)
   - **Client secret** → click "Generate a new client secret" and copy
   - **Private key** → click "Generate a private key" → downloads a `.pem` file

### P2. Store credentials in AWS Secrets Manager

Run locally (with AWS creds for the dev account, `470226123496`):

```bash
PEM_PATH="$HOME/Downloads/ciso-copilot.2026-05-18.private-key.pem"  # edit
APP_ID="123456"          # paste from P1
CLIENT_ID="Iv1.abc123"   # paste from P1
CLIENT_SECRET="..."      # paste from P1

aws secretsmanager create-secret \
  --name ciso-copilot/github-app/credentials \
  --description "CISO Copilot GitHub App private key + client credentials" \
  --secret-string "$(jq -n \
    --arg app_id "$APP_ID" \
    --arg client_id "$CLIENT_ID" \
    --arg client_secret "$CLIENT_SECRET" \
    --rawfile private_key "$PEM_PATH" \
    '{app_id:$app_id, client_id:$client_id, client_secret:$client_secret, private_key:$private_key}')" \
  --region us-east-1

# State JWT signing key (random 32 bytes, base64)
aws secretsmanager create-secret \
  --name ciso-copilot/state-jwt-signing-key \
  --description "HS256 signing key for short-lived state JWTs (GitHub App install flow)" \
  --secret-string "$(openssl rand -base64 32)" \
  --region us-east-1
```

Verify:

```bash
aws secretsmanager list-secrets --filters Key=name,Values=ciso-copilot/github-app \
  --query 'SecretList[].Name' --region us-east-1
aws secretsmanager list-secrets --filters Key=name,Values=ciso-copilot/state-jwt \
  --query 'SecretList[].Name' --region us-east-1
```

Expected: both names appear.

### P3. Note the App's install URL

The GitHub App's install URL is `https://github.com/apps/<app-slug>/installations/new`, where `<app-slug>` is the URL-safe version of the App name (e.g. `ciso-copilot`). Find it on the App's settings page → "Public link." Record it for Task 7.

---

## File structure

Files created or modified by this plan, with responsibilities:

```
platform/
  sql/
    004_phase_ai.sql                              [CREATE] full Slice-1 schema (ai_connections, ai_assets, ai_relationships, ai_scans, findings.evidence_packet)
  lambda/
    ai_github/                                    [CREATE] new directory
      main.py                                     [CREATE] handler; routes by path+method
      github_app.py                               [CREATE] GitHub App JWT + installation tokens + repo listing
      state_jwt.py                                [CREATE] stdlib HS256 state token (sign + verify)
      helpers.py                                  [CREATE] _resp, _resolve_tenant_id, _cors_headers
      requirements.txt                            [CREATE] pyjwt[crypto]==2.10.1
      tests/
        __init__.py                               [CREATE] empty
        test_state_jwt.py                         [CREATE] sign/verify round-trip, expiry, tamper
        test_github_app.py                        [CREATE] JWT minting, token caching, repo listing (mocked HTTP)
        test_handler.py                           [CREATE] integration: per-route happy + auth paths
  lib/
    api-stack.ts                                  [MODIFY] add aiGithubFn Lambda + 5 routes + Secrets + IAM
web/
  src/
    lib/
      api.ts                                      [MODIFY] add AIConnection, GitHubRepo types + 5 client methods
    routes/
      ConnectClouds.tsx                           [MODIFY] add "Connect GitHub" card alongside cloud cards
      InstallCallback.tsx                         [CREATE] /ai/install/callback
      RepoPicker.tsx                              [CREATE] /ai/connections/:id/repos
    App.tsx                                       [MODIFY] register the two new routes (inside Shell)
```

---

## Tasks

### Task 1: SQL migration — full Slice 1 schema

**Files:**
- Create: `platform/sql/004_phase_ai.sql`

**Why now:** Slice 1a only needs `ai_connections`, but shipping the full migration in one go avoids a second migration in 1b. Tables sitting empty are fine.

- [ ] **Step 1: Write the migration**

```sql
-- platform/sql/004_phase_ai.sql
-- AI-security schema for Slice 1 (1a + 1b + 1c).
-- Adds: ai_connections, ai_assets, ai_relationships, ai_scans tables
--       findings.evidence_packet column
-- See: docs/superpowers/specs/2026-05-18-ai-security-slice-1-design.md §6

BEGIN;

-- 1. AI provider connections (parallel to cloud_connections)
CREATE TABLE ai_connections (
  id                      UUID         PRIMARY KEY,
  tenant_id               UUID         NOT NULL REFERENCES tenants(tenant_id),
  provider                TEXT         NOT NULL
                                       CHECK (provider IN ('github', 'openai', 'anthropic')),
  status                  TEXT         NOT NULL
                                       CHECK (status IN ('pending', 'active', 'failed', 'revoked')),
  github_installation_id  BIGINT,
  github_org_name         TEXT,
  github_account_type     TEXT,
  secret_arn              TEXT,
  external_id             TEXT,
  created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  CONSTRAINT one_provider_id_present CHECK (
    (provider = 'github' AND github_installation_id IS NOT NULL)
    OR (provider IN ('openai', 'anthropic') AND secret_arn IS NOT NULL)
  ),
  UNIQUE (tenant_id, provider, github_installation_id)
);

CREATE INDEX ai_connections_tenant_idx ON ai_connections(tenant_id);

-- 2. AI entities discovered in scans (used in 1b)
CREATE TABLE ai_assets (
  id                UUID          PRIMARY KEY,
  tenant_id         UUID          NOT NULL REFERENCES tenants(tenant_id),
  connection_id     UUID          REFERENCES ai_connections(id),
  asset_type        TEXT          NOT NULL,
  name              TEXT          NOT NULL,
  source_repo_id    UUID          REFERENCES ai_assets(id),
  source_path       TEXT,
  attributes        JSONB         NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet   JSONB         NOT NULL,
  detector_id       TEXT          NOT NULL,
  detector_version  TEXT          NOT NULL,
  scan_id           UUID          NOT NULL,
  first_seen_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  last_seen_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  UNIQUE (tenant_id, asset_type, source_repo_id, source_path, name)
);

CREATE INDEX ai_assets_tenant_idx     ON ai_assets(tenant_id);
CREATE INDEX ai_assets_repo_idx       ON ai_assets(source_repo_id);
CREATE INDEX ai_assets_type_idx       ON ai_assets(asset_type);
CREATE INDEX ai_assets_connection_idx ON ai_assets(connection_id);

-- 3. Edges between AI entities (used in 1c)
CREATE TABLE ai_relationships (
  id                  UUID         PRIMARY KEY,
  tenant_id           UUID         NOT NULL REFERENCES tenants(tenant_id),
  source_asset_id     UUID         NOT NULL REFERENCES ai_assets(id) ON DELETE CASCADE,
  target_asset_id     UUID         NOT NULL REFERENCES ai_assets(id) ON DELETE CASCADE,
  relationship_type   TEXT         NOT NULL,
  attributes          JSONB        NOT NULL DEFAULT '{}'::jsonb,
  evidence_packet     JSONB        NOT NULL,
  detector_id         TEXT         NOT NULL,
  detector_version    TEXT         NOT NULL,
  scan_id             UUID         NOT NULL,
  first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  last_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (source_asset_id, target_asset_id, relationship_type)
);

CREATE INDEX ai_rel_tenant_idx ON ai_relationships(tenant_id);
CREATE INDEX ai_rel_source_idx ON ai_relationships(source_asset_id);
CREATE INDEX ai_rel_target_idx ON ai_relationships(target_asset_id);

-- 4. Scan lifecycle (used in 1b)
CREATE TABLE ai_scans (
  id                                UUID          PRIMARY KEY,
  tenant_id                         UUID          NOT NULL REFERENCES tenants(tenant_id),
  connection_id                     UUID          NOT NULL REFERENCES ai_connections(id),
  repo_asset_id                     UUID          NOT NULL REFERENCES ai_assets(id),
  status                            TEXT          NOT NULL
                                                  CHECK (status IN ('queued', 'running', 'success', 'failed')),
  started_at                        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
  completed_at                      TIMESTAMPTZ,
  error_message                     TEXT,
  assets_discovered_count           INT           NOT NULL DEFAULT 0,
  relationships_discovered_count    INT           NOT NULL DEFAULT 0,
  findings_generated_count          INT           NOT NULL DEFAULT 0,
  scanner_version                   TEXT          NOT NULL
);

CREATE INDEX ai_scans_tenant_idx ON ai_scans(tenant_id);
CREATE INDEX ai_scans_repo_idx   ON ai_scans(repo_asset_id);
CREATE INDEX ai_scans_status_idx ON ai_scans(status);

-- 5. Add evidence_packet column to existing findings table (populated by AI scanner in 1b)
ALTER TABLE findings ADD COLUMN evidence_packet JSONB;

COMMIT;
```

- [ ] **Step 2: Apply the migration via Data API**

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "$(cat platform/sql/004_phase_ai.sql)" \
  --region us-east-1
```

Expected: returns `{"numberOfRecordsUpdated": 0}` with no errors. (Data API splits on `;` so the BEGIN/COMMIT pair runs as one batch.)

- [ ] **Step 3: Verify tables exist**

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'ai_%' ORDER BY table_name" \
  --region us-east-1
```

Expected: 4 rows — `ai_assets`, `ai_connections`, `ai_relationships`, `ai_scans`.

Also verify the new findings column:

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT column_name FROM information_schema.columns WHERE table_name='findings' AND column_name='evidence_packet'" \
  --region us-east-1
```

Expected: 1 row — `evidence_packet`.

- [ ] **Step 4: Commit**

```bash
git add platform/sql/004_phase_ai.sql
git commit -m "feat(platform): SQL — Phase AI schema (ai_connections, ai_assets, ai_relationships, ai_scans)"
```

---

### Task 2: helpers.py — shared response + tenant resolution

**Files:**
- Create: `platform/lambda/ai_github/helpers.py`
- Create: `platform/lambda/ai_github/__init__.py` (empty)
- Create: `platform/lambda/ai_github/tests/__init__.py` (empty)

**Why:** Match the `_resp` / `_resolve_tenant_id` pattern from `onboarding_aws_initiate/main.py`. Single Lambda, so the helpers live in one place and the handler imports them.

- [ ] **Step 1: Create directory layout + test conftest**

```bash
mkdir -p platform/lambda/ai_github/tests
: > platform/lambda/ai_github/tests/__init__.py
```

Do **not** create `platform/lambda/ai_github/__init__.py` — at runtime, AWS Lambda places the function root on `sys.path` and modules are imported by bare name (`import helpers`). To match this in tests, write a conftest that injects the Lambda directory onto `sys.path`:

```python
# platform/lambda/ai_github/tests/conftest.py
"""Make modules inside ai_github/ importable by bare name in tests.

AWS Lambda's runtime auto-puts the function root on sys.path, so the
handler does `import helpers, state_jwt, github_app`. Tests mirror that.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 2: Write `helpers.py`**

```python
# platform/lambda/ai_github/helpers.py
"""Shared response + tenant-resolution helpers for the ai_github Lambda."""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]

rds_data = boto3.client("rds-data")


def resp(status: int, body: dict[str, Any]) -> dict[str, Any]:
    """API Gateway proxy response with the standard CORS headers."""
    return {
        "statusCode": status,
        "headers": {
            "content-type":                "application/json",
            "access-control-allow-origin": "*",
        },
        "body": json.dumps(body),
    }


def resolve_tenant_id(event: dict) -> str | None:
    """Look up tenant_id from the Cognito JWT in the API Gateway event."""
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    sso_subject = _subject_from_claims(claims)
    if not sso_subject:
        return None
    rs = rds_data.execute_statement(
        resourceArn=DB_CLUSTER_ARN,
        secretArn=DB_SECRET_ARN,
        database=DB_NAME,
        sql="SELECT tenant_id::text FROM users WHERE sso_subject = :s LIMIT 1",
        parameters=[{"name": "s", "value": {"stringValue": sso_subject}}],
    )
    rows = rs.get("records", [])
    return rows[0][0].get("stringValue") if rows else None


def _subject_from_claims(claims: dict) -> str | None:
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
```

- [ ] **Step 3: Commit**

```bash
git add platform/lambda/ai_github/
git commit -m "feat(platform): scaffold ai_github Lambda with response + tenant helpers"
```

---

### Task 3: state_jwt.py — stdlib HS256 sign/verify

**Files:**
- Create: `platform/lambda/ai_github/state_jwt.py`
- Create: `platform/lambda/ai_github/tests/test_state_jwt.py`

Behavior contract:
- `sign(payload, ttl_seconds)` → returns a compact `header.payload.sig` JWT string
- `verify(token)` → returns the decoded payload dict, or raises `ValueError` on bad signature / expired / malformed
- Signing key: pulled from `STATE_JWT_SECRET_ARN` env var (Secrets Manager), cached after first fetch

- [ ] **Step 1: Write the failing tests**

```python
# platform/lambda/ai_github/tests/test_state_jwt.py
"""Tests for the stdlib HS256 state-JWT module."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

# Stub Secrets Manager BEFORE importing state_jwt so it can't reach AWS at import time.
@pytest.fixture(autouse=True)
def stub_secrets(monkeypatch):
    monkeypatch.setenv("STATE_JWT_SECRET_ARN", "arn:fake")
    # patch boto3 client used in state_jwt
    import boto3
    class _FakeSm:
        def get_secret_value(self, SecretId): return {"SecretString": "test-signing-key-not-secret"}
    monkeypatch.setattr(boto3, "client", lambda _name, **_kw: _FakeSm())
    # invalidate the in-module cache between tests
    import state_jwt as sj
    sj._signing_key_cache = None
    yield


def test_sign_and_verify_round_trip():
    import state_jwt as sj
    token = sj.sign({"tenant_id": "abc", "user_id": "u1"}, ttl_seconds=300)
    payload = sj.verify(token)
    assert payload["tenant_id"] == "abc"
    assert payload["user_id"] == "u1"
    assert "exp" in payload
    assert "nonce" in payload


def test_verify_rejects_tampered_signature():
    import state_jwt as sj
    token = sj.sign({"tenant_id": "abc"}, ttl_seconds=300)
    # flip the last char of the signature segment
    h, p, s = token.split(".")
    bad_s = s[:-1] + ("A" if s[-1] != "A" else "B")
    with pytest.raises(ValueError, match="signature"):
        sj.verify(f"{h}.{p}.{bad_s}")


def test_verify_rejects_expired_token():
    import state_jwt as sj
    token = sj.sign({"tenant_id": "abc"}, ttl_seconds=-1)  # already expired
    with pytest.raises(ValueError, match="expired"):
        sj.verify(token)


def test_verify_rejects_malformed_token():
    import state_jwt as sj
    with pytest.raises(ValueError):
        sj.verify("not.a.jwt.too.many.parts")
    with pytest.raises(ValueError):
        sj.verify("only-one-part")
```

- [ ] **Step 2: Run the tests — they must fail**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_state_jwt.py -v
```

Expected: ImportError or ModuleNotFoundError ("No module named 'platform.lambda.ai_github.state_jwt'").

- [ ] **Step 3: Implement `state_jwt.py`**

```python
# platform/lambda/ai_github/state_jwt.py
"""Stdlib HS256 short-lived state JWTs for the GitHub App install flow.

Matches the pattern in lambda/post_confirmation/main.py:269 to avoid a
PyJWT dependency on a Lambda that only needs HMAC.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import boto3

STATE_JWT_SECRET_ARN = os.environ["STATE_JWT_SECRET_ARN"]

_sm = boto3.client("secretsmanager")
_signing_key_cache: bytes | None = None


def sign(payload: dict, ttl_seconds: int) -> str:
    """Return a compact `<header>.<payload>.<sig>` JWT."""
    now = int(time.time())
    full_payload = {
        **payload,
        "iat":   now,
        "exp":   now + ttl_seconds,
        "nonce": secrets.token_urlsafe(16),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    h = _b64url(json.dumps(header,       separators=(",", ":")).encode())
    p = _b64url(json.dumps(full_payload, separators=(",", ":")).encode())
    sig = hmac.new(_signing_key(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url(sig)}"


def verify(token: str) -> dict:
    """Return the decoded payload, or raise ValueError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    h, p, s = parts
    expected = hmac.new(_signing_key(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url(expected), s):
        raise ValueError("bad signature")
    try:
        payload = json.loads(_b64url_decode(p))
    except (ValueError, TypeError):
        raise ValueError("malformed payload")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("token expired")
    return payload


def _signing_key() -> bytes:
    global _signing_key_cache
    if _signing_key_cache is None:
        v = _sm.get_secret_value(SecretId=STATE_JWT_SECRET_ARN)
        _signing_key_cache = v["SecretString"].encode()
    return _signing_key_cache


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)
```

- [ ] **Step 4: Run tests — they must pass**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_state_jwt.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_github/state_jwt.py platform/lambda/ai_github/tests/test_state_jwt.py
git commit -m "feat(platform): ai_github state JWT (HS256 stdlib, sign + verify)"
```

---

### Task 4: github_app.py — App-level JWT minting (RS256)

**Files:**
- Create: `platform/lambda/ai_github/github_app.py`
- Create: `platform/lambda/ai_github/tests/test_github_app.py`
- Create: `platform/lambda/ai_github/requirements.txt`

GitHub App auth is RS256 with the App's private key. PyJWT[crypto] handles this; stdlib does not have RSA. We bundle the dep into the Lambda zip via CDK bundling (Task 12). For local tests, install into a venv.

- [ ] **Step 1: Write `requirements.txt`**

```
# platform/lambda/ai_github/requirements.txt
pyjwt[crypto]==2.10.1
```

- [ ] **Step 2: Install deps locally for tests**

```bash
cd platform && python -m pip install -r lambda/ai_github/requirements.txt pytest
```

- [ ] **Step 3: Write the failing test for App JWT minting**

```python
# platform/lambda/ai_github/tests/test_github_app.py
"""Tests for the GitHub App client."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest


# Test private key — never used for real signing. Generated once via:
#   openssl genrsa -out /tmp/test.pem 2048
# Inlined to keep tests hermetic.
TEST_PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAv5KEUSPN2pODcoTUuZbZqx3uMRzpbWNijIH1cmbqB12hSWtj
... (the test will fail on import until you generate one; see Step 5)
-----END RSA PRIVATE KEY-----
"""


@pytest.fixture(autouse=True)
def stub_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:fake")
    import boto3
    class _FakeSm:
        def get_secret_value(self, SecretId):
            return {"SecretString": json.dumps({
                "app_id":        "123456",
                "client_id":     "Iv1.testclient",
                "client_secret": "test-secret",
                "private_key":   TEST_PRIVATE_KEY,
            })}
    monkeypatch.setattr(boto3, "client", lambda _name, **_kw: _FakeSm())
    import github_app as ga
    ga._credentials_cache = None
    ga._installation_token_cache.clear()
    yield


def test_mint_app_jwt_returns_valid_rs256_token():
    import jwt as pyjwt
    import github_app as ga
    token = ga.mint_app_jwt()
    # decode without verification to inspect claims
    decoded = pyjwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == "Iv1.testclient"
    assert "iat" in decoded
    assert decoded["exp"] - decoded["iat"] == 600  # 10 minute TTL


def test_get_installation_token_caches_per_installation(monkeypatch):
    import github_app as ga
    calls: list[int] = []

    def fake_post(url, headers, body):
        calls.append(1)
        # return a far-future expiry so the cache short-circuits the second call
        return 201, {"token": "ghs_abc123", "expires_at": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr(ga, "_http_post", fake_post)
    t1 = ga.get_installation_token(99999)
    t2 = ga.get_installation_token(99999)
    assert t1 == "ghs_abc123"
    assert t2 == "ghs_abc123"
    assert len(calls) == 1  # cached


def test_list_authorized_repos_returns_normalised_rows(monkeypatch):
    import github_app as ga
    monkeypatch.setattr(ga, "get_installation_token", lambda _id: "ghs_abc")

    def fake_get(url, headers):
        assert "page=1" in url and "per_page=30" in url
        return 200, {
            "total_count": 1,
            "repositories": [{
                "full_name":      "kk/foo",
                "default_branch": "main",
                "pushed_at":      "2026-05-18T10:00:00Z",
                "size":           1234,
                "language":       "Python",
                "private":        True,
            }],
        }, {}

    monkeypatch.setattr(ga, "_http_get", fake_get)
    out = ga.list_authorized_repos(installation_id=99999, page=1, per_page=30)
    assert out["repos"][0] == {
        "full_name":      "kk/foo",
        "default_branch": "main",
        "last_pushed_at": "2026-05-18T10:00:00Z",
        "size_kb":        1234,
        "primary_language": "Python",
        "is_private":     True,
    }
    assert out["next_page"] is None  # only one page


def test_list_authorized_repos_returns_next_page_marker(monkeypatch):
    import github_app as ga
    monkeypatch.setattr(ga, "get_installation_token", lambda _id: "ghs_abc")

    def fake_get(url, headers):
        return 200, {"total_count": 100, "repositories": [{"full_name": "kk/r",
            "default_branch": "main", "pushed_at": "2026-05-18T10:00:00Z",
            "size": 1, "language": None, "private": False}]}, {}

    monkeypatch.setattr(ga, "_http_get", fake_get)
    out = ga.list_authorized_repos(installation_id=99999, page=1, per_page=30)
    assert out["next_page"] == 2  # ceil(100 / 30) = 4 pages, so page 2 exists


def test_revoke_installation_returns_204(monkeypatch):
    import github_app as ga
    monkeypatch.setattr(ga, "get_installation_token", lambda _id: "ghs_abc")
    monkeypatch.setattr(ga, "_http_delete", lambda url, headers: (204, b""))
    ga.revoke_installation_token(99999)  # should not raise
```

- [ ] **Step 4: Generate a real RSA test key and paste into the test**

```bash
openssl genrsa 2048
```

Copy the entire output (`-----BEGIN…END RSA PRIVATE KEY-----`) and replace the `TEST_PRIVATE_KEY` placeholder in `test_github_app.py`. **Do NOT commit a key that's used anywhere real**; this is throwaway test material.

- [ ] **Step 5: Run tests — they must fail (no implementation yet)**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_github_app.py -v
```

Expected: ModuleNotFoundError for `github_app`.

- [ ] **Step 6: Implement `github_app.py`**

```python
# platform/lambda/ai_github/github_app.py
"""GitHub App client: App-level JWT, installation tokens, repo listing.

App-level JWT (RS256, 10min TTL) is minted with the App's private key.
Installation tokens (1hr TTL) are exchanged via POST /app/installations/
{id}/access_tokens. Tokens are cached in-process per warm Lambda
container with a 50-min TTL.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from typing import Any

import boto3
import jwt as pyjwt

GITHUB_APP_SECRET_ARN = os.environ["GITHUB_APP_SECRET_ARN"]
GITHUB_API_BASE = "https://api.github.com"

_sm = boto3.client("secretsmanager")
_credentials_cache: dict | None = None
_installation_token_cache: dict[int, tuple[str, float]] = {}  # installation_id → (token, expires_at_unix)


def credentials() -> dict:
    """{app_id, client_id, client_secret, private_key}"""
    global _credentials_cache
    if _credentials_cache is None:
        v = _sm.get_secret_value(SecretId=GITHUB_APP_SECRET_ARN)
        _credentials_cache = json.loads(v["SecretString"])
    return _credentials_cache


def mint_app_jwt() -> str:
    """RS256 JWT signed with the App's private key. 10-minute TTL."""
    c = credentials()
    now = int(time.time())
    payload = {
        "iat": now - 30,        # 30s clock-skew tolerance per GitHub recommendation
        "exp": now + 600,       # 10 minutes (max permitted by GitHub)
        "iss": c["client_id"],  # GitHub now prefers client_id over numeric app_id
    }
    return pyjwt.encode(payload, c["private_key"], algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """Return a cached or freshly-minted installation access token."""
    cached = _installation_token_cache.get(installation_id)
    now = time.time()
    if cached and cached[1] > now + 60:  # 60s safety margin
        return cached[0]

    app_jwt = mint_app_jwt()
    url = f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
    status, body = _http_post(url, headers={
        "Authorization": f"Bearer {app_jwt}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }, body=b"")
    if status != 201:
        raise RuntimeError(f"installation token mint failed: {status} {body}")
    token = body["token"]
    # parse 2026-05-18T11:00:00Z → unix
    import datetime as dt
    exp_unix = dt.datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
    _installation_token_cache[installation_id] = (token, exp_unix)
    return token


def list_authorized_repos(installation_id: int, page: int = 1, per_page: int = 30) -> dict[str, Any]:
    """Page through the installation's accessible repos and normalise the shape."""
    token = get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/installation/repositories?page={page}&per_page={per_page}"
    status, body, _ = _http_get(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if status != 200:
        raise RuntimeError(f"list repos failed: {status} {body}")
    total = body["total_count"]
    pages = math.ceil(total / per_page) if total else 0
    next_page = page + 1 if page < pages else None
    return {
        "repos": [_normalise_repo(r) for r in body["repositories"]],
        "next_page": next_page,
        "total_count": total,
    }


def revoke_installation_token(installation_id: int) -> None:
    """Revoke the current installation token (best-effort cleanup on DELETE)."""
    token = get_installation_token(installation_id)
    url = f"{GITHUB_API_BASE}/installation/token"
    status, _ = _http_delete(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
    })
    _installation_token_cache.pop(installation_id, None)
    if status not in (204, 401):  # 401 means the token was already invalid — fine
        raise RuntimeError(f"revoke token failed: {status}")


def _normalise_repo(r: dict) -> dict:
    return {
        "full_name":        r["full_name"],
        "default_branch":   r.get("default_branch"),
        "last_pushed_at":   r.get("pushed_at"),
        "size_kb":          r.get("size"),
        "primary_language": r.get("language"),
        "is_private":       r.get("private", False),
    }


def _http_get(url: str, headers: dict) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            return r.status, body, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def _http_post(url: str, headers: dict, body: bytes) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _http_delete(url: str, headers: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method="DELETE", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
```

- [ ] **Step 7: Run tests — they must pass**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_github_app.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 8: Commit**

```bash
git add platform/lambda/ai_github/github_app.py platform/lambda/ai_github/requirements.txt platform/lambda/ai_github/tests/test_github_app.py
git commit -m "feat(platform): ai_github — App JWT, installation tokens, repo listing"
```

---

### Task 5: handler — POST /v1/ai/connections/github/install_url

**Files:**
- Create: `platform/lambda/ai_github/main.py` (skeleton + this route)
- Modify: `platform/lambda/ai_github/tests/test_handler.py` (CREATE on first edit)

The handler dispatches by `(httpMethod, path)`. We'll grow it route by route through Tasks 5–9.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/ai_github/tests/test_handler.py
"""Per-route tests for the ai_github Lambda handler."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:db")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_NAME", "ciso_copilot")
    monkeypatch.setenv("STATE_JWT_SECRET_ARN", "arn:state")
    monkeypatch.setenv("GITHUB_APP_SECRET_ARN", "arn:gh")
    monkeypatch.setenv("GITHUB_APP_SLUG", "ciso-copilot")
    monkeypatch.setenv("WEB_CALLBACK_URL", "https://app.settlingforless.com/ai/install/callback")


def _event_authed(tenant_id: str, sub: str = "user-sub-1",
                  method: str = "POST", path: str = "/v1/ai/connections/github/install_url",
                  body: dict | None = None, path_params: dict | None = None,
                  query: dict | None = None) -> dict:
    return {
        "httpMethod": method,
        "path":       path,
        "body":       json.dumps(body or {}),
        "pathParameters":  path_params or {},
        "queryStringParameters": query or {},
        "requestContext": {"authorizer": {"claims": {"sub": sub}}},
    }


def test_install_url_returns_signed_github_url(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "sign", lambda payload, ttl_seconds: "stub.state.jwt")

    out = main.handler(_event_authed("tenant-1"), None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["install_url"].startswith(
        "https://github.com/apps/ciso-copilot/installations/new"
    )
    assert "state=stub.state.jwt" in body["install_url"]


def test_install_url_401_when_no_tenant(monkeypatch):
    import main, helpers
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: None)

    out = main.handler(_event_authed("tenant-x"), None)
    assert out["statusCode"] == 401
```

- [ ] **Step 2: Run test — must fail**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py -v
```

Expected: ImportError for `main`.

- [ ] **Step 3: Implement `main.py` with the first route**

```python
# platform/lambda/ai_github/main.py
"""Lambda handler for /v1/ai/connections/github/* and /v1/ai/connections/*

Routes (path, method):
  POST  /v1/ai/connections/github/install_url
  POST  /v1/ai/connections/github/complete
  GET   /v1/ai/connections
  GET   /v1/ai/connections/{id}/repos
  DELETE /v1/ai/connections/{id}
"""
from __future__ import annotations

import json
import os
import urllib.parse
import uuid

import github_app
import helpers
import state_jwt

GITHUB_APP_SLUG  = os.environ["GITHUB_APP_SLUG"]
WEB_CALLBACK_URL = os.environ["WEB_CALLBACK_URL"]
STATE_TTL_SECONDS = 300  # 5 minutes


def handler(event: dict, context) -> dict:
    method = event.get("httpMethod") or ""
    path   = event.get("path") or ""

    try:
        if method == "POST" and path == "/v1/ai/connections/github/install_url":
            return _install_url(event)
        return helpers.resp(404, {"error": "not_found", "path": path, "method": method})
    except Exception as e:  # noqa: BLE001 — top-level fence
        # Surface message; production observability already logs to CloudWatch.
        return helpers.resp(500, {"error": "internal", "detail": str(e)})


# ----------------------------------------------------------------------------
# POST /v1/ai/connections/github/install_url
# ----------------------------------------------------------------------------

def _install_url(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    user_sub = claims.get("sub") or ""

    state = state_jwt.sign(
        {"tenant_id": tenant_id, "user_sub": user_sub},
        ttl_seconds=STATE_TTL_SECONDS,
    )
    install_url = (
        f"https://github.com/apps/{GITHUB_APP_SLUG}/installations/new"
        f"?state={urllib.parse.quote(state)}"
    )
    return helpers.resp(200, {"install_url": install_url})
```

- [ ] **Step 4: Run tests — they must pass**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py -v
```

Expected: both `test_install_url_*` tests pass.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_github/main.py platform/lambda/ai_github/tests/test_handler.py
git commit -m "feat(platform): ai_github — POST /ai/connections/github/install_url"
```

---

### Task 6: handler — POST /v1/ai/connections/github/complete

**Files:**
- Modify: `platform/lambda/ai_github/main.py` (add route + helpers)
- Modify: `platform/lambda/ai_github/tests/test_handler.py` (add tests)

Flow:
1. Body `{installation_id, state}` from the web callback.
2. Verify state JWT (decode + signature + expiry).
3. Assert `state.tenant_id == caller's tenant_id` (defense against installation_id replay across tenants).
4. Call GitHub `GET /app/installations/{id}` to fetch org name + account type (also validates the installation is real).
5. Insert `ai_connections` row with `status='active'`.
6. Return `{connection_id}`.

- [ ] **Step 1: Add the failing tests**

Append to `test_handler.py`:

```python
def test_complete_inserts_row_and_returns_connection_id(monkeypatch):
    import main, helpers, state_jwt, github_app
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "verify",
                        lambda token: {"tenant_id": "tenant-1", "user_sub": "u1"})

    # GitHub /app/installations/{id} response — stub the http client
    def fake_get(url, headers):
        assert url.endswith("/app/installations/99999")
        return 200, {"account": {"login": "kkmookhey", "type": "User"}}, {}
    monkeypatch.setattr(github_app, "_http_get", fake_get)
    monkeypatch.setattr(github_app, "mint_app_jwt", lambda: "stub.jwt")

    inserts: list[dict] = []
    def fake_execute(**kw):
        inserts.append(kw)
        return {"records": []}
    monkeypatch.setattr(helpers.rds_data, "execute_statement", fake_execute)

    event = _event_authed("tenant-1", path="/v1/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    uuid.UUID(body["connection_id"])  # is a valid UUID
    # one INSERT happened
    assert any("INSERT INTO ai_connections" in c["sql"] for c in inserts)


def test_complete_rejects_state_for_other_tenant(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(state_jwt, "verify",
                        lambda token: {"tenant_id": "tenant-OTHER", "user_sub": "u1"})

    event = _event_authed("tenant-1", path="/v1/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 403


def test_complete_rejects_expired_state(monkeypatch):
    import main, helpers, state_jwt
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    def boom(_t): raise ValueError("token expired")
    monkeypatch.setattr(state_jwt, "verify", boom)

    event = _event_authed("tenant-1", path="/v1/ai/connections/github/complete",
                          body={"installation_id": 99999, "state": "stub.state"})
    out = main.handler(event, None)
    assert out["statusCode"] == 400
```

`import uuid` at the top of `test_handler.py` if not already present.

- [ ] **Step 2: Run tests — they must fail**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py -v
```

Expected: 3 new tests fail with 404 (route not yet routed).

- [ ] **Step 3: Add `_complete` to `main.py`**

Add inside the dispatch in `handler()`:

```python
        if method == "POST" and path == "/v1/ai/connections/github/complete":
            return _complete(event)
```

Then append:

```python
# ----------------------------------------------------------------------------
# POST /v1/ai/connections/github/complete
# ----------------------------------------------------------------------------

def _complete(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return helpers.resp(400, {"error": "invalid_json"})

    installation_id = body.get("installation_id")
    state           = body.get("state")
    if not isinstance(installation_id, int) or not isinstance(state, str):
        return helpers.resp(400, {"error": "missing_fields"})

    try:
        decoded = state_jwt.verify(state)
    except ValueError as e:
        return helpers.resp(400, {"error": "bad_state", "detail": str(e)})

    if decoded.get("tenant_id") != tenant_id:
        return helpers.resp(403, {"error": "tenant_mismatch"})

    # Validate the installation exists + grab org metadata.
    app_jwt = github_app.mint_app_jwt()
    status, gh_body, _ = github_app._http_get(
        f"{github_app.GITHUB_API_BASE}/app/installations/{installation_id}",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if status != 200:
        return helpers.resp(400, {"error": "installation_lookup_failed",
                                  "github_status": status})

    account     = gh_body.get("account") or {}
    org_name    = account.get("login")
    account_typ = account.get("type")  # 'User' | 'Organization'

    conn_id = str(uuid.uuid4())
    helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "INSERT INTO ai_connections "
            "  (id, tenant_id, provider, status, github_installation_id, "
            "   github_org_name, github_account_type) "
            "VALUES (CAST(:id AS UUID), CAST(:tid AS UUID), 'github', 'active', "
            "        :inst, :org, :acct) "
            "ON CONFLICT (tenant_id, provider, github_installation_id) "
            "  DO UPDATE SET status='active', github_org_name=EXCLUDED.github_org_name, "
            "                github_account_type=EXCLUDED.github_account_type, "
            "                updated_at=NOW() "
            "RETURNING id::text"
        ),
        parameters=[
            {"name": "id",   "value": {"stringValue": conn_id}},
            {"name": "tid",  "value": {"stringValue": tenant_id}},
            {"name": "inst", "value": {"longValue":   installation_id}},
            {"name": "org",  "value": {"stringValue": org_name or ""}},
            {"name": "acct", "value": {"stringValue": account_typ or ""}},
        ],
    )
    return helpers.resp(200, {"connection_id": conn_id})
```

- [ ] **Step 4: Run tests — must pass**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_github/main.py platform/lambda/ai_github/tests/test_handler.py
git commit -m "feat(platform): ai_github — POST /ai/connections/github/complete"
```

---

### Task 7: handler — GET /v1/ai/connections

**Files:**
- Modify: `platform/lambda/ai_github/main.py`
- Modify: `platform/lambda/ai_github/tests/test_handler.py`

Returns `[{id, provider, status, github_org_name, created_at}, ...]` filtered by tenant.

- [ ] **Step 1: Add the failing test**

Append:

```python
def test_list_connections_returns_tenant_rows(monkeypatch):
    import main, helpers
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")

    def fake_execute(**kw):
        assert ":tid" in kw["sql"]
        return {"records": [[
            {"stringValue": "11111111-1111-1111-1111-111111111111"},
            {"stringValue": "github"},
            {"stringValue": "active"},
            {"stringValue": "kkmookhey"},
            {"stringValue": "2026-05-18T10:00:00Z"},
        ]]}
    monkeypatch.setattr(helpers.rds_data, "execute_statement", fake_execute)

    event = _event_authed("tenant-1", method="GET", path="/v1/ai/connections")
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["connections"][0]["provider"] == "github"
    assert body["connections"][0]["github_org_name"] == "kkmookhey"
```

- [ ] **Step 2: Run — must fail**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py::test_list_connections_returns_tenant_rows -v
```

Expected: 404.

- [ ] **Step 3: Implement — add dispatch + `_list_connections`**

In `handler()`:

```python
        if method == "GET" and path == "/v1/ai/connections":
            return _list_connections(event)
```

Append:

```python
# ----------------------------------------------------------------------------
# GET /v1/ai/connections
# ----------------------------------------------------------------------------

def _list_connections(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT id::text, provider, status, COALESCE(github_org_name, ''), "
            "       to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
            "FROM ai_connections "
            "WHERE tenant_id = CAST(:tid AS UUID) AND status != 'revoked' "
            "ORDER BY created_at DESC"
        ),
        parameters=[{"name": "tid", "value": {"stringValue": tenant_id}}],
    )
    connections = [
        {
            "id":              r[0].get("stringValue"),
            "provider":        r[1].get("stringValue"),
            "status":          r[2].get("stringValue"),
            "github_org_name": r[3].get("stringValue"),
            "created_at":      r[4].get("stringValue"),
        }
        for r in rs.get("records", [])
    ]
    return helpers.resp(200, {"connections": connections})
```

- [ ] **Step 4: Run — must pass**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py -v
```

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_github/main.py platform/lambda/ai_github/tests/test_handler.py
git commit -m "feat(platform): ai_github — GET /ai/connections (list AI provider connections)"
```

---

### Task 8: handler — GET /v1/ai/connections/{id}/repos

**Files:**
- Modify: `platform/lambda/ai_github/main.py`
- Modify: `platform/lambda/ai_github/tests/test_handler.py`

Authorisation: caller's tenant must own the `ai_connections` row. Otherwise 404 (not 403 — avoid leaking existence).

- [ ] **Step 1: Add the failing tests**

Append:

```python
def test_repos_returns_paginated_list(monkeypatch):
    import main, helpers, github_app
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")

    # tenant ownership lookup: returns the installation_id
    def fake_execute(**kw):
        assert "SELECT github_installation_id" in kw["sql"]
        return {"records": [[{"longValue": 99999}]]}
    monkeypatch.setattr(helpers.rds_data, "execute_statement", fake_execute)

    monkeypatch.setattr(github_app, "list_authorized_repos",
                        lambda installation_id, page, per_page: {
                            "repos": [{"full_name": "kk/foo", "default_branch": "main",
                                       "last_pushed_at": "2026-05-18T10:00:00Z", "size_kb": 1,
                                       "primary_language": "Python", "is_private": True}],
                            "next_page": None, "total_count": 1,
                        })
    event = _event_authed("tenant-1", method="GET",
                          path="/v1/ai/connections/cid-1/repos",
                          path_params={"id": "11111111-1111-1111-1111-111111111111"})
    out = main.handler(event, None)
    assert out["statusCode"] == 200
    body = json.loads(out["body"])
    assert body["repos"][0]["full_name"] == "kk/foo"
    assert body["next_page"] is None


def test_repos_404_when_connection_not_owned_by_tenant(monkeypatch):
    import main, helpers
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")
    monkeypatch.setattr(helpers.rds_data, "execute_statement",
                        lambda **kw: {"records": []})  # no rows == not found
    event = _event_authed("tenant-1", method="GET",
                          path="/v1/ai/connections/cid-1/repos",
                          path_params={"id": "11111111-1111-1111-1111-111111111111"})
    out = main.handler(event, None)
    assert out["statusCode"] == 404
```

- [ ] **Step 2: Run — must fail**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py -v
```

Expected: new tests 404 (route not yet wired).

- [ ] **Step 3: Implement — add dispatch + `_list_repos`**

In `handler()`, add **before** the existing static-path checks:

```python
        # Path with {id} parameter
        if method == "GET" and path.startswith("/v1/ai/connections/") and path.endswith("/repos"):
            return _list_repos(event)
        if method == "DELETE" and path.startswith("/v1/ai/connections/") and not path.endswith("/repos"):
            return _delete_connection(event)
```

Append:

```python
# ----------------------------------------------------------------------------
# GET /v1/ai/connections/{id}/repos
# ----------------------------------------------------------------------------

def _list_repos(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})

    conn_id = (event.get("pathParameters") or {}).get("id")
    if not conn_id:
        return helpers.resp(400, {"error": "missing_id"})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT github_installation_id FROM ai_connections "
            "WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tid AS UUID) "
            "  AND provider = 'github' AND status = 'active'"
        ),
        parameters=[
            {"name": "id",  "value": {"stringValue": conn_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return helpers.resp(404, {"error": "not_found"})
    installation_id = rows[0][0].get("longValue")

    q = event.get("queryStringParameters") or {}
    try:
        page     = int(q.get("page", "1"))
        per_page = min(int(q.get("per_page", "30")), 100)
    except (TypeError, ValueError):
        return helpers.resp(400, {"error": "bad_pagination"})

    try:
        return helpers.resp(200, github_app.list_authorized_repos(
            installation_id=installation_id, page=page, per_page=per_page,
        ))
    except RuntimeError as e:
        # GitHub 401 → installation likely revoked
        if " 401 " in str(e):
            _mark_revoked(conn_id)
            return helpers.resp(409, {"error": "installation_revoked"})
        raise


def _mark_revoked(conn_id: str) -> None:
    helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=("UPDATE ai_connections SET status='revoked', updated_at=NOW() "
             "WHERE id = CAST(:id AS UUID)"),
        parameters=[{"name": "id", "value": {"stringValue": conn_id}}],
    )
```

- [ ] **Step 4: Run — must pass**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py -v
```

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_github/main.py platform/lambda/ai_github/tests/test_handler.py
git commit -m "feat(platform): ai_github — GET /ai/connections/{id}/repos"
```

---

### Task 9: handler — DELETE /v1/ai/connections/{id}

**Files:**
- Modify: `platform/lambda/ai_github/main.py`
- Modify: `platform/lambda/ai_github/tests/test_handler.py`

Flips `status='revoked'`. Best-effort revokes the installation token. Does **not** uninstall the GitHub App on GitHub — that's the customer's action on `github.com`.

- [ ] **Step 1: Add the failing test**

```python
def test_delete_connection_flips_status_and_revokes_token(monkeypatch):
    import main, helpers, github_app
    monkeypatch.setattr(helpers, "resolve_tenant_id", lambda e: "tenant-1")

    updates: list[dict] = []
    def fake_execute(**kw):
        updates.append(kw)
        if "SELECT github_installation_id" in kw["sql"]:
            return {"records": [[{"longValue": 99999}]]}
        return {"records": []}
    monkeypatch.setattr(helpers.rds_data, "execute_statement", fake_execute)

    revoked: list[int] = []
    monkeypatch.setattr(github_app, "revoke_installation_token",
                        lambda iid: revoked.append(iid))

    event = _event_authed("tenant-1", method="DELETE",
                          path="/v1/ai/connections/cid-1",
                          path_params={"id": "11111111-1111-1111-1111-111111111111"})
    out = main.handler(event, None)
    assert out["statusCode"] == 204
    assert revoked == [99999]
    # at least one UPDATE happened
    assert any("UPDATE ai_connections" in u["sql"] for u in updates)
```

- [ ] **Step 2: Run — must fail**

```bash
cd platform && python -m pytest lambda/ai_github/tests/test_handler.py::test_delete_connection_flips_status_and_revokes_token -v
```

- [ ] **Step 3: Implement — append `_delete_connection`**

```python
# ----------------------------------------------------------------------------
# DELETE /v1/ai/connections/{id}
# ----------------------------------------------------------------------------

def _delete_connection(event: dict) -> dict:
    tenant_id = helpers.resolve_tenant_id(event)
    if not tenant_id:
        return helpers.resp(401, {"error": "no_tenant"})
    conn_id = (event.get("pathParameters") or {}).get("id")
    if not conn_id:
        return helpers.resp(400, {"error": "missing_id"})

    rs = helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=(
            "SELECT github_installation_id FROM ai_connections "
            "WHERE id = CAST(:id AS UUID) AND tenant_id = CAST(:tid AS UUID) "
            "  AND provider = 'github'"
        ),
        parameters=[
            {"name": "id",  "value": {"stringValue": conn_id}},
            {"name": "tid", "value": {"stringValue": tenant_id}},
        ],
    )
    rows = rs.get("records", [])
    if not rows:
        return helpers.resp(404, {"error": "not_found"})
    installation_id = rows[0][0].get("longValue")

    # Best-effort token revocation; ignore errors so DELETE always proceeds.
    try:
        github_app.revoke_installation_token(installation_id)
    except Exception:  # noqa: BLE001
        pass

    helpers.rds_data.execute_statement(
        resourceArn=helpers.DB_CLUSTER_ARN,
        secretArn=helpers.DB_SECRET_ARN,
        database=helpers.DB_NAME,
        sql=("UPDATE ai_connections SET status='revoked', updated_at=NOW() "
             "WHERE id = CAST(:id AS UUID)"),
        parameters=[{"name": "id", "value": {"stringValue": conn_id}}],
    )
    return {"statusCode": 204,
            "headers": {"access-control-allow-origin": "*"},
            "body": ""}
```

- [ ] **Step 4: Run all tests — must pass**

```bash
cd platform && python -m pytest lambda/ai_github/tests/ -v
```

Expected: all tests across `test_state_jwt.py`, `test_github_app.py`, `test_handler.py` pass.

- [ ] **Step 5: Commit**

```bash
git add platform/lambda/ai_github/main.py platform/lambda/ai_github/tests/test_handler.py
git commit -m "feat(platform): ai_github — DELETE /ai/connections/{id}"
```

---

### Task 10: CDK — provision `ai_github` Lambda with bundling

**Files:**
- Modify: `platform/lib/api-stack.ts`

Bundling installs `pyjwt[crypto]` into the Lambda zip at synth time. Requires Docker on the local mac.

- [ ] **Step 1: Add the Lambda + IAM policies**

Insert in `api-stack.ts` near the other Lambda definitions (e.g. just before the `REST API + authorizer` block at line ~262):

```typescript
    // ========================================================================
    // /v1/ai/connections/github/* — GitHub App install + listing
    // ========================================================================
    const aiGithubFn = new lambda.Function(this, 'AiGithubFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ai_github'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash', '-c',
            'pip install --no-cache-dir -r requirements.txt -t /asset-output && cp -au . /asset-output',
          ],
        },
      }),
      timeout:    cdk.Duration.seconds(15),
      memorySize: 512,
      environment: {
        ...dbEnv,
        GITHUB_APP_SECRET_ARN: `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials`,
        STATE_JWT_SECRET_ARN:  `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/state-jwt-signing-key`,
        GITHUB_APP_SLUG:       'ciso-copilot',
        WEB_CALLBACK_URL:      'https://app.settlingforless.com/ai/install/callback',
      },
    });
    props.dbCluster.grantDataApiAccess(aiGithubFn);
    aiGithubFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials*`,
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/state-jwt-signing-key*`,
      ],
    }));
```

- [ ] **Step 2: Add the 5 API routes**

After the `/connections` line in the REST API block, add:

```typescript
    // /v1/ai/connections — GitHub App install + listing
    const aiRes      = api.root.addResource('ai');
    const aiConns    = aiRes.addResource('connections');
    const aiConnId   = aiConns.addResource('{id}');
    const aiGithub   = aiConns.addResource('github');

    aiConns.addMethod( 'GET',    new apigw.LambdaIntegration(aiGithubFn), authedOpts);
    aiConnId.addMethod('DELETE', new apigw.LambdaIntegration(aiGithubFn), authedOpts);
    aiConnId.addResource('repos').addMethod(
      'GET', new apigw.LambdaIntegration(aiGithubFn), authedOpts,
    );
    aiGithub.addResource('install_url').addMethod(
      'POST', new apigw.LambdaIntegration(aiGithubFn), authedOpts,
    );
    aiGithub.addResource('complete').addMethod(
      'POST', new apigw.LambdaIntegration(aiGithubFn), authedOpts,
    );
```

- [ ] **Step 3: Synth locally to verify**

```bash
cd platform && npx cdk synth CisoCopilotApi > /tmp/synth.yaml 2>&1
grep -c 'AiGithubFn' /tmp/synth.yaml
```

Expected: a non-zero count (Lambda + permission + integration references).

- [ ] **Step 4: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "feat(platform): wire ai_github Lambda + /v1/ai/connections routes"
```

---

### Task 11: Deploy the Lambda + verify endpoints respond

**Files:** none (deploy step)

- [ ] **Step 1: Deploy**

```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never
```

Expected: stack updates with `AiGithubFn` added.

- [ ] **Step 2: Smoke-test from the command line with a valid Cognito ID token**

Get an ID token (sign in to the web app, copy from devtools `localStorage.idToken`, or use the existing test helper). Then:

```bash
ID_TOKEN="eyJ..."   # paste

curl -s -H "Authorization: Bearer $ID_TOKEN" \
  -X POST -H "Content-Type: application/json" -d '{}' \
  https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1/ai/connections/github/install_url | jq .
```

Expected:

```json
{"install_url": "https://github.com/apps/ciso-copilot/installations/new?state=..."}
```

```bash
curl -s -H "Authorization: Bearer $ID_TOKEN" \
  https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1/ai/connections | jq .
```

Expected:

```json
{"connections": []}
```

- [ ] **Step 3: No commit (deploy-only step)**

---

### Task 12: web — API client additions

**Files:**
- Modify: `web/src/lib/api.ts`

Add TS types + 5 client methods.

- [ ] **Step 1: Insert types**

Find the existing type declarations in `api.ts` (after `Finding`, before the `api` object). Add:

```typescript
export interface AIConnection {
  id:              string;
  provider:        "github" | "openai" | "anthropic";
  status:          "pending" | "active" | "failed" | "revoked";
  github_org_name: string;
  created_at:      string;
}

export interface GitHubRepo {
  full_name:        string;
  default_branch:   string | null;
  last_pushed_at:   string | null;
  size_kb:          number | null;
  primary_language: string | null;
  is_private:       boolean;
}

export interface InstallUrlResponse {
  install_url: string;
}

export interface CompleteInstallResponse {
  connection_id: string;
}

export interface ListReposResponse {
  repos:       GitHubRepo[];
  next_page:   number | null;
  total_count: number;
}
```

- [ ] **Step 2: Add client methods to the `api` object**

Inside the `api = { ... }` literal:

```typescript
  async getGithubInstallUrl(): Promise<InstallUrlResponse> {
    return request<InstallUrlResponse>("/ai/connections/github/install_url", {
      method: "POST",
      body:   "{}",
    });
  },

  async completeGithubInstall(installationId: number, state: string): Promise<CompleteInstallResponse> {
    return request<CompleteInstallResponse>("/ai/connections/github/complete", {
      method: "POST",
      body:   JSON.stringify({ installation_id: installationId, state }),
    });
  },

  async listAIConnections(): Promise<{ connections: AIConnection[] }> {
    return request<{ connections: AIConnection[] }>("/ai/connections", { method: "GET" });
  },

  async listAuthorizedRepos(connectionId: string, page = 1): Promise<ListReposResponse> {
    return request<ListReposResponse>(
      `/ai/connections/${connectionId}/repos?page=${page}&per_page=30`,
      { method: "GET" },
    );
  },

  async revokeAIConnection(connectionId: string): Promise<void> {
    await request<void>(`/ai/connections/${connectionId}`, { method: "DELETE" });
  },
```

(The exact `request` helper signature is in the existing `api.ts`; copy the surrounding pattern if the calls above don't compile.)

- [ ] **Step 3: Typecheck**

```bash
cd web && pnpm typecheck
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/api.ts
git commit -m "feat(web): api.ts — AI connection + GitHub repo types and client methods"
```

---

### Task 13: web — "Connect GitHub" card on `ConnectClouds.tsx`

**Files:**
- Modify: `web/src/routes/ConnectClouds.tsx`

Pattern matches the existing 4 cloud cards.

- [ ] **Step 1: Add state + handler**

In the `useState` block at the top of the component, add:

```typescript
  const [pendingGithub, setPendingGithub] = useState(false);
  const [githubUrl, setGithubUrl] = useState<string | null>(null);
```

Add this handler alongside `connectAws` etc.:

```typescript
  async function connectGithub() {
    setPendingGithub(true); setError(null);
    try {
      const r = await api.getGithubInstallUrl();
      setGithubUrl(r.install_url);
      // Send the user straight to GitHub — no intermediate panel for now.
      window.location.href = r.install_url;
    } catch (e) { setError((e as Error).message); }
    finally { setPendingGithub(false); }
  }
```

- [ ] **Step 2: Add the card in the JSX grid**

In the grid that contains the cloud tiles (`<div className="mt-10 grid grid-cols-2 gap-4">`), append a new `CloudTile`:

```tsx
        <CloudTile name="GitHub"
                   tagline="AI inventory via the CISO Copilot GitHub App"
                   enabled={true} loading={pendingGithub} onClick={connectGithub} />
```

Optionally grow the grid to 3 cols if the layout gets too tall: change `grid-cols-2` to `lg:grid-cols-3` on the wrapping div.

- [ ] **Step 3: Typecheck**

```bash
cd web && pnpm typecheck
```

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/ConnectClouds.tsx
git commit -m "feat(web): Connect GitHub card on ConnectClouds page"
```

---

### Task 14: web — `/ai/install/callback` route (`InstallCallback.tsx`)

**Files:**
- Create: `web/src/routes/InstallCallback.tsx`

Reads `installation_id` + `state` from the URL, posts to `/ai/connections/github/complete`, redirects to repo picker on success.

- [ ] **Step 1: Write the component**

```tsx
// web/src/routes/InstallCallback.tsx
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";

export function InstallCallback() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const installationIdRaw = params.get("installation_id");
    const state             = params.get("state");
    const setupAction       = params.get("setup_action") || "";

    if (setupAction === "request") {
      // GitHub redirects here when a non-owner asks an org admin to approve.
      // We can't proceed; tell the user to wait.
      setError("Awaiting admin approval on GitHub. We'll detect the install once approved.");
      return;
    }
    if (!installationIdRaw || !state) {
      setError("Missing installation_id or state. Reopen the Connect flow on the Connect page.");
      return;
    }
    const installationId = parseInt(installationIdRaw, 10);
    if (Number.isNaN(installationId)) {
      setError("Bad installation_id.");
      return;
    }

    api.completeGithubInstall(installationId, state)
       .then(({ connection_id }) => nav(`/ai/connections/${connection_id}/repos`, { replace: true }))
       .catch((e: Error) => setError(e.message || "Install failed."));
  }, [params, nav]);

  return (
    <div className="max-w-xl mx-auto py-20 text-center">
      {error
        ? <>
            <h1 className="text-xl font-semibold text-red-700">Install error</h1>
            <p className="mt-3 text-slate-700">{error}</p>
            <a href="/connect" className="mt-6 inline-block text-blue-700 hover:underline">← Back to Connect</a>
          </>
        : <>
            <h1 className="text-xl font-semibold">Finishing GitHub install…</h1>
            <p className="mt-3 text-slate-600">Hang tight, this takes a second.</p>
          </>}
    </div>
  );
}
```

- [ ] **Step 2: Register the route**

Modify `web/src/App.tsx`. Inside the `<Route element={<Shell />}>` block (so it gets auth gating + chrome), add:

```tsx
          <Route path="/ai/install/callback" element={<InstallCallback />} />
```

Add the import at the top:

```tsx
import { InstallCallback } from "./routes/InstallCallback";
```

- [ ] **Step 3: Typecheck**

```bash
cd web && pnpm typecheck
```

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/InstallCallback.tsx web/src/App.tsx
git commit -m "feat(web): /ai/install/callback route — finalize GitHub App install"
```

---

### Task 15: web — `/ai/connections/:id/repos` route (`RepoPicker.tsx`)

**Files:**
- Create: `web/src/routes/RepoPicker.tsx`
- Modify: `web/src/App.tsx`

The "Scan" button is rendered but disabled (greyed) — it's wired in mini-slice 1b.

- [ ] **Step 1: Write the component**

```tsx
// web/src/routes/RepoPicker.tsx
import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, type GitHubRepo } from "../lib/api";

export function RepoPicker() {
  const { id } = useParams<{ id: string }>();
  const [repos,    setRepos]    = useState<GitHubRepo[] | null>(null);
  const [page,     setPage]     = useState(1);
  const [nextPage, setNextPage] = useState<number | null>(null);
  const [total,    setTotal]    = useState<number | null>(null);
  const [error,    setError]    = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setRepos(null); setError(null);
    api.listAuthorizedRepos(id, page)
       .then((r) => { setRepos(r.repos); setNextPage(r.next_page); setTotal(r.total_count); })
       .catch((e: Error) => setError(e.message));
  }, [id, page]);

  if (!id) return <div>Missing connection id.</div>;

  return (
    <div className="max-w-5xl">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Choose repositories to scan</h1>
          <p className="text-slate-600 mt-1">
            {total !== null ? `${total} authorized.` : "Loading…"}{" "}
            Scanning is per-repo — pick the AI-bearing ones first.
          </p>
        </div>
        <Link to="/connect" className="text-sm text-slate-600 hover:underline">← Back to Connect</Link>
      </div>

      {error && (
        <div className="mt-6 p-4 rounded-lg bg-red-50 border border-red-200 text-red-800 text-sm">
          {error}
        </div>
      )}

      <div className="mt-8 rounded-2xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 text-xs uppercase tracking-wide">
            <tr>
              <th className="px-4 py-3 text-left">Repository</th>
              <th className="px-4 py-3 text-left">Language</th>
              <th className="px-4 py-3 text-left">Last push</th>
              <th className="px-4 py-3 text-left">Visibility</th>
              <th className="px-4 py-3 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {repos === null && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-slate-500">Loading…</td></tr>
            )}
            {repos?.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-slate-500">
                No repositories authorized. Add some on the GitHub App settings page.
              </td></tr>
            )}
            {repos?.map((r) => (
              <tr key={r.full_name} className="border-t border-slate-100">
                <td className="px-4 py-3">
                  <a className="text-blue-700 hover:underline"
                     href={`https://github.com/${r.full_name}`} target="_blank" rel="noopener noreferrer">
                    {r.full_name}
                  </a>
                </td>
                <td className="px-4 py-3 text-slate-700">{r.primary_language ?? "—"}</td>
                <td className="px-4 py-3 text-slate-700">{formatDate(r.last_pushed_at)}</td>
                <td className="px-4 py-3 text-slate-700">{r.is_private ? "Private" : "Public"}</td>
                <td className="px-4 py-3 text-right">
                  <button disabled
                          title="Scanning will be enabled in the next release."
                          className="px-3 py-1.5 rounded-md bg-slate-100 text-slate-400 text-xs cursor-not-allowed">
                    Scan
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 flex items-center gap-3 text-sm">
        <button onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
          ← Prev
        </button>
        <span className="text-slate-600">Page {page}</span>
        <button onClick={() => setPage((p) => p + 1)}
                disabled={nextPage === null}
                className="px-3 py-1.5 rounded-md bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
          Next →
        </button>
      </div>
    </div>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}
```

- [ ] **Step 2: Register the route in `App.tsx`**

Add the import:

```tsx
import { RepoPicker } from "./routes/RepoPicker";
```

Inside the `<Route element={<Shell />}>` block:

```tsx
          <Route path="/ai/connections/:id/repos" element={<RepoPicker />} />
```

- [ ] **Step 3: Typecheck + build**

```bash
cd web && pnpm typecheck && pnpm build
```

Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add web/src/routes/RepoPicker.tsx web/src/App.tsx
git commit -m "feat(web): /ai/connections/:id/repos repo picker (Scan disabled until 1b)"
```

---

### Task 16: Deploy web + end-to-end manual verification

**Files:** none (deploy + manual test)

- [ ] **Step 1: Deploy web**

```bash
cd web && pnpm build
aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
```

Expected: sync succeeds, invalidation in progress.

- [ ] **Step 2: Manual E2E test (the demo)**

In a browser, on a clean profile:

1. Go to `https://app.settlingforless.com/`.
2. Sign in with your Google identity (`kkmookhey@gmail.com` should land tenant-approved).
3. Click **Connect clouds** in the nav.
4. Click the new **GitHub** card.
5. Browser redirects to `https://github.com/apps/ciso-copilot/installations/new?state=…`.
6. On the GitHub install page, choose your `kkmookhey` user.
7. Pick **Only select repositories** and select 3 repos with real AI code (e.g. anything that imports `openai`, `anthropic`, `langchain`).
8. Click **Install**.
9. GitHub redirects to `https://app.settlingforless.com/ai/install/callback?installation_id=…&state=…&setup_action=install`.
10. After a brief "Finishing GitHub install…" screen, the URL changes to `/ai/connections/<uuid>/repos`.
11. The selected 3 repos appear in the table with their last-push dates and language.
12. The **Scan** buttons are visible but greyed out — that's expected (1b enables them).

- [ ] **Step 3: Verify the DB row**

```bash
aws rds-data execute-statement \
  --resource-arn arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh \
  --secret-arn arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp \
  --database ciso_copilot \
  --sql "SELECT id, tenant_id, provider, status, github_installation_id, github_org_name FROM ai_connections" \
  --region us-east-1
```

Expected: exactly one row with `provider='github'`, `status='active'`, a real installation_id, and your GitHub login as `github_org_name`.

- [ ] **Step 4: No commit (verification step)**

---

## Out of scope for 1a (do NOT add here)

- The scanner Lambda (`ai_scanner/`) — 1b.
- Detector code — 1b.
- AI Inventory tab on web/iOS — 1b.
- Trust graph viz — 1c.
- AI Risks tab — 1c.
- Webhook receiver for `installation` / `installation_repositories` / `push` events — later slice.
- KMS signing on evidence packets — later slice.

If during execution you discover any of the above is "needed" to finish 1a, **stop and surface it** rather than scope-creeping.

---

## Self-review checklist (run before declaring this plan complete)

- All 5 endpoints from spec §8.4 have a handler task: ✅ (Tasks 5, 6, 7, 8, 9).
- Web UI from spec §8.5 covered: ✅ (Tasks 13, 14, 15).
- Database migration aligned with spec §6: ✅ (Task 1 ships the full schema).
- Prerequisites documented: ✅ (P1, P2, P3).
- Each step has executable code/commands, no placeholders.
- The `Scan` button is disabled (1a doesn't ship scanning; that's 1b's deliverable).
