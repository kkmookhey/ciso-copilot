# Secrets / Hardcoded-Identifier Extraction (Phase 2, Slice A) — Design Spec

> Extracts every per-deployment identifier (AWS account ID, API Gateway URL,
> ARNs, admin emails) from source code into env-var configuration. The last
> code-side gate before the MIT-licensed `CISOBrief` repo can flip public,
> per `ROADMAP.md` Phase 2.
>
> Sister doc: the 2026-05-26 audit catalog in the same chat session that
> enumerated Tier 1-4 leaks. This spec implements Tier 1 + Tier 4 hygiene;
> Tier 2 (doc sanitization) is explicitly deferred.
>
> Date: 2026-05-26
> Status: brainstorm-approved by KK on 2026-05-26; awaiting written-spec
> review before the implementation plan is written.

---

## 1. What we are building

A three-file env-var configuration surface — `platform/.env`, `web/.env.production`,
`ios/Local.xcconfig` — each gitignored with a committed `.example` companion.
Every CDK stack, every web bundle constant, every iOS API endpoint, and every
operational script reads its per-deployment identifiers from these files
through a `requireEnv()` boundary that fails loudly on missing values.

Two architectural changes ride along:

1. **Admin allowlist moves server-side.** The current hardcoded `ADMIN_EMAILS`
   array in `web/src/{chat,routes}/Shell.tsx` is dropped. The server adds
   `is_admin: bool` to its existing user/session response, computed from the
   `ADMIN_EMAILS` env var it already reads. Web consumes `me.is_admin`.
2. **CFN customer-onboarding templates lose their hardcoded `Default:` values.**
   `platform/cfn/aws-onboard.yaml`, `azure/onboard.sh`, `gcp/onboard.sh` get
   parameter substitution at upload time from the onboarding Lambda; templates
   in source contain placeholders only.

Plus Tier 4 hygiene: loose `.p8`/`.pem`/`.env` files at repo root relocate to
`~/.shasta/secrets/`; the v1 sunset `workers/` directory is deleted.

## 2. Why this is needed

The 2026-05-26 audit found:

- **Zero live secret material in git** (`.gitignore` correctly covers
  `*.p8`/`*.pem`/`.env`/`*.key`). No AWS access keys, no API tokens, no
  private keys in tracked source.
- **Single-tenant identifiers leaked across production code**: AWS account
  `470226123496` in 15 source files; API Gateway ID `xoljryrb7i` in 10
  source files; APNS Platform App ARN + ACM cert ARN as literal constants
  in CDK; KK's three personal email addresses in two web client files +
  one CDK file.

For the MIT-public flip, the code needs to be operator-agnostic. A new
operator should be able to `cp platform/.env.example platform/.env`, edit
their values, `pnpm cdk deploy`, and have a working Shasta deployment with
their own account. That's the success criterion. No constants in source
need to change between deployments.

## 3. Scope and non-goals

**In scope (Slice A, this spec):**

1. Tier 1 code extraction: every hardcoded account ID, API Gateway URL, ARN,
   redirect URL, app domain, and admin email in `platform/lib/`, `platform/bin/`,
   `platform/scripts/`, `scripts/`, `web/src/`, `ios/`, and `platform/cfn/`
   moves to env vars.
2. The `is_admin` refactor (server emits flag; web consumes flag).
3. CFN onboarding templates parameterize their previously-hardcoded defaults.
4. Test fixtures containing `470226123496` change to `999999999999`.
5. Tier 4 hygiene: relocate loose secret files, delete `workers/`.
6. Final grep-audit verifying zero hits for `470226123496` and `xoljryrb7i`
   in source code paths (docs/specs/plans excluded).

**Explicitly out of scope (deferred to a later session):**

- **Tier 2 doc sanitization.** `HANDOFF.md`, `TEST_PLAN.md`, `CLAUDE.md`,
  and ~30 historical files in `docs/superpowers/plans/` + `specs/` contain
  many literal account IDs and ARNs. Redacting these line-by-line is its
  own session; the MIT flip can include either a redacted-docs branch or
  an `internal-docs/` exclusion at publish time.
