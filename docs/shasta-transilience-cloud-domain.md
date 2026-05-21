# Adding `shasta.transilience.cloud` as the app domain

> Cutover record for pointing `shasta.transilience.cloud` at the CISO Copilot
> web SPA. `shasta.transilience.cloud` is the **canonical** domain;
> `app.settlingforless.com` was a stop-gap and is kept only as a fallback.
> Written 2026-05-21.

**Status:** DNS + TLS live (§1). Codebase changes implemented (§2). Deploy
pending (§3).

---

## 1. DNS / CloudFront / TLS — DONE & verified

| Item | Detail |
|---|---|
| `transilience.cloud` CAA | Co-founder added `0 issue "amazon.com"` so AWS's CA is permitted |
| ACM certificate | `…/certificate/28690c41-24bc-4eb8-b925-87820a2fb605` — SAN `app.settlingforless.com` + `shasta.transilience.cloud`, **ISSUED**, valid to 2026-12-04, auto-renews |
| CloudFront distribution | `E2FV1Z0DJ4RQS4` (`dil1ztnjosz43.cloudfront.net`) — alias `shasta.transilience.cloud` added, new cert attached, **Deployed** |
| DNS CNAME (Vercel) | `shasta` → `dil1ztnjosz43.cloudfront.net` — resolves on Google + Cloudflare; `HTTP/2 200`, valid TLS verified |
| Old failed cert | `19d52f40-…` (failed `CAA_ERROR`) deleted |

The cert + CloudFront changes were made directly via AWS CLI — **not** by
CDK. §2.1 brings the CDK back in sync so the next deploy doesn't revert them.

---

## 2. Codebase changes — IMPLEMENTED

Decision: **Option A** — `shasta.transilience.cloud` is the single canonical
domain. All redirect URIs point there. `app.settlingforless.com` callbacks
are retained as a fallback but no longer the target of generated redirects.

### 2.1. CloudFront CDK drift — `platform/lib/static-stack.ts`
`APP_CERT_ARN` → new cert `28690c41-…`; added `SHASTA_DOMAIN`;
`domainNames` → `[SHASTA_DOMAIN, APP_DOMAIN]`. CDK now matches the live
distribution, so deploying `CisoCopilotStatic` is an idempotent no-op for
CloudFront rather than a revert.

### 2.2. Cognito web client callback URLs — `platform/lib/auth-stack.ts`
Added `https://shasta.transilience.cloud/callback` to `callbackUrls` and
`https://shasta.transilience.cloud/` to `logoutUrls` on the web client.
Cognito exact-matches `redirect_uri`; without this, sign-in on the new
domain fails with `redirect_uri mismatch`.

### 2.3. Email-first sign-in redirect — `platform/bin/platform.ts`
`webRedirectUri` → `https://shasta.transilience.cloud/callback`. This feeds
the `auth_discover` lambda's `WEB_REDIRECT_URI`, which builds the Cognito
authorize URL for the `/auth/discover-tenant` path.

### 2.4. `auth_discover` fallback default — `platform/lambda/auth_discover/main.py`
`WEB_REDIRECT_URI` env-var default → `https://shasta.transilience.cloud/callback`
(was a stale `dil1ztnjosz43.cloudfront.net` URL). Cosmetic — CDK always sets
the env var — but keeps the default consistent.

### 2.5. GitHub App connector landing — `platform/lib/api-stack.ts`
`WEB_CALLBACK_URL` → `https://shasta.transilience.cloud/ai/install/callback`.
Post-install landing page for the AI-discovery GitHub flow. The GitHub App's
*own* registered Callback/Setup URL (in GitHub App settings) is separate and
likely points at the API Gateway — **verify there**, but no code change for it.

Verified: `cdk synth CisoCopilotStatic CisoCopilotAuth CisoCopilotApi`
succeeds; `auth_discover/main.py` compiles.

---

## 3. Remaining — deploy

The code changes take effect only once deployed. From `platform/`:

```bash
# 1. Sanity-check the diff first — CisoCopilotStatic should show cert +
#    aliases changing to values the live distribution ALREADY has (no-op);
#    Auth/Api show the callback-URL / env-var changes.
npx cdk diff CisoCopilotStatic CisoCopilotAuth CisoCopilotApi

# 2. Deploy.
npx cdk deploy CisoCopilotStatic --require-approval never            # CloudFront — ~5 min, idempotent
npx cdk deploy CisoCopilotAuth   --require-approval never            # Cognito callback URLs (NOT hotswappable)
npx cdk deploy CisoCopilotApi    --require-approval never --hotswap  # env vars only — fast
```

> ⚠️ Deploy `CisoCopilotApi` from a branch that has the **intended**
> api-stack.ts state. The in-flight `feat/aws-scanner-uplift` branch also
> modifies `api-stack.ts` and `bin/platform.ts`; deploying this domain
> branch (which is `main` + the domain delta) would not include scanner
> changes. Coordinate the merge order.

> ⚠️ Deploying `CisoCopilotAuth` updates the Cognito web-client resource.
> The `auth_discover` lambda lazily adds per-tenant `MS-<tenant>` IdPs to
> the client's `SupportedIdentityProviders` at runtime; a CloudFormation
> update of that resource can reset it to the CDK-declared list, dropping
> the runtime-added IdPs. They self-heal on each tenant's next
> `/auth/discover-tenant` call, but expect a brief window.

Then verify end-to-end on `https://shasta.transilience.cloud`:
- Direct sign-in (`startSignIn`) and email-first sign-in
  (`discoverTenantAndSignIn`) both complete and land back on
  `shasta.transilience.cloud`.
- Sign-out returns to `shasta.transilience.cloud`.

---

## 4. Entra & Google configs — NO change needed

The federation is indirected through Cognito:

```
SPA → Cognito Hosted UI → Entra / Google IdP → Cognito → SPA /callback
      (ciso-copilot.auth.us-east-1.amazoncognito.com)
```

The Entra and Google apps only ever redirect back to **Cognito**, never to
the app domain. Their registered redirect URI is Cognito's fixed endpoint
`https://ciso-copilot.auth.us-east-1.amazoncognito.com/oauth2/idpresponse`,
which does not change when an app domain is added.

| External config | Registered redirect URI | Change for `shasta`? |
|---|---|---|
| Entra app reg — user sign-in (`ENTRA_CLIENT_ID`) | Cognito `/oauth2/idpresponse` | **No** |
| Google OAuth client (`GOOGLE_CLIENT_ID`) | Cognito `/oauth2/idpresponse` | **No** |
| Entra app reg — cloud scanning (`ENTRA_APP_ID`) | API Gateway `…/v1/onboarding/entra/callback` | **No** — API domain, not app domain |
| GCP onboarding | Workload Identity Federation — no OAuth redirect | **No** |
| GitHub App (`ciso-copilot`) | Verify in GitHub App settings (likely API Gateway) | Verify only — see §2.5 |

The only thing that would force an Entra/Google change is moving the Cognito
**Hosted UI** itself to a custom domain (e.g. `auth.transilience.cloud`) —
not part of this cutover.
