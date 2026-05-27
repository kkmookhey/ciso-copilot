# Secrets / Hardcoded-Identifier Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract every per-deployment identifier (AWS account ID, API Gateway URL, ARNs, admin emails) from source code into env-var configuration, so the repo becomes operator-agnostic and ready for the MIT-public flip.

**Architecture:** Three env files — `platform/.env` (CDK + Lambdas), `web/.env.production` (Vite), `ios/Local.xcconfig` (Xcode) — all gitignored with committed `.example` companions. A `required()`/`requireEnv()` boundary throws on missing values. Server emits `is_admin` on `/me`; web drops its duplicate allowlist. Customer-facing CFN templates lose hardcoded `Default:` values; the onboarding Lambda already passes explicit parameters.

**Tech Stack:** AWS CDK (TypeScript) + dotenv; Python 3.12 Lambdas; Vite + React + TypeScript; Xcode 15 + xcconfig + Info.plist substitution.

**Spec:** `docs/superpowers/specs/2026-05-26-secrets-extraction-design.md`

---

## File Structure

### New files
- `platform/.env.example` — augment existing file (already has `AWS_REGION`, `DOMAIN`, `APPROVAL_RECIPIENT`, `ENTRA_*`, `GOOGLE_*`)
- `web/.env.example` — committed placeholder
- `web/.env.production` — gitignored, KK's real values
- `web/src/lib/env.ts` — `requireEnv()` for Vite `VITE_*` vars
- `ios/Local.xcconfig.example` — committed placeholder
- `ios/Local.xcconfig` — gitignored, KK's real values

### Modified files (CDK)
- `platform/lib/config.ts` — add new `required()` calls for new vars
- `platform/bin/platform.ts:71` — `webRedirectUri` from config
- `platform/lib/static-stack.ts:13` — `APP_CERT_ARN` from config
- `platform/lib/events-stack.ts:99` — `APNS_PLATFORM_APP_ARN` from config
- `platform/lib/auth-stack.ts:37` — post_confirmation `API_BASE_URL`
- `platform/lib/auth-stack.ts:146-156` — web client callback/logout URLs
- `platform/lib/api-stack.ts:347-348` — `ADMIN_EMAILS` + `DOMAIN` for admin_tenants
- `platform/lib/api-stack.ts:391` — `WEB_CALLBACK_URL` for ai_github
- `platform/lib/api-stack.ts:658` — `APP_DOMAIN` (and the MeFn env block — see Slice A3)

### Modified files (Lambdas — onboarding constants)
- `platform/lambda/onboarding_aws_initiate/main.py` — module constants → env vars

### Modified files (web)
- `web/src/lib/api.ts:6` — `BASE_URL`
- `web/src/lib/cognito.ts:129` — `API_BASE_URL`
- `web/src/chat/{chatApi,voiceClient,turnQueue}.ts:8/46/20` — `REST_BASE`
- `web/src/routes/TrustPublic.tsx:14` — `API_BASE_URL`
- `web/src/chat/Shell.tsx:21-30` — drop `ADMIN_EMAILS`; use `me.user.is_admin`
- `web/src/routes/Shell.tsx:39-48` — drop `ADMIN_EMAILS`; use `me.user.is_admin`
- `web/src/lib/api.ts:37-41` — extend `MeResponse` with `is_admin`
- `web/src/routes/AISummary.test.tsx`, similar test files — mock the new field where MeResponse is mocked

### Modified files (iOS)
- `ios/project.yml` — add `configFiles:` + Info.plist key
- `ios/CISOCopilot/Services/APIClient.swift:8` — `baseURL` from Bundle

### Modified files (server is_admin)
- `platform/lambda/me/main.py` — read `ADMIN_EMAILS`, compute + emit `is_admin`

### Modified files (CFN customer onboarding)
- `platform/cfn/aws-onboard.yaml:13,30,35` — drop `Default:` values
- `platform/cfn/azure/onboard.sh`, `gcp/onboard.sh` — drop hardcoded defaults (already overridable via env)

### Modified files (test fixtures)
- `platform/lambda/ai_scanner/tests/test_unified_writer.py:123,202-203`
- `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/expected.json`
- `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/repo/.github/workflows/deploy.yml`
- `platform/lambda/soc_enrichment/tests/conftest.py:34-35`
- `platform/lambda/event_router/tests/conftest.py:14,21,27,40,50`

### Modified files (scripts)
- `scripts/send_approval_email.py:28-29,31,33`
- `platform/scripts/migrate_to_entities.py:36-37` (or delete if obsolete)

### Deleted files
- `workers/` directory entirely (v1 sunset)

### Updated config
- `.gitignore` — add `web/.env.*` (except `.example`) and `ios/Local.xcconfig`
- `HANDOFF.md` — append Slice-A-shipped block at the top

---

## Slice A1 — Foundation (additive only)

**Goal:** create the new env-var surfaces and helpers without changing any consumer. After A1, `cdk synth` must produce the identical CloudFormation template; the codebase is unchanged behaviourally.

### Task A1.1: Augment `platform/.env.example`

**Files:**
- Modify: `platform/.env.example`

- [ ] **Step 1: Append the new variables**

Add this block to the end of `platform/.env.example`:

```bash
# ---- Augmented for secrets extraction (2026-05-26) ----

# AWS deployment target — used by CDK at synth time
AWS_ACCOUNT_ID=

# Public-facing identifiers — used by both CDK and runtime Lambdas
SHASTA_DOMAIN=shasta.transilience.cloud
API_BASE_URL=https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/v1
WEB_REDIRECT_URI=https://shasta.transilience.cloud/callback
APP_DOMAIN=https://shasta.transilience.cloud

# Admin allowlist — server-side gate; client receives is_admin flag via /me
ADMIN_EMAILS=

# Pre-existing per-deployment ARNs
APNS_PLATFORM_APP_ARN=
APP_CERT_ARN=

# Legacy stop-gap domain still in CloudFront cert (drop when DNS-only on
# shasta.transilience.cloud); empty string disables the alternate domain
LEGACY_APP_DOMAIN=app.settlingforless.com
```

- [ ] **Step 2: Commit**

```bash
git add platform/.env.example
git commit -m "chore(env): augment .env.example with deployment identifiers"
```

### Task A1.2: Augment `platform/lib/config.ts` with the new vars

**Files:**
- Modify: `platform/lib/config.ts`

- [ ] **Step 1: Add the new fields**

Replace the contents of `platform/lib/config.ts` with:

```typescript
import 'dotenv/config';

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required env var: ${name}. Copy platform/.env.example to platform/.env and fill it in.`);
  return v;
}

function optional(name: string, fallback: string = ''): string {
  return process.env[name] ?? fallback;
}

export const config = {
  awsRegion:          process.env.AWS_REGION ?? 'us-east-1',
  awsAccountId:       required('AWS_ACCOUNT_ID'),
  domain:             required('DOMAIN'),
  approvalRecipient:  required('APPROVAL_RECIPIENT'),
  entraTenantId:      required('ENTRA_TENANT_ID'),
  entraClientId:      required('ENTRA_CLIENT_ID'),
  entraClientSecret:  required('ENTRA_CLIENT_SECRET'),
  googleClientId:     required('GOOGLE_CLIENT_ID'),
  googleClientSecret: required('GOOGLE_CLIENT_SECRET'),

  // Augmented 2026-05-26 — extracted from hardcoded source
  shastaDomain:       required('SHASTA_DOMAIN'),
  apiBaseUrl:         required('API_BASE_URL'),
  webRedirectUri:     required('WEB_REDIRECT_URI'),
  appDomain:          required('APP_DOMAIN'),
  adminEmails:        required('ADMIN_EMAILS'),
  apnsPlatformAppArn: required('APNS_PLATFORM_APP_ARN'),
  appCertArn:         required('APP_CERT_ARN'),
  legacyAppDomain:    optional('LEGACY_APP_DOMAIN', ''),
};
```

- [ ] **Step 2: Populate `platform/.env` with KK's real values**

Open `platform/.env` (NOT `.env.example`) and add the same block as A1.1 but with KK's actual values:

```bash
AWS_ACCOUNT_ID=470226123496
SHASTA_DOMAIN=shasta.transilience.cloud
API_BASE_URL=https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1
WEB_REDIRECT_URI=https://shasta.transilience.cloud/callback
APP_DOMAIN=https://shasta.transilience.cloud
ADMIN_EMAILS=kkmookhey@gmail.com,kkmookhey@transilience.ai,kkmookhey@networkintelligence.ai
APNS_PLATFORM_APP_ARN=arn:aws:sns:us-east-1:470226123496:app/APNS_SANDBOX/CISOCopilotAPNSSandbox
APP_CERT_ARN=arn:aws:acm:us-east-1:470226123496:certificate/28690c41-24bc-4eb8-b925-87820a2fb605
LEGACY_APP_DOMAIN=app.settlingforless.com
```

- [ ] **Step 3: Verify `cdk synth` still works**

```bash
cd platform && npx cdk synth --quiet 2>&1 | tail -20
```

Expected: no errors. The fact that `config.ts` already throws on missing values means a successful synth proves `platform/.env` has everything.

- [ ] **Step 4: Commit**

```bash
git add platform/lib/config.ts
git commit -m "feat(config): expose deployment identifiers via config module"
```

### Task A1.3: Create `web/.env.example`, `web/.env.production`, `web/src/lib/env.ts`

**Files:**
- Create: `web/.env.example`, `web/.env.production`, `web/src/lib/env.ts`

- [ ] **Step 1: Create `web/.env.example`**

```bash
# Copy to web/.env.production (which is gitignored) and fill in real values.
# Vite picks up VITE_*-prefixed vars at build time and substitutes them into
# the bundle via import.meta.env.VITE_*