- `platform/cdk.context.json` leaks the account ID via cached AZ lookups.
  Standard CDK practice is to commit this file. We accept this single leak
  rather than disabling the cache.
- New tests for the refactored surfaces. The existing test suites validate
  behavior is unchanged; this is refactor-only.
- Capability gating + billing module (Phase 2 work items 1+2). Slice A
  delivers ROADMAP Phase 2 item 4 only.

## 4. Configuration surface

Three env files, each consumed differently:

| File | Gitignored | Consumer | Mechanism |
|---|---|---|---|
| `platform/.env` | yes (existing) | CDK app (synth-time) + Python Lambdas (runtime) | `dotenv.config()` at top of `platform/bin/platform.ts`; CDK passes selected values to Lambda env vars via stack props |
| `web/.env.production` | yes (new) | Vite build (`pnpm build`) | `import.meta.env.VITE_*` substitution at build time |
| `ios/Local.xcconfig` | yes (new) | Xcode build | Info.plist `${API_BASE_URL}` substitution → `Bundle.main.object(forInfoDictionaryKey:)` in `APIClient.swift` |

Each gets a committed `.example` companion documenting variable names with
placeholder values:

```
platform/.env.example       (committed, placeholder values)
platform/.env               (gitignored, real values)
web/.env.example            (committed, placeholder values)
web/.env.production         (gitignored, real values)
ios/Local.xcconfig.example  (committed, placeholder values)
ios/Local.xcconfig          (gitignored, real values)
```

`.gitignore` already covers `.env`; we add `web/.env.*` (except `.example`)
and `ios/Local.xcconfig`.

## 5. Variable catalog

### `platform/.env`

```
# AWS deployment target
AWS_ACCOUNT_ID=470226123496
AWS_REGION=us-east-1

# Public-facing identifiers
SHASTA_DOMAIN=shasta.transilience.cloud
API_BASE_URL=https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1
WEB_REDIRECT_URI=https://shasta.transilience.cloud/callback
APP_DOMAIN=https://shasta.transilience.cloud

# Admin allowlist (server-side gate; client receives is_admin flag)
ADMIN_EMAILS=kkmookhey@gmail.com,kkmookhey@transilience.ai,kkmookhey@networkintelligence.ai

# Pre-existing per-deployment ARNs
APNS_PLATFORM_APP_ARN=arn:aws:sns:us-east-1:470226123496:app/APNS_SANDBOX/CISOCopilotAPNSSandbox
APP_CERT_ARN=arn:aws:acm:us-east-1:470226123496:certificate/28690c41-24bc-4eb8-b925-87820a2fb605

# Operational scripts
APPROVAL_RECIPIENT=kkmookhey@gmail.com

# Pre-existing (already in platform/.env per CLAUDE.md): ENTRA_*, GOOGLE_*, DOMAIN
```

### `web/.env.production`

```
VITE_API_BASE_URL=https://xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1
VITE_APP_DOMAIN=https://shasta.transilience.cloud
```

No `VITE_ADMIN_EMAILS` — the client receives `is_admin` from the server.

### `ios/Local.xcconfig`

```
API_BASE_URL = https:/$()/xoljryrb7i.execute-api.us-east-1.amazonaws.com/v1
```

(The `/$()` is Xcode's escape for `//` in xcconfig files; without it, Xcode
interprets `//` as a comment.)

## 6. Admin allowlist refactor

**Current state (the smell):**

- `platform/lib/api-stack.ts:347` sets the `ADMIN_EMAILS` env var on the
  appropriate Lambda(s) — server has the truth.
- `web/src/chat/Shell.tsx:22-24` and `web/src/routes/Shell.tsx:40-42`
  duplicate the allowlist as a TypeScript `const`. Used purely to decide
  whether to show admin nav items. Bypassable by anyone editing the bundled JS;
  not a security boundary.

