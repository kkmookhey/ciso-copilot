# Plan — recreate Cognito user pool with `email: mutable: true`

> Root cause of the web sign-in failure (`user.email: Attribute cannot be
> updated`): pool's `email` standard attribute is `mutable: false`. Cognito
> tries to sync email from the Google id_token on every fresh federated
> sign-in. Standard-attribute mutability cannot be changed in-place — the
> pool must be replaced.
>
> Today is testing+bugfix mode and we have 0 paying customers / 2 federated
> test users. Right time to do this.

## Blast radius (verified)

| Resource | Effect | Action |
|---|---|---|
| User Pool `us-east-1_ePRQ2iwZT` | Replaced (new ID) | CDK auto-creates new |
| Hosted UI domain prefix `ciso-copilot` | Same prefix on new pool | **Pre-delete** old domain so new can claim |
| iOS client `4vhj2avv7lgtu4jjbuusi7bjq2` | New ID | Update `ios/CISOCopilot/Services/AuthManager.swift:19` |
| Web client `1cauum3919ml3ppdnrijg532tm` | New ID | Update `web/src/lib/cognito.ts:8` |
| API Gateway authorizer | Auto re-binds (CDK reference) | `cdk deploy CisoCopilotApi` |
| `auth_discover` Lambda env vars (USER_POOL_ID, USER_POOL_CLIENT_ID) | Auto re-binds | `cdk deploy CisoCopilotApi` |
| Per-tenant MS IdPs (`MS-5519d10366...`) | Gone with old pool | Re-created lazily on next sign-in |
| Google + Microsoft IdPs (top-level) | Auto re-created | CDK |
| 2 federated users in old pool | Gone | Both must re-sign-in (clean first sign-up into new pool) |
| `users` DB rows | **Stable** — keyed on Google/Microsoft `sub`, not Cognito sub | None |
| `tenants` DB rows | Stable — keyed on email_domain | None |
| `cloud_connections`, `scans`, `findings` | Stable — keyed on tenant_id | None |
| KK's iOS session | Refresh token invalid → forced sign-out | KK re-signs in on iOS after deploy |

## Steps

### 1. Pre-delete the old Cognito domain
```bash
aws cognito-idp delete-user-pool-domain \
  --user-pool-id us-east-1_ePRQ2iwZT \
  --domain ciso-copilot --region us-east-1
```
This frees the `ciso-copilot` prefix. Old pool keeps existing (still RETAIN);
just loses Hosted UI URL until cleanup.

### 2. CDK change: flip mutability + removal policy
`platform/lib/auth-stack.ts`:
- Line 54: `email: { required: true, mutable: false }` → `email: { required: true, mutable: true }`
- Line 62: `removalPolicy: cdk.RemovalPolicy.RETAIN` → `removalPolicy: cdk.RemovalPolicy.DESTROY`

(DESTROY so the replaced pool actually gets cleaned up; otherwise we
accumulate orphan pools.)

### 3. Deploy auth stack
```bash
cd platform
npx cdk deploy CisoCopilotAuth --require-approval never
```
CFN creates the new pool with the same `ciso-copilot` prefix (now free),
new clientIds, fresh Google + Microsoft top-level IdPs. Records the new
IDs in CloudFormation outputs. Old pool deleted (DESTROY).

### 4. Deploy API stack (re-binds authorizer + Lambda env)
```bash
npx cdk deploy CisoCopilotApi --require-approval never
```

### 5. Capture new IDs
```bash
aws cloudformation describe-stacks --stack-name CisoCopilotAuth \
  --query 'Stacks[0].Outputs' --region us-east-1
```
Note the new `UserPoolId`, `UserPoolClientId` (iOS), `WebClientId`.

### 6. Update web clientId + redeploy
- Edit `web/src/lib/cognito.ts:8` → new `WebClientId`.
- ```bash
  cd web && pnpm build && \
    aws s3 sync dist/ s3://ciso-copilot-app-470226123496/ --delete && \
    aws cloudfront create-invalidation --distribution-id E2FV1Z0DJ4RQS4 --paths '/*'
  ```

### 7. Update iOS clientId + rebuild
- Edit `ios/CISOCopilot/Services/AuthManager.swift:19` → new `UserPoolClientId` (iOS).
- ```bash
  cd ios && xcodegen generate && \
    xcodebuild build -project CISOCopilot.xcodeproj -scheme CISOCopilot \
    -destination "id=00008140-001E104E3A9B001C" -derivedDataPath build-device \
    -allowProvisioningUpdates && \
    xcrun devicectl device install app --device 00008140-001E104E3A9B001C \
    build-device/Build/Products/Debug-iphoneos/CISOCopilot.app
  ```

### 8. Verify
- **Web**: incognito → `https://dil1ztnjosz43.cloudfront.net/` → Google sign-in → land at `/`.
- Sign out, sign in again (this was the failing case) → must succeed.
- **iOS**: open app → sign out (forced by 401) → email-first sign-in → land at Overview tab.
- **DB**: confirm `users` rows for both emails still present (they should be — we keyed on Google/MS sub, not Cognito).

### 9. Update HANDOFF.md
- New `Cognito User Pool`, `Cognito iOS client`, `Cognito Web client` IDs in the live-URLs table.
- Strike or mark gotcha #4 as fixed.
- Note in "What works": "web sign-in via Google verified after pool recreate (2026-05-18)."

## Rollback

If anything goes wrong before step 6:
- The old retained pool is gone (DESTROY) once CDK deploy succeeds, so
  there's no easy revert. Risk window is small (steps 3–4, ~5 min).
- If CDK deploy fails mid-way, the partial state should be cleaned by
  re-running `cdk deploy`. CFN rollback should restore the old auth stack
  with the new pool gone.

## Cleanup state after deploy

- Old pool: deleted by CDK DESTROY.
- Old domain: deleted in step 1.
- New pool has fresh Google + Microsoft IdPs.
- No per-tenant MS IdPs until next /auth/discover-tenant call from a MS user.