VITE_API_BASE_URL=https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/v1
VITE_APP_DOMAIN=https://shasta.example.com
```

- [ ] **Step 2: Create `web/.env.production`**

```bash
VITE_API_BASE_URL=https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1
VITE_APP_DOMAIN=https://shasta.transilience.cloud
```

- [ ] **Step 3: Create `web/src/lib/env.ts`**

```typescript
// Vite env-var boundary. Throws at module load if any required VITE_* var is
// missing — fail loud rather than ship a bundle that 404s every request.

function requireEnv(name: string): string {
  const v = (import.meta.env as Record<string, string | undefined>)[name];
  if (!v) {
    throw new Error(
      `Missing required Vite env var: ${name}. ` +
      `Copy web/.env.example to web/.env.production (or .env.development) and fill it in.`,
    );
  }
  return v;
}

export const env = {
  apiBaseUrl: requireEnv('VITE_API_BASE_URL'),
  appDomain:  requireEnv('VITE_APP_DOMAIN'),
};
```

- [ ] **Step 4: Commit**

```bash
git add web/.env.example web/src/lib/env.ts
git commit -m "feat(web): vite env-var boundary with fail-loud validator"
```

(Note: `web/.env.production` is NOT staged — it's gitignored in A1.5.)

### Task A1.4: Create `ios/Local.xcconfig.example` and `ios/Local.xcconfig`

**Files:**
- Create: `ios/Local.xcconfig.example`, `ios/Local.xcconfig`

- [ ] **Step 1: Create `ios/Local.xcconfig.example`**

```bash
// CISO Copilot — local build configuration.
//
// Copy to ios/Local.xcconfig (which is gitignored) and fill in real values.
// The xcconfig substitutes into Info.plist via $(API_BASE_URL); the app
// reads it at runtime from Bundle.main.infoDictionary.
//
// xcconfig syntax note: // is a line comment in xcconfig, so a URL with //
// must escape the double slash as /$()/ — that's an empty variable expansion
// between the slashes, telling Xcode not to treat them as a comment.

API_BASE_URL = https:/$()/YOUR-API-ID.execute-api.us-east-1.amazonaws.com/v1
```

- [ ] **Step 2: Create `ios/Local.xcconfig`**

```bash
API_BASE_URL = https:/$()/xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1
```

- [ ] **Step 3: Commit (only the .example)**

```bash
git add ios/Local.xcconfig.example
git commit -m "feat(ios): xcconfig template for API base URL"
```

### Task A1.5: Update `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add new ignore patterns**

Find the existing `# Secrets — never commit` block in `.gitignore` and append:

```
# Web app per-environment Vite vars (template lives at web/.env.example)
web/.env
web/.env.local
web/.env.development
web/.env.production

# iOS local build config (template lives at ios/Local.xcconfig.example)
ios/Local.xcconfig
```

- [ ] **Step 2: Verify new files are now ignored**

```bash
git check-ignore -v web/.env.production ios/Local.xcconfig
```

Expected: both paths listed with matching `.gitignore` line numbers.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore(gitignore): cover web/.env.* and ios/Local.xcconfig"
```

### Task A1.6: A1 verification gate — `cdk synth` unchanged

- [ ] **Step 1: Synth before consumers change**

```bash
cd platform && npx cdk synth --quiet > /tmp/synth-before-a2.txt 2>&1
echo "exit=$?"
```

Expected: `exit=0`. The file `/tmp/synth-before-a2.txt` is the baseline for Slice A2's diff check.

- [ ] **Step 2: Re-run `pnpm test` in web**

```bash
cd web && pnpm test --run 2>&1 | tail -5
```

Expected: same pass count as before A1 (the new `env.ts` has no consumers yet).

---

## Slice A2 — CDK + scripts migration

**Goal:** every hardcoded ARN/URL/email/account-ID in `platform/lib/`, `platform/bin/`, `platform/lambda/onboarding_aws_initiate/`, and `scripts/` reads from config. `cdk synth` diff vs Slice A1 baseline shows source-of-value changes only; `cdk diff` against deployed stack shows zero resource changes.

### Task A2.1: Move `APP_CERT_ARN` to config

**Files:**
- Modify: `platform/lib/static-stack.ts:13`

- [ ] **Step 1: Replace the hardcoded constant**

Change `platform/lib/static-stack.ts`:

```typescript
// BEFORE (line 1-8 import block + 10-15 constants):
import { Construct } from 'constructs';
import * as path from 'path';

// ACM cert covering both the canonical shasta.transilience.cloud domain and
// the legacy app.settlingforless.com stop-gap domain (DNS-validated 2026-05-21).
// Lives in us-east-1 by necessity (CloudFront only accepts certs from us-east-1).
const APP_CERT_ARN  = 'arn:aws:acm:us-east-1:470226123496:certificate/28690c41-24bc-4eb8-b925-87820a2fb605';
const APP_DOMAIN    = 'app.settlingforless.com';      // legacy stop-gap domain
const SHASTA_DOMAIN = 'shasta.transilience.cloud';    // canonical domain
```

To:

```typescript
import { Construct } from 'constructs';
import * as path from 'path';
import { config } from './config';

// ACM cert + canonical domain come from platform/.env so the repo stays
// operator-agnostic. The legacy `app.settlingforless.com` stop-gap is kept
// as an optional alternate domain — empty string disables it.
const APP_CERT_ARN  = config.appCertArn;
const APP_DOMAIN    = config.legacyAppDomain;
const SHASTA_DOMAIN = config.shastaDomain;
```

- [ ] **Step 2: Handle the `domainNames` array when `APP_DOMAIN` is empty**

In the same file (around line 99), change:

```typescript
domainNames: [SHASTA_DOMAIN, APP_DOMAIN],
```

To:

```typescript
domainNames: APP_DOMAIN ? [SHASTA_DOMAIN, APP_DOMAIN] : [SHASTA_DOMAIN],
```

- [ ] **Step 3: Verify synth still works and matches baseline**

```bash
cd platform && npx cdk synth CisoCopilotStatic --quiet > /tmp/synth-static-a2.txt 2>&1
diff <(npx cdk synth CisoCopilotStatic --quiet 2>/dev/null) /tmp/synth-static-a2.txt
echo "exit=$?"
```

Expected: `exit=0` (empty diff — same template).

- [ ] **Step 4: Commit**

```bash
git add platform/lib/static-stack.ts
git commit -m "refactor(cdk): static stack reads APP_CERT_ARN + domains from config"
```

### Task A2.2: Move `APNS_PLATFORM_APP_ARN` to config

**Files:**
- Modify: `platform/lib/events-stack.ts:99-100`

- [ ] **Step 1: Add config import (if not present)**

Verify `platform/lib/events-stack.ts` has `import { config } from './config';`. If not, add it after the other imports.

- [ ] **Step 2: Replace the hardcoded constant**

```typescript
// BEFORE:
const APNS_PLATFORM_APP_ARN = 'arn:aws:sns:us-east-1:470226123496:app/APNS_SANDBOX/CISOCopilotAPNSSandbox';
this.routerFn.addEnvironment('APNS_PLATFORM_APPLICATION_ARN', APNS_PLATFORM_APP_ARN);