**Target state:**

1. Identify the existing user/session endpoint that returns the current user's
   email and tenant_id (likely an `/auth/whoami` or piggybacked on
   `/connections` — investigate during planning).
2. That endpoint adds `is_admin: bool` to its JSON response, computed from
   `ADMIN_EMAILS.split(',').includes(claims.email)`.
3. Web `Shell.tsx` (both copies) drops the `ADMIN_EMAILS` constant. The
   `me` query result includes `is_admin`; admin nav items render conditionally
   on that flag.

**Result:** zero personal emails in the production JS bundle. Server-side gate
remains the actual security boundary (unchanged).

## 7. Fail-fast on missing values

A tiny helper at every boundary:

```typescript
// platform/lib/_config.ts
import dotenv from "dotenv";
dotenv.config({ path: path.join(__dirname, "../.env") });

export function requireEnv(name: string): string {
  const v = process.env[name];
  if (!v) {
    throw new Error(
      `Missing required env var: ${name}. ` +
      `Set in platform/.env (see platform/.env.example).`,
    );
  }
  return v;
}
```

`platform/bin/platform.ts` imports `requireEnv` and uses it for every value.
Same pattern for the Python helpers in `scripts/` and `platform/scripts/`.

The web build inherits Vite's behavior: `import.meta.env.VITE_API_BASE_URL`
is `undefined` if not set, and a tiny `requireEnv` wrapper in `web/src/lib/env.ts`
throws at module load if any required `VITE_*` is missing.

The iOS build fails at Info.plist substitution if `Local.xcconfig` is missing
(Xcode error). A clear comment in `Local.xcconfig.example` points the operator
at the `cp` step.

## 8. Slice sequence

Seven self-contained slices, each verifiable before the next:

### Slice A1 — Foundation (additive only)

- Create `platform/.env.example`, `web/.env.example`, `ios/Local.xcconfig.example`
- Add the `requireEnv()` helper (TypeScript + Python variants)
- Add `dotenv` to `platform/package.json`
- Update `.gitignore` for `web/.env.*` (except `.example`) and `ios/Local.xcconfig`
- Update `web/src/lib/env.ts` with the `VITE_*` validator (created but not yet consumed)

**Verify:** `pnpm cdk synth` produces the same CFN template as before
(no consumers changed yet); `pnpm test` in `web/` still green.

### Slice A2 — CDK + scripts migration

- Migrate `platform/bin/platform.ts` to read via `requireEnv()`
- Migrate `platform/lib/static-stack.ts`, `events-stack.ts`, `api-stack.ts`,
  `auth-stack.ts` to read from `process.env` (no hardcoded ARNs/URLs/emails)
- Migrate `scripts/send_approval_email.py` and `platform/scripts/migrate_to_entities.py`
  to env vars (or delete if obsolete — investigate during planning)

**Verify:**
- `pnpm cdk synth` diff vs the Slice-A1 baseline shows only source-of-value
  changes, no resource changes
- `pnpm cdk diff` against the deployed stacks shows no changes
- Deploy: `npx cdk deploy <all-affected-stacks>` and smoke-test sign-in
  (Google + Microsoft federation), Dashboard, `/ai`, `/soc`

### Slice A3 — Server emits `is_admin`

- Find the current user/session endpoint (planning step locates it)
- Add `is_admin: bool` to its JSON response
- Update its TypeScript response type in `web/src/lib/api.ts`
- Unit test the endpoint with admin + non-admin claim fixtures

**Verify:** pytest on the endpoint's tests; `curl -H "Authorization: Bearer ..."`
with KK's token returns `is_admin: true`.

### Slice A4 — Web migration

- Create `web/.env.production` with KK's real values (mirroring the
  `.example` committed in A1)
- Migrate `web/src/lib/api.ts`, `web/src/lib/cognito.ts`,
  `web/src/chat/{chatApi,voiceClient,turnQueue}.ts`,
  `web/src/routes/TrustPublic.tsx` to `import.meta.env.VITE_*`
- Drop `ADMIN_EMAILS` from `web/src/chat/Shell.tsx` and `web/src/routes/Shell.tsx`;
  read `me.is_admin` instead

**Verify:**
- `pnpm test` in `web/` (passes — admin-nav tests adapt to the new `is_admin` source)
- `pnpm build`; grep the resulting `dist/` for `470226123496`, `xoljryrb7i`,
  `kkmookhey@` → all zero hits
- `pnpm dev` against staging; sign-in flow + admin nav + deep-link routes
- Deploy: `aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete`;
  `aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'`
- Production smoke test from a fresh browser

### Slice A5 — iOS migration

- Add `Local.xcconfig` reference to `ios/project.yml`; regenerate xcodeproj
- Info.plist gains `API_BASE_URL = $(API_BASE_URL)` substitution
- `ios/CISOCopilot/Services/APIClient.swift:8` reads from
  `Bundle.main.object(forInfoDictionaryKey: "API_BASE_URL")` instead of the
  hardcoded URL

**Verify:** device build + install on KK's iPhone 16 Pro Max + sign-in smoke
+ confirm an APNs push lands.

### Slice A6 — CFN templates + test fixtures

- `platform/cfn/aws-onboard.yaml`: remove `Default:` from `CentralAccountId`
  and `CentralEventBusArn` and `CompleteUrl`; keep parameter declarations
- `platform/cfn/azure/onboard.sh`, `gcp/onboard.sh`: strip hardcoded
  `AWS_ACCOUNT_ID`, `COMPLETE_URL` defaults — these scripts already accept
  values via env (`${CISO_COMPLETE_URL:-...}`); drop the fallback
- Investigate the onboarding Lambda that presigns the S3 URL for the CFN
  template; if it substitutes parameters at presign time, add account-ID
  substitution there
- Replace `470226123496` with `999999999999` in:
  - `platform/lambda/ai_scanner/tests/test_unified_writer.py`
  - `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/expected.json`
  - `platform/lambda/ai_scanner/tests/fixtures/crossdomain/with_oidc/repo/.github/workflows/deploy.yml`
  - `platform/lambda/soc_enrichment/tests/conftest.py`
  - `platform/lambda/event_router/tests/conftest.py`

**Verify:**
- `pytest platform/lambda/{ai_scanner,soc_enrichment,event_router}/tests/`
  all green
- Dry-run a customer onboarding flow (the CDN-served `aws-onboard.yaml`
  should render the customer's selected parameters, not KK's defaults)

### Slice A7 — Final verification + Tier 4 hygiene

- Final grep audit:
  ```
  grep -rEn "\b470226123496\b|\bxoljryrb7i\b|kkmookhey@" \
    --include="*.ts" --include="*.tsx" --include="*.js" --include="*.py" \
    --include="*.yaml" --include="*.yml" --include="*.sh" --include="*.swift" \
    --include="*.json" \
    platform/ web/ ios/ scripts/
  ```
  Expected: zero hits (test fixtures excluded — they now use `999999999999`)
- Move loose secret files from repo root to `~/.shasta/secrets/`:
  `AuthKey_8AJ3RT8CCA.p8`, `.env.key`, `ciso-copilot.2026-05-18.private-key.pem`,
  `.env` (any stray non-platform/.env)
- Delete `workers/` (v1 sunset; recoverable from git history)
- Update `HANDOFF.md` with the Slice A complete block

**Verify:**
- The final grep is clean
- `cdk deploy` still works after the loose-file relocation (anything CDK
  needs at deploy time is now in `platform/.env`, not loose at repo root)

## 9. Error handling

Three failure modes worth designing for:

1. **Missing env var.** `requireEnv()` throws at boundary. Operator sees
   exact variable name and pointer to `.env.example`. No silent fallback.