// AFTER:
this.routerFn.addEnvironment('APNS_PLATFORM_APPLICATION_ARN', config.apnsPlatformAppArn);
```

- [ ] **Step 3: Verify**

```bash
cd platform && npx cdk synth CisoCopilotEvents --quiet > /tmp/synth-events-after.txt 2>&1
echo "exit=$?"
```

Expected: `exit=0`.

- [ ] **Step 4: Commit**

```bash
git add platform/lib/events-stack.ts
git commit -m "refactor(cdk): events stack reads APNS ARN from config"
```

### Task A2.3: Move `webRedirectUri` to config

**Files:**
- Modify: `platform/bin/platform.ts:71`

- [ ] **Step 1: Replace the literal**

```typescript
// BEFORE (line 71):
webRedirectUri:     'https://shasta.transilience.cloud/callback',

// AFTER:
webRedirectUri:     config.webRedirectUri,
```

- [ ] **Step 2: Add the `config` import**

Verify `import { config } from '../lib/config';` is already at the top of `platform/bin/platform.ts` (it is — line 4). No change needed.

- [ ] **Step 3: Verify**

```bash
cd platform && npx cdk synth CisoCopilotApi --quiet 2>&1 | head -5
```

Expected: no errors. (The full template comparison happens in A2.11.)

- [ ] **Step 4: Commit**

```bash
git add platform/bin/platform.ts
git commit -m "refactor(cdk): platform entry reads webRedirectUri from config"
```

### Task A2.4: Move `ADMIN_EMAILS` + `DOMAIN` env vars for `admin_tenants`

**Files:**
- Modify: `platform/lib/api-stack.ts:345-349`

- [ ] **Step 1: Replace the hardcoded env block**

```typescript
// BEFORE (around line 345-349):
environment: {
  ...dbEnv,
  ADMIN_EMAILS: 'kkmookhey@gmail.com,kkmookhey@transilience.ai,kkmookhey@networkintelligence.ai',
  DOMAIN:       'settlingforless.com',
},

// AFTER:
environment: {
  ...dbEnv,
  ADMIN_EMAILS: config.adminEmails,
  DOMAIN:       config.domain,
},
```

- [ ] **Step 2: Verify**

```bash
cd platform && grep -n "kkmookhey@" platform/lib/
```

Expected: no hits (only `config.adminEmails` references remain).

- [ ] **Step 3: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "refactor(cdk): admin_tenants env from config (ADMIN_EMAILS, DOMAIN)"
```

### Task A2.5: Move `WEB_CALLBACK_URL`, `APP_DOMAIN`, post-confirmation `API_BASE_URL`

**Files:**
- Modify: `platform/lib/api-stack.ts:391`, `platform/lib/api-stack.ts:658`, `platform/lib/auth-stack.ts:37`

- [ ] **Step 1: api-stack.ts:391 — ai_github WEB_CALLBACK_URL**

```typescript
// BEFORE:
WEB_CALLBACK_URL:      'https://shasta.transilience.cloud/ai/install/callback',

// AFTER:
WEB_CALLBACK_URL:      `${config.appDomain}/ai/install/callback`,
```

- [ ] **Step 2: api-stack.ts:658 — APP_DOMAIN env var**

```typescript
// BEFORE:
APP_DOMAIN:      'https://shasta.transilience.cloud',

// AFTER:
APP_DOMAIN:      config.appDomain,
```

- [ ] **Step 3: auth-stack.ts:37 — post_confirmation API_BASE_URL**

```typescript
// BEFORE:
API_BASE_URL:       'https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1',

// AFTER:
API_BASE_URL:       config.apiBaseUrl,
```

- [ ] **Step 4: Verify no remaining literals in api-stack/auth-stack**

```bash
grep -nE "(xoljryrb7i|shasta\.transilience\.cloud)" platform/lib/api-stack.ts platform/lib/auth-stack.ts
```

Expected: zero hits (one false positive in auth-stack.ts:146-156 callback URLs which A2.6 handles).

- [ ] **Step 5: Commit**

```bash
git add platform/lib/api-stack.ts platform/lib/auth-stack.ts
git commit -m "refactor(cdk): API/auth Lambdas read URLs from config"
```

### Task A2.6: Move auth-stack web client callback/logout URLs

**Files:**
- Modify: `platform/lib/auth-stack.ts:142-156`

- [ ] **Step 1: Replace the callback/logout URL arrays**

```typescript
// BEFORE (lines 145-156):
callbackUrls: [
  'https://shasta.transilience.cloud/callback',      // canonical domain
  `https://app.${config.domain}/callback`,           // legacy stop-gap domain
  'https://dil1ztnjosz43.cloudfront.net/callback',   // CloudFront default (kept as backup)
  'http://localhost:5173/callback',                  // Vite dev server
],
logoutUrls: [
  'https://shasta.transilience.cloud/',
  `https://app.${config.domain}/`,
  'https://dil1ztnjosz43.cloudfront.net/',
  'http://localhost:5173/',
],

// AFTER:
callbackUrls: [
  `${config.appDomain}/callback`,                    // canonical domain
  ...(config.legacyAppDomain ? [`https://app.${config.domain}/callback`] : []),
  'http://localhost:5173/callback',                  // Vite dev server
],
logoutUrls: [
  `${config.appDomain}/`,
  ...(config.legacyAppDomain ? [`https://app.${config.domain}/`] : []),
  'http://localhost:5173/',
],
```

(Note: the `dil1ztnjosz43.cloudfront.net` backup URLs are dropped — they were defensive for the pre-canonical-domain period and aren't load-bearing now. If KK wants to keep them, leave them as-is; flagged here as a small cleanup.)

- [ ] **Step 2: Verify final auth-stack literal sweep**

```bash
grep -nE "(xoljryrb7i|shasta\.transilience\.cloud|dil1ztnjosz43)" platform/lib/auth-stack.ts
```

Expected: zero hits.

- [ ] **Step 3: Commit**

```bash
git add platform/lib/auth-stack.ts
git commit -m "refactor(cdk): web client URLs from config; drop CloudFront fallback"
```

### Task A2.7: Move `onboarding_aws_initiate` module constants to env vars

**Files:**
- Modify: `platform/lambda/onboarding_aws_initiate/main.py`
- Modify: `platform/lib/api-stack.ts` (find the OnboardingAwsInitiateFn definition; add new env vars)

- [ ] **Step 1: Find the Lambda's CDK env block**

```bash
grep -n "OnboardingAwsInitiateFn\|onboarding_aws_initiate" platform/lib/api-stack.ts
```

Note the Lambda's `environment:` block — it likely already sets some vars; we add `OUR_ACCOUNT_ID`, `CENTRAL_EVENT_BUS_ARN`, `COMPLETE_WEBHOOK_URL`, `CDN_BUCKET_NAME` if not already present.

- [ ] **Step 2: Read the current module constants in the Lambda**

```bash
grep -nE "OUR_ACCOUNT_ID|CENTRAL_EVENT_BUS_ARN|COMPLETE_WEBHOOK_URL|CFN_TEMPLATE_BUCKET|CFN_TEMPLATE_KEY" \
  platform/lambda/onboarding_aws_initiate/main.py
```

Note the values currently assigned at module top. Confirm whether they're already `os.environ.get(...)` (good — just need to fill CDK side) or literal strings (need to change both sides).

- [ ] **Step 3: If literals, replace with `os.environ[...]`**

In `platform/lambda/onboarding_aws_initiate/main.py`, change any literal module-level constants to:

```python
OUR_ACCOUNT_ID         = os.environ["OUR_ACCOUNT_ID"]
CENTRAL_EVENT_BUS_ARN  = os.environ["CENTRAL_EVENT_BUS_ARN"]
COMPLETE_WEBHOOK_URL   = os.environ["COMPLETE_WEBHOOK_URL"]
# CFN_TEMPLATE_BUCKET + CFN_TEMPLATE_KEY likely already env-driven; verify
```

- [ ] **Step 4: Pass values from CDK**

In `platform/lib/api-stack.ts`, on the Lambda's `environment:` block, add:

```typescript
OUR_ACCOUNT_ID:        config.awsAccountId,
CENTRAL_EVENT_BUS_ARN: `arn:aws:events:${config.awsRegion}:${config.awsAccountId}:event-bus/ciso-copilot-events`,
COMPLETE_WEBHOOK_URL:  `${config.apiBaseUrl}/onboarding/aws/complete`,
```

- [ ] **Step 5: Repeat for the Azure + GCP onboarding initiate Lambdas**

```bash
grep -nE "OUR_ACCOUNT_ID|COMPLETE_WEBHOOK_URL" \
  platform/lambda/onboarding_{azure,gcp}_initiate/main.py 2>/dev/null