2. **`.env` file missing entirely.** `dotenv.config()` succeeds silently
   when no file is present (it just doesn't populate `process.env`).
   `requireEnv()` then catches the missing values on first use. Operator
   sees the same clear error.
3. **`.env.example` and `.env` drift over time.** Mitigation: a one-line
   pre-commit (manual for now) `diff <(grep -oE '^[A-Z_]+=' .env.example) <(grep -oE '^[A-Z_]+=' .env) | grep '^[<>]'`
   surfaces drift. Add to the Phase-2-final-audit checklist; don't
   automate yet.

## 10. Testing strategy

- **At every slice:** existing test suites must still pass (`pytest` across
  all `platform/lambda/*/tests/` directories; `pnpm test` in `web/`).
- **After Slice A2 deploy:** smoke-test sign-in (Google + Microsoft
  federation), Dashboard, `/ai`, `/soc`, `/connect` — manually from a fresh
  incognito window.
- **After Slice A4 deploy:** same smoke flows + verify admin nav renders for
  KK and does *not* render for any non-admin test account.
- **After Slice A5 build:** device install + sign-in + APNs push round trip.
- **After Slice A7:** final grep audit returns zero hits; full test suite
  green.

No new tests required — this is refactor-only.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| CDK env-var migration produces a different CFN template than today, silently changing prod | Slice A2 includes `cdk synth` diff check + `cdk diff` against deployed stack before any `cdk deploy` |
| Web deploy goes out with broken env-var substitution → live site breaks | Slice A4 includes `pnpm build` + bundle-grep verification + staging smoke before prod S3 sync |
| iOS Info.plist substitution typo → app fails to fetch any API → blank UI | Slice A5 verified on KK's device before any merge; the iOS app is single-device until App Store anyway |
| New operator runs `cdk deploy` without filling `.env` → cryptic error | `requireEnv()` throws with the exact missing-variable name and pointer to `.env.example` at synth time |
| Cleaning `workers/` loses something we still rely on | The v1 was deployed at `ciso-copilot.kkmookhey.workers.dev` and is sunset per CLAUDE.md. Final check before deletion: `grep -r "import.*workers" platform/ web/ ios/` should return zero hits |
| `git add -A` after a moved-loose-file relocation grabs the new `~/.shasta/secrets/` path | Files live outside the repo root entirely after relocation; impossible for `git add` from inside the repo to reach them |
| Tier 2 (doc sanitization) deferral means HANDOFF/specs/plans still leak the account ID when MIT flips | Acknowledged. The MIT-public flip itself is a separate event; doc sanitization is a pre-flip task that happens in its own session. Slice A's success criterion is "code clean," not "everything clean." |

## 12. Dependencies and ordering

```
A1 (Foundation)
  ↓
A2 (CDK + scripts) ←─── must deploy before web migration to validate the new env path
  ↓
A3 (Server is_admin)
  ↓
A4 (Web migration) ←─── depends on A3 endpoint to consume is_admin
  ↓
A5 (iOS migration) ←─── independent of A4 but cleaner to keep web stable first
  ↓
A6 (CFN + fixtures) ←─── independent; could run earlier but lands here for grouping
  ↓
A7 (Final verification + Tier 4 hygiene)
```

A3 must precede A4 (web reads the new server field). Otherwise the order is
risk-ascending (lowest blast first).

## 13. Success criteria

1. `grep -rEn "\b470226123496\b" platform/ web/ ios/ scripts/` returns zero
   hits in source files (test fixtures using `999999999999`; `cdk.context.json`
   excluded per non-goal #2)
2. `grep -rEn "\bxoljryrb7i\b" platform/ web/ ios/ scripts/` returns zero hits
3. `grep -rEn "kkmookhey@" web/src/` returns zero hits
4. `pnpm test` in `web/`, `pytest` across all Lambda directories all green
5. Production smoke tests pass for sign-in + Dashboard + `/ai` + `/soc` + admin
   nav (KK sees admin items; non-admin doesn't)
6. iOS device smoke: sign-in + APNs push round trip
7. The repo is `cp .env.example .env` + edit + `cdk deploy` deployable by a
   new operator with their own AWS account