```

Apply the same pattern: literal → `os.environ[...]`; matching env-var assignments in api-stack.ts.

- [ ] **Step 6: Run the Lambda tests**

```bash
pytest platform/lambda/onboarding_aws_initiate/tests/ \
       platform/lambda/onboarding_azure_initiate/tests/ \
       platform/lambda/onboarding_gcp_initiate/tests/ 2>&1 | tail -5
```

Expected: all green. (Tests likely already mock these env vars — if any fail, set the env var in the conftest fixture.)

- [ ] **Step 7: Commit**

```bash
git add platform/lambda/onboarding_*_initiate/main.py platform/lib/api-stack.ts
git commit -m "refactor(onboarding): module constants -> env vars (AWS/Azure/GCP)"
```

### Task A2.8: Migrate `scripts/send_approval_email.py`

**Files:**
- Modify: `scripts/send_approval_email.py:28-33`

- [ ] **Step 1: Replace literal constants with env reads + helpful errors**

```python
# BEFORE (lines 28-33):
DB_CLUSTER_ARN      = "arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh"
DB_SECRET_ARN       = "arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp"
APPROVAL_RECIPIENT  = "kkmookhey@gmail.com"
API_BASE_URL        = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1"

# AFTER:
import os
from dotenv import load_dotenv

# Load platform/.env if present (script lives in repo root scripts/ but reads CDK env)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "platform", ".env"))

def _required(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Missing env var: {name}. Set in platform/.env or export it.")
    return v

DB_CLUSTER_ARN      = _required("DB_CLUSTER_ARN")
DB_SECRET_ARN       = _required("DB_SECRET_ARN")
APPROVAL_RECIPIENT  = _required("APPROVAL_RECIPIENT")
API_BASE_URL        = _required("API_BASE_URL")
```

- [ ] **Step 2: Add the DB ARNs to `platform/.env`** (which doesn't have them yet)

Append to `platform/.env`:

```bash
# Aurora cluster + secret for one-off operational scripts (CDK stacks read these from stack outputs)
DB_CLUSTER_ARN=arn:aws:rds:us-east-1:470226123496:cluster:cisocopilotdata-aurorapg9038c119-4oo3zrwtnfxh
DB_SECRET_ARN=arn:aws:secretsmanager:us-east-1:470226123496:secret:AuroraPgSecretF5CEE99C-niqW1iheRsGP-BgwkPp
```

And to `platform/.env.example`:

```bash
DB_CLUSTER_ARN=
DB_SECRET_ARN=
```

- [ ] **Step 3: Smoke-test the script (dry-run if it has one; else manual)**

```bash
python3 scripts/send_approval_email.py --help 2>&1 | head -5
```

Expected: at minimum, no `SystemExit: Missing env var` (the env loaded).

- [ ] **Step 4: Commit**

```bash
git add scripts/send_approval_email.py platform/.env.example
git commit -m "refactor(scripts): send_approval_email reads from platform/.env"
```

### Task A2.9: Decide on `platform/scripts/migrate_to_entities.py`

**Files:**
- Read: `platform/scripts/migrate_to_entities.py` (decision-driven)

- [ ] **Step 1: Check whether the script is still referenced**

```bash
grep -rn "migrate_to_entities" --include="*.md" --include="*.sh" --include="*.py" --include="*.ts" \
  . 2>/dev/null | grep -v node_modules
```

If only doc references in `docs/superpowers/plans/*.md` (which are historical), the script is obsolete.

- [ ] **Step 2A: If obsolete, delete it**

```bash
git rm platform/scripts/migrate_to_entities.py
git commit -m "chore: remove obsolete one-off migrate_to_entities script"
```

- [ ] **Step 2B: If still useful, migrate to env like A2.8**

Same pattern as A2.8 — read ARNs from `platform/.env` via `python-dotenv`. Commit:

```bash
git add platform/scripts/migrate_to_entities.py
git commit -m "refactor(scripts): migrate_to_entities reads from platform/.env"
```

### Task A2.10: Slice A2 verification gate — `cdk synth` + `cdk diff`

- [ ] **Step 1: Full synth after all A2 changes**

```bash
cd platform && npx cdk synth --quiet > /tmp/synth-after-a2.txt 2>&1
echo "exit=$?"
```

Expected: `exit=0`.

- [ ] **Step 2: Compare synth output to A1 baseline — there should be NO functional changes**

```bash
diff /tmp/synth-before-a2.txt /tmp/synth-after-a2.txt | head -50
```

Expected: empty diff (CDK substituted the same literal values from `config` as were hardcoded before). If anything non-trivial differs, investigate before deploying — likely a typo in the new env var or a callback-URL list that changed shape.

- [ ] **Step 3: `cdk diff` against deployed stack**

```bash
cd platform && npx cdk diff CisoCopilotStatic CisoCopilotEvents CisoCopilotAuth CisoCopilotApi 2>&1 | tail -30
```

Expected: "There were no differences" for each of the four stacks. If any shows a diff, do NOT deploy until investigated.

- [ ] **Step 4: Deploy**

```bash
cd platform && \
  npx cdk deploy CisoCopilotStatic CisoCopilotEvents CisoCopilotAuth CisoCopilotApi \
  --require-approval never 2>&1 | tail -20
```

Expected: each stack reports `no changes — skipping` (because the templates are identical to deployed) or completes with `UPDATE_COMPLETE`.

- [ ] **Step 5: Smoke-test sign-in flow + Dashboard + /ai + /soc**

Manually from a fresh incognito window:
1. Visit `https://shasta.transilience.cloud/`
2. Click "Sign in with Google" → complete sign-in → land on Dashboard
3. Navigate to `/ai`, `/soc`, `/connect` — each loads without 500/401
4. Check `/admin` — admin nav visible (still using current client allowlist; refactored in A3+A4)

If anything fails, `git revert` the A2 commits and investigate before proceeding.

---

## Slice A3 — Server emits `is_admin` on `/me`

**Goal:** `/me` Lambda receives `ADMIN_EMAILS` env var, computes `is_admin: bool` from the caller's email, and includes it in the response. Web `MeResponse` type extends with `is_admin`.

### Task A3.1: Add `ADMIN_EMAILS` env var to `MeFn` in CDK

**Files:**
- Modify: `platform/lib/api-stack.ts:57-65` (the MeFn block)

- [ ] **Step 1: Add the env var**

```typescript
// BEFORE (around line 57-65):
const meFn = new lambda.Function(this, 'MeFn', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'main.handler',
  code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'me')),
  timeout: cdk.Duration.seconds(10),
  environment: dbEnv,
});

// AFTER:
const meFn = new lambda.Function(this, 'MeFn', {
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'main.handler',
  code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'me')),
  timeout: cdk.Duration.seconds(10),
  environment: {
    ...dbEnv,
    ADMIN_EMAILS: config.adminEmails,
  },
});
```

- [ ] **Step 2: Commit (will be combined with handler change at A3.4)**

Hold off committing — bundle with A3.4.

### Task A3.2: Write the failing tests for `me/main.py` `is_admin`

**Files:**
- Create: `platform/lambda/me/tests/test_handler.py`

- [ ] **Step 1: Confirm there's no existing test file**

```bash
ls platform/lambda/me/tests/ 2>/dev/null
```

If empty/missing, create the directory: `mkdir -p platform/lambda/me/tests`.

- [ ] **Step 2: Write the test file**

```python
# platform/lambda/me/tests/test_handler.py
"""Tests for /me handler — focused on the new is_admin field.

The DB lookup is the existing behavior; we stub rds_data to return a fixed
row and verify is_admin is correctly computed from the ADMIN_EMAILS env var.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def env_setup(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:999999999999:cluster:test")
    monkeypatch.setenv("DB_SECRET_ARN",  "arn:aws:secretsmanager:us-east-1:999999999999:secret:test")
    monkeypatch.setenv("DB_NAME",        "ciso_copilot_test")
    monkeypatch.setenv("ADMIN_EMAILS",   "admin@example.com,kk@example.com")


def _make_event(sub: str = "test-sub") -> dict:
    return {
        "requestContext": {
            "authorizer": {
                "claims": {"sub": sub, "email": "user@example.com"},
            },
        },
    }


def _make_db_response(email: str, role: str = "admin") -> dict:
    return {
        "records": [[
            {"stringValue": email},
            {"stringValue": role},
            {"stringValue": "tenant-uuid"},
            {"stringValue": "Test Tenant"},
            {"stringValue": "approved"},
        ]],
    }


def test_is_admin_true_when_email_in_allowlist(env_setup):
    with patch("boto3.client") as mock_boto:
        mock_rds = MagicMock()
        mock_rds.execute_statement.return_value = _make_db_response("admin@example.com")
        mock_boto.return_value = mock_rds

        # Re-import the module so the new env var is read at module load
        import importlib
        from platform.lambda.me import main as me_main
        importlib.reload(me_main)

        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is True


def test_is_admin_false_when_email_not_in_allowlist(env_setup):
    with patch("boto3.client") as mock_boto:
        mock_rds = MagicMock()
        mock_rds.execute_statement.return_value = _make_db_response("randomuser@example.com")
        mock_boto.return_value = mock_rds

        import importlib
        from platform.lambda.me import main as me_main
        importlib.reload(me_main)

        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is False


def test_is_admin_case_insensitive(env_setup):
    with patch("boto3.client") as mock_boto:
        mock_rds = MagicMock()
        mock_rds.execute_statement.return_value = _make_db_response("Admin@Example.com")
        mock_boto.return_value = mock_rds

        import importlib
        from platform.lambda.me import main as me_main
        importlib.reload(me_main)

        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is True


def test_is_admin_false_when_admin_emails_empty(monkeypatch):
    monkeypatch.setenv("DB_CLUSTER_ARN", "arn:...:test")
    monkeypatch.setenv("DB_SECRET_ARN",  "arn:...:test")
    monkeypatch.setenv("DB_NAME",        "test")
    monkeypatch.setenv("ADMIN_EMAILS",   "")  # explicitly empty

    with patch("boto3.client") as mock_boto:
        mock_rds = MagicMock()
        mock_rds.execute_statement.return_value = _make_db_response("anyone@example.com")
        mock_boto.return_value = mock_rds

        import importlib
        from platform.lambda.me import main as me_main
        importlib.reload(me_main)

        result = me_main.handler(_make_event(), None)

    body = json.loads(result["body"])
    assert body["user"]["is_admin"] is False
```

- [ ] **Step 3: Run the tests — expect them to FAIL (is_admin field doesn't exist yet)**

```bash
cd /Users/kkmookhey/Projects/CISOBrief && \
  pytest platform/lambda/me/tests/test_handler.py -v 2>&1 | tail -15
```

Expected: 4 FAIL with `KeyError: 'is_admin'` or `assert ... in body["user"]`.

### Task A3.3: Modify `me/main.py` to emit `is_admin`

**Files:**
- Modify: `platform/lambda/me/main.py`

- [ ] **Step 1: Add the ADMIN_EMAILS module constant**

At the top of `platform/lambda/me/main.py`, after the existing `DB_*` constants:

```python
DB_CLUSTER_ARN = os.environ["DB_CLUSTER_ARN"]
DB_SECRET_ARN  = os.environ["DB_SECRET_ARN"]
DB_NAME        = os.environ["DB_NAME"]
ADMIN_EMAILS   = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}
```

- [ ] **Step 2: Compute is_admin and include in response**

Replace the response block at lines 58-69:

```python
# BEFORE:
return _resp(200, {
    "user": {
        "email": r[0].get("stringValue"),
        "role":  r[1].get("stringValue"),
    },
    "tenant": {
        "tenant_id":    r[2].get("stringValue"),
        "display_name": r[3].get("stringValue"),
        "status":       r[4].get("stringValue"),
    },
})

# AFTER:
email = r[0].get("stringValue")
is_admin = bool(email) and email.lower() in ADMIN_EMAILS
return _resp(200, {
    "user": {
        "email":    email,
        "role":     r[1].get("stringValue"),
        "is_admin": is_admin,
    },
    "tenant": {
        "tenant_id":    r[2].get("stringValue"),
        "display_name": r[3].get("stringValue"),
        "status":       r[4].get("stringValue"),
    },
})
```

- [ ] **Step 3: Run tests — expect them to PASS**

```bash
pytest platform/lambda/me/tests/test_handler.py -v 2>&1 | tail -10
```

Expected: 4 PASS.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/me/main.py platform/lambda/me/tests/test_handler.py platform/lib/api-stack.ts
git commit -m "feat(me): emit is_admin computed from ADMIN_EMAILS env var

Server-side gate stays the security boundary (ADMIN_EMAILS read in
admin_tenants); /me now exposes the flag for client UX gating so the
web bundle no longer needs to know KK's personal email addresses."
```

### Task A3.4: Update `MeResponse` type in web

**Files:**
- Modify: `web/src/lib/api.ts:37-41`

- [ ] **Step 1: Extend `MeResponse`**

```typescript
// BEFORE:
export interface MeResponse {
  user:   { email: string | null; role: string | null } | null;
  tenant: { tenant_id: string; display_name: string;
            status: "pending" | "approved" | "rejected" | "suspended" } | null;
}

// AFTER:
export interface MeResponse {
  user:   { email: string | null; role: string | null; is_admin: boolean } | null;
  tenant: { tenant_id: string; display_name: string;
            status: "pending" | "approved" | "rejected" | "suspended" } | null;
}
```

- [ ] **Step 2: Verify nothing else breaks**

```bash
cd web && pnpm typecheck 2>&1 | tail -10
```

(If `pnpm typecheck` doesn't exist as a script, use `npx tsc --noEmit`.)

Expected: no errors. (If there are existing mocks of `MeResponse` in test files that lack `is_admin`, we'll catch them in A4 web tests; for now type-only addition is non-breaking via optional or addition.)

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/api.ts
git commit -m "feat(web): MeResponse includes is_admin flag"
```

### Task A3.5: Deploy + curl verify

- [ ] **Step 1: Deploy**

```bash
cd platform && npx cdk deploy CisoCopilotApi --require-approval never 2>&1 | tail -10
```

Expected: `UPDATE_COMPLETE` (one Lambda update).

- [ ] **Step 2: Curl `/me` with KK's token**

KK retrieves a fresh ID token from the running web app (DevTools → Application → Local Storage → `id_token`). Then:

```bash
TOKEN="<paste>"
curl -s -H "Authorization: Bearer $TOKEN" \
  https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1/me | python3 -m json.tool
```

Expected: `user.is_admin: true` for KK; field present and `false` for any non-allowlisted account.

---

## Slice A4 — Web migration

**Goal:** every `web/src/` file reads URLs/identifiers from Vite env; both `Shell.tsx` copies drop `ADMIN_EMAILS` and consume `me.user.is_admin`; production bundle contains zero literal account IDs, API IDs, or personal emails.

### Task A4.1: Migrate `web/src/lib/api.ts`

**Files:**
- Modify: `web/src/lib/api.ts:6`

- [ ] **Step 1: Replace literal**

```typescript
// BEFORE (line 4-6):
import { validIdToken, signOut } from "./cognito";

const BASE_URL = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";

// AFTER:
import { validIdToken, signOut } from "./cognito";
import { env } from "./env";

const BASE_URL = env.apiBaseUrl;
```

- [ ] **Step 2: Commit**

```bash
git add web/src/lib/api.ts
git commit -m "refactor(web): api.ts reads BASE_URL from Vite env"
```

### Task A4.2: Migrate `web/src/lib/cognito.ts`

**Files:**
- Modify: `web/src/lib/cognito.ts:129`

- [ ] **Step 1: Replace literal**

```typescript
// BEFORE (line 129):
const API_BASE_URL = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";

// AFTER (and import env at the top of the file):
import { env } from "./env";
// ...
const API_BASE_URL = env.apiBaseUrl;
```

- [ ] **Step 2: Commit**

```bash
git add web/src/lib/cognito.ts
git commit -m "refactor(web): cognito.ts reads API_BASE_URL from Vite env"
```

### Task A4.3: Migrate `web/src/chat/{chatApi,voiceClient,turnQueue}.ts`

**Files:**
- Modify: `web/src/chat/chatApi.ts:8`
- Modify: `web/src/chat/voiceClient.ts:46`
- Modify: `web/src/chat/turnQueue.ts:20`

- [ ] **Step 1: Each file — replace the `REST_BASE` literal**

For each of the three files, change:

```typescript
const REST_BASE  = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";
```

To:

```typescript
import { env } from "../lib/env";
// ... (keep all other imports)

const REST_BASE = env.apiBaseUrl;
```

- [ ] **Step 2: Verify zero hits in chat dir**

```bash
grep -n "xoljryrb7i" web/src/chat/
```

Expected: zero hits.

- [ ] **Step 3: Commit**

```bash
git add web/src/chat/chatApi.ts web/src/chat/voiceClient.ts web/src/chat/turnQueue.ts
git commit -m "refactor(web): chat clients read REST_BASE from Vite env"
```

### Task A4.4: Migrate `web/src/routes/TrustPublic.tsx`

**Files:**
- Modify: `web/src/routes/TrustPublic.tsx:14`

- [ ] **Step 1: Replace literal**

```typescript
// BEFORE (line 14):
const API_BASE_URL = "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1";

// AFTER:
import { env } from "../lib/env";
// ...
const API_BASE_URL = env.apiBaseUrl;
```

- [ ] **Step 2: Commit**

```bash
git add web/src/routes/TrustPublic.tsx
git commit -m "refactor(web): TrustPublic reads API_BASE_URL from Vite env"
```

### Task A4.5: Drop `ADMIN_EMAILS` from `web/src/chat/Shell.tsx`

**Files:**
- Modify: `web/src/chat/Shell.tsx:21-30`

- [ ] **Step 1: Remove the constant and local `isAdmin` helper**

Delete lines 21-30 (the `ADMIN_EMAILS` `Set` and the `isAdmin()` helper).

- [ ] **Step 2: Replace `isAdmin(me.user?.email)` callsites with `me.user?.is_admin`**

Find every callsite:

```bash
grep -n "isAdmin(" web/src/chat/Shell.tsx
```

For each callsite, replace:

```typescript
// BEFORE:
{isAdmin(me?.user?.email) && (
  <Link to="/admin">Admin</Link>
)}

// AFTER:
{me?.user?.is_admin && (
  <Link to="/admin">Admin</Link>
)}
```

- [ ] **Step 3: Commit**

```bash
git add web/src/chat/Shell.tsx
git commit -m "refactor(web): chat Shell consumes me.user.is_admin (drop local allowlist)"
```

### Task A4.6: Drop `ADMIN_EMAILS` from `web/src/routes/Shell.tsx`

**Files:**
- Modify: `web/src/routes/Shell.tsx:39-48`

Repeat the same pattern as A4.5 on `web/src/routes/Shell.tsx`. Delete the `ADMIN_EMAILS` set + `isAdmin()` helper; replace callsites with `me?.user?.is_admin`.

- [ ] **Step 1: Same replacements as A4.5 on the routes copy**

- [ ] **Step 2: Verify zero hits for the email pattern**

```bash
grep -rn "kkmookhey@" web/src/
```

Expected: zero hits.

- [ ] **Step 3: Commit**

```bash
git add web/src/routes/Shell.tsx
git commit -m "refactor(web): routes Shell consumes me.user.is_admin (drop local allowlist)"
```

### Task A4.7: Fix tests that mock `MeResponse`

**Files:**
- Modify: any web test file that mocks `MeResponse` without `is_admin`

- [ ] **Step 1: Find tests that need updating**

```bash
grep -rn "MeResponse\|api.me\|aiSummary" web/src/**/*.test.tsx 2>/dev/null | head -20
```

- [ ] **Step 2: Add `is_admin: false` (or `true` per scenario) to each `user:` block in mocks**

For example in `web/src/routes/AISummary.test.tsx` or `web/src/routes/ConnectClouds.test.tsx` — wherever a `MeResponse`-shaped fixture is used:

```typescript
// BEFORE (typical mock):
user: { email: "alice@example.com", role: "admin" },

// AFTER:
user: { email: "alice@example.com", role: "admin", is_admin: true },
```

- [ ] **Step 3: Run web tests**

```bash
cd web && pnpm test --run 2>&1 | tail -10
```

Expected: all tests pass. Same pass count as before A4 (or +1 if any test asserts on the new `is_admin` field).

- [ ] **Step 4: Commit**

```bash
git add web/src/**/*.test.tsx
git commit -m "test(web): update MeResponse mocks with is_admin field"
```

### Task A4.8: Bundle grep verification + deploy

- [ ] **Step 1: Build**

```bash
cd web && pnpm build 2>&1 | tail -10
```

Expected: `built in ...` success message; no TypeScript errors.

- [ ] **Step 2: Grep the resulting bundle for leaked literals**

```bash
grep -rl "kkmookhey@" web/dist/ ; echo "---" ; \
grep -rl "470226123496" web/dist/ ; echo "---" ; \
grep -rl "xoljryrb7i" web/dist/
```

Expected: first two return nothing. The third (`xoljryrb7i`) WILL return matches because the API URL is correctly substituted into the bundle from `VITE_API_BASE_URL` — that's expected. Document this in the commit message as expected behavior.

- [ ] **Step 3: Deploy to S3 + invalidate CloudFront**

```bash
aws s3 sync web/dist/ s3://ciso-copilot-app-470226123496/ --delete && \
aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*' 2>&1 | tail -5
```

Expected: `sync` completes; invalidation `Status: InProgress`.

- [ ] **Step 4: Smoke-test from fresh incognito**

1. `https://shasta.transilience.cloud/` — sign in via Google
2. Land on Dashboard — loads without error
3. Click `/admin` (sidebar) — admin page loads (KK is admin)
4. From a non-admin account (or modify token claims), confirm admin nav is hidden
5. `/ai`, `/soc`, `/connect` all load

- [ ] **Step 5: Commit (records the build + deploy event)**

```bash
git commit --allow-empty -m "deploy(web): Slice A4 — env-driven config + is_admin consumer

S3 sync + CloudFront invalidation completed. Bundle contains
no personal emails or hardcoded account IDs; API URL is
expected (substituted from VITE_API_BASE_URL at build time)."
```

---

## Slice A5 — iOS migration

**Goal:** `APIClient.baseURL` reads from `Info.plist` via xcconfig substitution; the device build picks up the URL from `Local.xcconfig`. New operators copy `Local.xcconfig.example` → `Local.xcconfig`.

### Task A5.1: Reference `Local.xcconfig` in `project.yml`

**Files:**
- Modify: `ios/project.yml`

- [ ] **Step 1: Add `configFiles` to the target**

Under `targets.CISOCopilot`, add:

```yaml
targets:
  CISOCopilot:
    type: application
    platform: iOS
    configFiles:
      Debug:   Local.xcconfig
      Release: Local.xcconfig
    sources:
      # ... (unchanged)
```

(If `Local.xcconfig` is missing, xcodegen will warn but proceed — the build later fails clearly.)

- [ ] **Step 2: Add `INFOPLIST_KEY_API_BASE_URL` to base settings**

Under `settings.base`, add:

```yaml
settings:
  base:
    # ... existing keys ...
    INFOPLIST_KEY_API_BASE_URL: "$(API_BASE_URL)"
```

xcodegen + Xcode substitute `$(API_BASE_URL)` from `Local.xcconfig` at build time into the generated Info.plist.

- [ ] **Step 3: Commit**

```bash
git add ios/project.yml
git commit -m "feat(ios): wire Local.xcconfig + Info.plist API_BASE_URL substitution"
```

### Task A5.2: Update `APIClient.swift` to read from Bundle

**Files:**
- Modify: `ios/CISOCopilot/Services/APIClient.swift:8`

- [ ] **Step 1: Replace the literal with Bundle lookup**

```swift
// BEFORE (lines 6-9):
@Observable
final class APIClient {
    static let baseURL = URL(string: "https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1")!

// AFTER:
@Observable
final class APIClient {
    static let baseURL: URL = {
        guard let s = Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL") as? String,
              !s.isEmpty,
              let u = URL(string: s) else {
            fatalError("API_BASE_URL missing or invalid in Info.plist. " +
                       "Copy ios/Local.xcconfig.example to ios/Local.xcconfig and rebuild.")
        }
        return u
    }()
```

- [ ] **Step 2: Verify zero hits for the literal in iOS source**

```bash
grep -rn "xoljryrb7i" ios/CISOCopilot/
```

Expected: zero hits.

- [ ] **Step 3: Commit**

```bash
git add ios/CISOCopilot/Services/APIClient.swift
git commit -m "refactor(ios): APIClient.baseURL reads from Info.plist (API_BASE_URL)"
```

### Task A5.3: Regenerate + build + install + smoke-test

- [ ] **Step 1: Regenerate xcodeproj**

```bash
cd ios && xcodegen generate 2>&1 | tail -5
```

Expected: no errors; `CISOCopilot.xcodeproj` regenerated with the new build settings.

- [ ] **Step 2: Build for device**

```bash
xcodebuild build \
  -project CISOCopilot.xcodeproj -scheme CISOCopilot \
  -destination "id=00008140-001E104E3A9B001C" \
  -derivedDataPath build-device \
  -allowProvisioningUpdates 2>&1 | tail -20
```

Expected: `BUILD SUCCEEDED`.

- [ ] **Step 3: Install on KK's device**

```bash
xcrun devicectl device install app \
  --device 00008140-001E104E3A9B001C \
  build-device/Build/Products/Debug-iphoneos/CISOCopilot.app 2>&1 | tail -5
```

Expected: install succeeds.

- [ ] **Step 4: Smoke-test on device**

1. Launch app → tap Sign in
2. Cognito hosted UI loads (proves `APIClient.baseURL` is reading the correct URL from Info.plist)
3. Complete Google sign-in → land on home screen
4. Trigger an event that should fire an APNs push (any drift event on the test account)
5. Confirm push lands

- [ ] **Step 5: Commit (records the smoke event)**

```bash
git commit --allow-empty -m "verify(ios): Slice A5 — device build + sign-in + APNs round-trip OK"
```

---

## Slice A6 — CFN templates + test fixtures

**Goal:** customer-downloadable CFN templates carry no `Default:` values that leak KK's identifiers (the onboarding Lambda already passes explicit parameters via deep-link); test fixtures use `999999999999` instead of the real account ID.

### Task A6.1: Strip Defaults from `platform/cfn/aws-onboard.yaml`

**Files:**
- Modify: `platform/cfn/aws-onboard.yaml:13,30,35`

- [ ] **Step 1: Remove the three `Default:` lines**

```yaml
# BEFORE (lines 11-16):
CisoCopilotAccountId:
  Type: String
  Default: "470226123496"
  Description: AWS account ID that hosts the CISO Copilot platform. Do not change.
  AllowedPattern: "^[0-9]{12}$"

# AFTER:
CisoCopilotAccountId:
  Type: String
  Description: AWS account ID that hosts the CISO Copilot platform. Passed by the onboarding flow.
  AllowedPattern: "^[0-9]{12}$"
```

Repeat for `CentralEventBusArn` (line 28-31) and `CompleteWebhookUrl` (line 33-36) — drop their `Default:` lines.

- [ ] **Step 2: Verify the onboarding deep-link still passes them**

The deep-link function `_build_cfn_deep_link` in `platform/lambda/onboarding_aws_initiate/main.py:101-117` already passes all three as `param_*` query-string args. Confirm with a grep:

```bash
grep -n "param_CisoCopilotAccountId\|param_CentralEventBusArn\|param_CompleteWebhookUrl" \
  platform/lambda/onboarding_aws_initiate/main.py
```

Expected: three hits, all in `_build_cfn_deep_link`.

- [ ] **Step 3: Redeploy the CDN bucket so customers see the new template**

```bash
cd platform && npx cdk deploy CisoCopilotStatic --require-approval never 2>&1 | tail -10
```

Expected: `UPDATE_COMPLETE` (`BucketDeployment` re-uploads `aws-onboard.yaml`).

- [ ] **Step 4: Commit**

```bash
git add platform/cfn/aws-onboard.yaml
git commit -m "refactor(cfn): aws-onboard.yaml drops hardcoded Defaults

Onboarding Lambda always passes explicit parameters via the
CloudFormation Console deep-link; the Defaults were a fallback
for direct-Console use and they leak KK's identifiers."
```

### Task A6.2: Verify Azure + GCP scripts already fall back via env

**Files:**
- Read/Modify: `platform/cfn/azure/onboard.sh:34`, `platform/cfn/gcp/onboard.sh:64-65`

- [ ] **Step 1: Inspect current fallback logic**

```bash
sed -n '30,40p' platform/cfn/azure/onboard.sh
sed -n '60,70p' platform/cfn/gcp/onboard.sh
```

Both files use `${CISO_COMPLETE_URL:-...}` fallback pattern.

- [ ] **Step 2: Strip the hardcoded fallback URL — require the env var**

In `platform/cfn/azure/onboard.sh:34`:

```bash
# BEFORE:
COMPLETE_URL="${CISO_COMPLETE_URL:-https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1/onboarding/azure/complete}"

# AFTER:
COMPLETE_URL="${CISO_COMPLETE_URL:?CISO_COMPLETE_URL must be set — the onboarding flow passes this automatically}"
```

(Same pattern for `gcp/onboard.sh:64`. For `AWS_ACCOUNT_ID` at line 65 of gcp/onboard.sh, do the same `:?error` pattern.)

- [ ] **Step 3: Verify the onboarding initiate Lambdas set these vars in the generated script**

```bash
grep -n "CISO_COMPLETE_URL\|AWS_ACCOUNT_ID" \
  platform/lambda/onboarding_azure_initiate/main.py \
  platform/lambda/onboarding_gcp_initiate/main.py
```

Confirm both Lambdas inject the values into the generated script body. If not, that's a real bug — fix before deploying.

- [ ] **Step 4: Redeploy CDN + commit**

```bash
cd platform && npx cdk deploy CisoCopilotStatic --require-approval never
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/cfn/azure/onboard.sh platform/cfn/gcp/onboard.sh
git commit -m "refactor(cfn): Azure/GCP onboard scripts require env var (no defaults)"
```

### Task A6.3: Replace `470226123496` in test fixtures

**Files:**
- Modify: `platform/lambda/ai_scanner/tests/test_unified_writer.py:123,202-203`
- Modify: `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/expected.json`
- Modify: `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/repo/.github/workflows/deploy.yml:13`
- Modify: `platform/lambda/soc_enrichment/tests/conftest.py:34-35`
- Modify: `platform/lambda/event_router/tests/conftest.py:14,21,27,40,50`

- [ ] **Step 1: Bulk-replace across all fixture files**

```bash
cd /Users/kkmookhey/Projects/CISOBrief && \
  grep -rl "470226123496" platform/lambda/*/tests/ | \
  xargs sed -i.bak 's/470226123496/999999999999/g'

# Clean up .bak files
find platform/lambda/*/tests/ -name "*.bak" -delete
```

- [ ] **Step 2: Verify**

```bash
grep -rn "470226123496" platform/lambda/
```

Expected: zero hits.

- [ ] **Step 3: Run all affected test suites**

```bash
pytest platform/lambda/ai_scanner/tests/ \
       platform/lambda/soc_enrichment/tests/ \
       platform/lambda/event_router/tests/ 2>&1 | tail -10
```

Expected: same pass count as before. If anything fails, investigate — the fixture data might be referenced by `assertEqual` against a literal `470226...` somewhere we missed.

- [ ] **Step 4: Commit**

```bash
git add platform/lambda/ai_scanner/tests/ \
        platform/lambda/soc_enrichment/tests/ \
        platform/lambda/event_router/tests/
git commit -m "test: replace real AWS account ID in fixtures with 999999999999"
```

---

## Slice A7 — Final verification + Tier 4 hygiene

**Goal:** the success criteria from the spec are all met. Loose secret files relocated. `workers/` deleted. HANDOFF updated.

### Task A7.1: Final grep audit

- [ ] **Step 1: Source-code-only grep for account ID**

```bash
cd /Users/kkmookhey/Projects/CISOBrief && \
grep -rEn "\b470226123496\b" \
  --include="*.ts" --include="*.tsx" --include="*.js" --include="*.py" \
  --include="*.yaml" --include="*.yml" --include="*.sh" --include="*.swift" \
  --include="*.json" \
  platform/lib/ platform/bin/ platform/lambda/ platform/scripts/ \
  platform/cfn/ web/src/ ios/CISOCopilot/ scripts/ 2>/dev/null
```

Expected: zero hits. (`platform/cdk.context.json` is excluded per spec Section 3 non-goals; if it appears in the search, that's the documented acceptance.)

- [ ] **Step 2: Source-code-only grep for API Gateway ID**

```bash
grep -rEn "\bxoljryrb7i\b" \
  --include="*.ts" --include="*.tsx" --include="*.js" --include="*.py" \
  --include="*.yaml" --include="*.yml" --include="*.sh" --include="*.swift" \
  platform/lib/ platform/bin/ platform/lambda/ platform/cfn/ \
  web/src/ ios/CISOCopilot/ scripts/ 2>/dev/null
```

Expected: zero hits.

- [ ] **Step 3: Web-only grep for personal emails**

```bash
grep -rEn "kkmookhey@" web/src/
```

Expected: zero hits.

If any of these three returns hits, fix immediately before the gate closes — there's a missed extraction.

### Task A7.2: Relocate loose secret files

- [ ] **Step 1: Create the target directory**

```bash
mkdir -p ~/.shasta/secrets
```

- [ ] **Step 2: Move the files**

```bash
cd /Users/kkmookhey/Projects/CISOBrief && \
  mv AuthKey_8AJ3RT8CCA.p8 ~/.shasta/secrets/ && \
  mv .env.key             ~/.shasta/secrets/ && \
  mv ciso-copilot.2026-05-18.private-key.pem ~/.shasta/secrets/ && \
  ls ~/.shasta/secrets/
```

Expected: `AuthKey_8AJ3RT8CCA.p8`, `.env.key`, `ciso-copilot.2026-05-18.private-key.pem` all listed.

- [ ] **Step 3: Move `.env` from repo root if it exists there separately**

```bash
ls -la /Users/kkmookhey/Projects/CISOBrief/.env 2>&1
```

If it exists at repo root (i.e., NOT inside `platform/`), inspect its contents — it may be Cloudflare worker `.dev.vars`-equivalent left over from v1. If so, move to `~/.shasta/secrets/v1.env`. If not relevant any longer, delete.

- [ ] **Step 4: Verify nothing in source references the old root-level paths**

```bash
grep -rEn "AuthKey_8AJ3RT8CCA|ciso-copilot\.2026-05-18\.private-key" \
  --include="*.ts" --include="*.py" --include="*.sh" --include="*.swift" \
  --include="*.toml" --include="*.json" \
  /Users/kkmookhey/Projects/CISOBrief/ 2>/dev/null
```

Expected: zero hits. (Anything that needs those files is already reading from outside the repo — they were never path-referenced inside the codebase, only deposited locally for occasional manual use.)

### Task A7.3: Delete `workers/`

- [ ] **Step 1: Confirm no imports from workers/ in active code**

```bash
grep -rn "from.*workers\|import.*workers\|require.*workers" \
  --include="*.ts" --include="*.tsx" --include="*.js" --include="*.py" \
  platform/ web/ ios/ scripts/ 2>/dev/null
```

Expected: zero hits.

- [ ] **Step 2: Delete the directory**

```bash
rm -rf /Users/kkmookhey/Projects/CISOBrief/workers/
```

- [ ] **Step 3: Commit**

```bash
git add -A workers/
git commit -m "chore: remove v1 sunset workers/ directory

Cloudflare Workers v1 was deployed at ciso-copilot.kkmookhey.workers.dev
and superseded by v2 (AWS-native). The directory contributed hardcoded
identifier references to the secrets audit and contained no active code.
Recoverable from git history."
```

### Task A7.4: Update HANDOFF.md with the Slice A shipped block

**Files:**
- Modify: `HANDOFF.md`

- [ ] **Step 1: Insert a new shipped block at the top**

After the existing "🚀 Phase 1 team-ready" block, insert:

```markdown
## 🚀 Phase 2 Slice A — Secrets / hardcoded-identifier extraction (2026-05-26)

Last code-side gate before the MIT-public flip. Every hardcoded AWS account ID,
API Gateway URL, ARN, personal email, and redirect URI in `platform/lib/`,
`platform/bin/`, `platform/lambda/onboarding_*/`, `web/src/`, `ios/`, and
`scripts/` now reads from env-var configuration. A new operator copies
`.env.example` → real, `cdk deploy`, gets a working deployment with their
own AWS account.

**What's live and deployed:**
- `platform/.env` augmented with `AWS_ACCOUNT_ID`, `SHASTA_DOMAIN`,
  `API_BASE_URL`, `WEB_REDIRECT_URI`, `APP_DOMAIN`, `ADMIN_EMAILS`,
  `APNS_PLATFORM_APP_ARN`, `APP_CERT_ARN`, `LEGACY_APP_DOMAIN`,
  `DB_CLUSTER_ARN`, `DB_SECRET_ARN`. `platform/lib/config.ts` exposes
  them via the existing `required()` helper.
- `web/.env.production` (gitignored) + `web/src/lib/env.ts` Vite boundary.
- `ios/Local.xcconfig` (gitignored) + Info.plist substitution; `APIClient.baseURL`
  reads from `Bundle.main.infoDictionary`.
- `/me` now emits `is_admin: bool` computed server-side from `ADMIN_EMAILS`.
  Both `web/src/{chat,routes}/Shell.tsx` drop their local allowlist
  constants and consume `me.user.is_admin`. Personal emails are out of the
  production JS bundle.
- `platform/cfn/aws-onboard.yaml` drops the three hardcoded `Default:`
  values; the onboarding deep-link in `onboarding_aws_initiate` always
  passed them explicitly, so customer onboarding is unchanged.
  Azure + GCP onboard scripts now require `CISO_COMPLETE_URL` / `AWS_ACCOUNT_ID`
  (the initiate Lambdas already inject them).
- Test fixtures use `999999999999` instead of the real account ID across
  `ai_scanner/`, `soc_enrichment/`, `event_router/` test suites.
- Loose `.p8`/`.pem`/`.env` files relocated from repo root to
  `~/.shasta/secrets/`. `workers/` directory deleted (v1 sunset).

**Final grep audit:** zero hits for `470226123496`, `xoljryrb7i`, or
`kkmookhey@` in any source file under `platform/lib/ platform/bin/
platform/lambda/ platform/cfn/ web/src/ ios/CISOCopilot/ scripts/`. The
only remaining account-ID leak is `platform/cdk.context.json` (standard
CDK practice; accepted per spec §3 non-goals) and `docs/superpowers/`
historical specs/plans (Tier 2 doc sanitization is a separate session
before the MIT flip).

**Deferred to a later session:**
- Tier 2 — sanitize `HANDOFF.md`, `TEST_PLAN.md`, `CLAUDE.md`,
  `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md`.
  Line-by-line redaction across ~30 files.

**Spec:** `docs/superpowers/specs/2026-05-26-secrets-extraction-design.md`
**Plan:** `docs/superpowers/plans/2026-05-26-secrets-extraction-plan.md`
**Branch:** `<feat/secrets-extraction or main>`
**Verify:** `TEST_PLAN.md` post-A7 grep gate verified
```

- [ ] **Step 2: Update the "Last updated" line at the top of HANDOFF.md**

Change the date string to `2026-05-26` + "Secrets extraction shipped" note.

- [ ] **Step 3: Commit**

```bash
git add HANDOFF.md
git commit -m "docs(handoff): Phase 2 Slice A — secrets extraction shipped"
```

### Task A7.5: Final smoke pass

- [ ] **Step 1: Sign-in via fresh incognito on `shasta.transilience.cloud`**

Confirms web bundle (deployed in A4) works against deployed API (A2 + A3).

- [ ] **Step 2: iOS smoke** (carried over from A5.4)

Confirms `Local.xcconfig` survives a fresh `xcodegen` regeneration.

- [ ] **Step 3: One end-to-end test scan**

Trigger a scan from `/connect` → confirm it completes → confirm findings show in `/findings` → confirm `/ai` and `/soc` populate as before. This is the regression test that A1-A6 didn't silently break anything.

If anything fails, surface and fix BEFORE declaring Slice A done.

---

## Out of scope (handled in a later session)

- **Tier 2 doc sanitization** — `HANDOFF.md`, `TEST_PLAN.md`, `CLAUDE.md`,
  `docs/superpowers/specs/*.md`, `docs/superpowers/plans/*.md`. Each file
  reviewed line-by-line; sensitive identifiers replaced with placeholders
  or moved to a redacted-internal-docs branch.
- **`platform/cdk.context.json`** — accepted leak per spec §3 (standard CDK practice).
- **Capability gating + billing module** — separate Phase 2 work items.
