# `CisoCopilotAi` stack extraction — clear CFN headroom for Slice 1.4 onwards

> Brainstormed 2026-06-10. The `CisoCopilotApi` stack has been operating at
> the edge of the 500-resource CloudFormation cap for weeks. AI Security
> Sub-slice 1.4 (Google Workspace shadow-AI scanner) adds ~12-16 resources
> and will push it past the cap. This spec extracts new AI-domain work into
> a new `CisoCopilotAi` stack that shares the existing API Gateway via
> `RestApi.fromRestApiAttributes`. **"New work only" scope** — the four
> existing AI Lambdas stay in `CisoCopilotApi`.
>
> Cross-refs:
> - [`HANDOFF.md` "Architectural blocker"](../../../HANDOFF.md) — describes the cap incident and identifies stack extraction as the scaling fix
> - [`BACKLOG.md` "AI Security Slice 1"](../../../BACKLOG.md) — names this as a blocker before Sub-slice 1.4 can start
> - [`2026-06-04-ai-security-slice-1-design.md`](2026-06-04-ai-security-slice-1-design.md) — Sub-slice 1.4 is the immediate consumer of the headroom this spec unlocks

## 0. Codebase baseline — verified 2026-06-10

Everything below was confirmed by `grep`/`Read`/`wc` against the working tree on `feat/ai-security-slice-1` @ `1cab7f5`, not memory.

- **CDK app entry** — `platform/bin/platform.ts` (80 lines). Composes 8 stacks today, in this dependency order: `CisoCopilotNetwork` → `CisoCopilotData` → `CisoCopilotAuth` / `CisoCopilotEcr` / `CisoCopilotStatic` / `CisoCopilotEvents` → `CisoCopilotScan` → `CisoCopilotApi`. `CisoCopilotApi` is the last stack and the one with route + Lambda concentration.
- **`api-stack.ts`** — 1347 lines. Defines **34 Lambdas** via `new lambda.Function` / `new lambda.DockerImageFunction`, all on a single `apigw.RestApi` (constructed at line 526). Existing AI-domain Lambdas (4):
  - `AiSummaryFn` (line 349) — `GET /v1/ai/summary`
  - `AiBomExportFn` (line 362) — `GET /v1/ai/bom?format=cyclonedx` (shipped 2026-06-08 in Sub-slice 1.2)
  - `AiGithubFn` (line 462) — `POST/GET/DELETE /v1/ai/connections/github*`
  - `EntitiesApiFn` (line 509) — `GET /v1/entities/*` (AI graph)
  Existing AI routes are registered against `api.root.addResource('ai')` (line 931) and on `api.root` directly.
- **Shared Lambda helper** — `lambdaCodeWithSharedMeta(lambdaDir)` at `platform/lib/api-stack.ts:22-65` (shipped in Sub-slice 1.1). Uses `lambda.Code.fromAsset` with a `local.tryBundle` that `fs.cpSync`'s `scanner_core/framework_meta.py` into the Lambda asset at synth time. Reusable for any Lambda that needs to share `scanner_core/` content without a Layer. Both `AiSummaryFn` and `AiBomExportFn` use it.
- **The CFN 500-resource cap incident** — Sub-slice 1.2 deploy synthesized **666 resources** for `CisoCopilotApi`; CloudFormation reported **506 actual** (>500 hard cap). Per `HANDOFF.md` "Cap relief landed in Sub-slice 1.2", three verified-dead routes were deleted to free ~6 resources: `GET /v1/entities/{id}/graph`, `GET /v1/entities/{id}/relationships`, `DELETE /v1/ai/connections/{id}`. Web + iOS grep confirmed zero callers. **Current actual: ~494/500.** The underlying Lambda handlers in `entities_api` + `ai_github` still implement those operations; re-adding the routes is one line each if a caller appears.
- **Cognito authorizer** — `apigw.CognitoUserPoolsAuthorizer` constructed in `api-stack.ts` and attached via `authedOpts` to every authed `addMethod` call. Reused on the `RestApi`'s root. Authorizer ID + name will need to be exported for the new stack to attach the same authorizer to its routes.
- **CORS** — configured on the single `apigw.RestApi` (`api-stack.ts:526` area). Same configuration applies to all routes regardless of which stack registers them, because the API Gateway is shared.
- **Stack output exports today** — `platform/lib/api-stack.ts` does NOT export `restApiId` / `rootResourceId` / `cognitoAuthorizerId` as named `CfnOutput`s. New exports required.
- **`ScanStack`** — `platform/lib/scan-stack.ts` (538 lines). Defines four Fargate scan task-defs (AWS / Azure / Entra / GCP), `aiScanQueue`, `aiScannerFn`, and the shared scan cluster. **This is where the Workspace scanner Fargate container will land** — matching the existing AWS / Azure / Entra / GCP pattern. The Workspace scanner is NOT in scope for this spec; only the OAuth routes + the API-side Lambdas that drive ConnectClouds are.
- **`DataStack`** — `platform/lib/data-stack.ts` (131 lines). Defines the Aurora cluster, KMS key, DDB tables. `grantDataApiAccess(fn)` is the canonical way to give a Lambda Aurora Data API + Secrets Manager + KMS-decrypt-of-secret in one call. Used 8 times in `api-stack.ts` today.
- **Onboarding Lambdas in `api-stack.ts`** — 8 Lambdas matching the cloud-OAuth pattern: `OnboardingAwsInitiateFn` (178), `OnboardingAwsCompleteFn` (198), `OnboardingAzureInitiateFn` (689), `OnboardingAzureCompleteFn` (703), `OnboardingEntraInitiateFn` (753), `OnboardingEntraCallbackFn` (767), `OnboardingGcpInitiateFn` (800), `OnboardingGcpCompleteFn` (814). All today live in `CisoCopilotApi`. Workspace OAuth (1.4) is the same shape; in this spec it lands in `CisoCopilotAi` per the "new work only" decision (§3).
- **`fromRestApiAttributes` viability** — confirmed via AWS CDK API ref: `apigw.RestApi.fromRestApiAttributes({restApiId, rootResourceId})` returns an `IRestApi`. `addResource`/`addMethod` are supported on the imported root. `LambdaIntegration` generates `AWS::Lambda::Permission` resources that land in the **Lambda's owning stack** (CFN-correct). The known limitation is documented: routes added from a different stack do NOT trigger the owning stack's `AWS::ApiGateway::Deployment` to update, so the stage continues serving the old set of routes unless explicitly re-pointed.
- **`AwsCustomResource`** — `aws-cdk-lib/custom-resources` provides `AwsCustomResource` for invoking SDK calls during stack lifecycle. Used in the codebase today: zero direct uses (`grep -rn AwsCustomResource platform/lib/ platform/bin/` → empty). New pattern for this codebase; well-trodden CDK pattern overall.

**What's genuinely new in this spec:**

1. New stack `CisoCopilotAi` in `platform/lib/ai-stack.ts`, wired in `platform/bin/platform.ts`.
2. Three new named `CfnOutput`s in `api-stack.ts` (`RestApiId`, `RestApiRootResourceId`, `CognitoAuthorizerId`).
3. One-line `addToLogicalId({ aiStackExtensionVersion: 'v1' })` pin on `CisoCopilotApi`'s `Deployment` to defend against the stage-re-pointing failure mode (§5.d).
4. `CisoCopilotAi` registers `/v1/ai/workspace/*` routes (and any future new AI routes) on the imported API Gateway via `fromRestApiAttributes`.
5. An `AwsCustomResource` in `CisoCopilotAi` that calls `apigateway:UpdateStage` on every deploy where the `Deployment` logicalId changes, re-pointing the existing `v1` stage at the new deployment.
6. Documentation: HANDOFF.md gotcha entry + ARCHITECTURE.md ADR for "cross-stack RestApi extension."

**Out of scope (verified by §0):** migrating any of the 4 existing AI Lambdas (`AiSummaryFn`, `AiBomExportFn`, `AiGithubFn`, `EntitiesApiFn`), migrating any onboarding Lambda, the Workspace Fargate scanner itself, a shared `lib/api-helpers.ts` abstraction.

## 1. Goal and success criteria

**Goal:** unblock Sub-slice 1.4 by giving new AI-domain Lambdas a CFN-resource budget that does not compete with `CisoCopilotApi`'s. Do this once, in a way that scales: subsequent AI work (Sub-slice 1.5, Bedrock detector extensions, future AI connector OAuth) lands in `CisoCopilotAi` by default.

**Success criteria (testable):**

1. `cdk synth CisoCopilotAi` succeeds and produces a stack with `< 50` resources at first deploy (one stub Workspace route is enough to prove the wiring).
2. `cdk synth CisoCopilotApi` resource count is unchanged within ±3 (the only delta should be the three new `CfnOutput`s and the one-line `addToLogicalId` pin).
3. After deploying both stacks, `curl -i https://api.shasta.io/v1/ai/workspace/initiate` returns `401` (Cognito-gated, not 404). Returns `404` if and only if the stage didn't re-point at the new deployment — fail.
4. `curl -i -H "Authorization: Bearer $TOKEN" https://api.shasta.io/v1/ai/summary` still returns `200` after both deploys — existing routes unaffected.
5. `aws apigateway get-stage --rest-api-id <id> --stage-name v1 --query 'deploymentId'` returns the `deploymentId` produced by `CisoCopilotAi`'s `Deployment` resource (verifies the `AwsCustomResource` fired).
6. Subsequent `cdk deploy CisoCopilotApi` (with no AI-route changes) does NOT clobber the new `/v1/ai/workspace/*` routes — the `aiStackExtensionVersion: 'v1'` pin holds the deployment logicalId stable across un-related route changes.
7. Subsequent `cdk deploy CisoCopilotAi --hotswap` works for Lambda code-only changes (no route changes). Full `cdk deploy CisoCopilotAi` is required when route definitions change (the `AwsCustomResource` won't fire on hotswap; document loudly).
8. Rollback path: `cdk deploy CisoCopilotAi` failure does not affect `CisoCopilotApi` traffic. One-way dependency holds (verified via dependency graph in `cdk.out`).

## 2. Why this design (and what was reconsidered)

The CFN 500-resource cap is a hard CloudFormation limit, not a soft suggestion. We hit 494/500 today; Sub-slice 1.4 alone is ~12-16 new resources. Continued route-deletion to free space is unsustainable — every new feature creates the same pressure. The structural fix is to split the stack.

**Reconsidered — boundary for what migrates:**

- *Rejected: pure AI-domain (move all 4 existing AI Lambdas now).* Would free ~20-30 resources from `CisoCopilotApi` immediately, but bigger blast radius for a deploy that's primarily about Slice 1.4 unblocking. KK explicitly chose to limit scope to new work.
- *Rejected: connector-OAuth domain (move all 8 onboarding Lambdas + new Workspace).* Cleaner conceptual boundary (Workspace IS an onboarding flow, not an AI feature), but a much larger refactor for what is currently a tactical headroom problem. Deferred unless we hit cap again on a non-AI route.
- *Chosen: "new work only."* CisoCopilotAi starts empty; Slice 1.4 onwards lands there. Smallest blast radius. Validates the cross-stack pattern with low-stakes work. Older AI Lambdas can be opportunistically migrated later if and only if `CisoCopilotApi` hits cap again. Accept the semantic inconsistency that Workspace OAuth lives in a different stack than AWS/Azure/Entra/GCP OAuth — frame Workspace as "AI scanner connector" rather than "cloud onboarding."

**Reconsidered — cross-stack API mechanism:**

- *Rejected: separate `RestApi` at `ai-api.shasta.io`.* Eliminates cross-stack coupling but requires web + iOS to know which host to call per route. Split CORS, split rate-limit, split Cognito wiring. More client-side moving parts than we want.
- *Rejected: CloudFront path-routing to a second `RestApi`.* Keeps the `api.shasta.io` host unified, but introduces a CloudFront-as-router failure mode, doubles the API Gateway monitoring surface, and complicates the auth path (Cognito JWT validation against either of two backends).
- *Chosen: shared `RestApi` via `fromRestApiAttributes`.* One API Gateway, one `/v1` stage, one Cognito authorizer, one CORS config. The stage-re-pointing gotcha is real but well-bounded and fixable with an `AwsCustomResource` (§5.c).

**Reconsidered — stage re-pointing technique:**

- *Rejected: manual `aws apigateway create-deployment` step in deploy.sh.* Simplest to write but creates a "don't forget" foot-gun. Every contributor would need to remember.
- *Rejected: forcing a `cdk deploy CisoCopilotApi` after every `CisoCopilotAi` change.* Defeats the point of the split (we'd still have to redeploy the big stack to take effect).
- *Chosen: `AwsCustomResource` in `CisoCopilotAi` that calls `apigateway:UpdateStage` on every deploy where the `Deployment`'s logicalId changes.* Automatic, idempotent, runs only when route definitions actually changed. Adds one Lambda-backed CR resource to `CisoCopilotAi`; trivial cost.

## 3. Scope

**In scope:**

- Create `CisoCopilotAi` stack in `platform/lib/ai-stack.ts`.
- Wire it into `platform/bin/platform.ts` with dependencies on `CisoCopilotApi`, `CisoCopilotData`, `CisoCopilotAuth`, `CisoCopilotNetwork`.
- Three named `CfnOutput`s on `CisoCopilotApi` (RestApi ID, root resource ID, Cognito authorizer ID).
- One-line `addToLogicalId({ aiStackExtensionVersion: 'v1' })` on `CisoCopilotApi`'s `Deployment`.
- `AwsCustomResource` in `CisoCopilotAi` that re-points the `v1` stage at the new Deployment when AI routes change.
- One stub Lambda + route in `CisoCopilotAi` to prove the wiring end-to-end (a `GET /v1/ai/_health` Lambda returning `{ok: true}`; deleted once Sub-slice 1.4's first real route lands).
- HANDOFF.md gotcha block documenting the deploy-order + hotswap-limitations rules.
- ARCHITECTURE.md ADR for "cross-stack RestApi extension via `fromRestApiAttributes` + Custom-Resource stage re-point."

**Out of scope (YAGNI):**

- Migrating `AiSummaryFn`, `AiBomExportFn`, `AiGithubFn`, `EntitiesApiFn` — explicit "new work only" decision (§2).
- Migrating any of the 8 onboarding Lambdas.
- Sub-slice 1.4's Workspace OAuth Lambdas themselves — those land in `CisoCopilotAi` once it exists, but their detector logic + Aurora schema + Fargate task-def are 1.4's spec, not this one.
- The Workspace Fargate scanner — lives in `ScanStack` per existing AWS/Azure/Entra/GCP pattern.
- A reusable `lib/api-helpers.ts` abstraction for cross-stack route registration. Build the second-time-we-need-it abstraction, not the first.
- Backfilling `cdk synth`-snapshot tests for the resource-count delta (no existing snapshot infrastructure to extend; would be a new sub-project).

## 4. Components & architecture

```
CisoCopilotNetwork ─┐
CisoCopilotData ────┼──> CisoCopilotApi ──[exports: restApiId,
CisoCopilotAuth ────┘                       rootResourceId,
                                            cognitoAuthorizerId]
                                                  │
                                                  ▼
                                            CisoCopilotAi
                                            ├─ imports RestApi via fromRestApiAttributes
                                            ├─ Lambdas + routes (Sub-slice 1.4 onwards)
                                            ├─ Deployment (logicalId hashes over AI routes)
                                            └─ AwsCustomResource → apigateway:UpdateStage
```

**Key invariants:**

- One-way dependency: `CisoCopilotAi` depends on `CisoCopilotApi`. Never the reverse.
- One `AWS::ApiGateway::RestApi`, one `AWS::ApiGateway::Stage`, one Cognito authorizer — all in `CisoCopilotApi`. `CisoCopilotAi` only contributes new `AWS::ApiGateway::Resource` + `AWS::ApiGateway::Method` resources, a new `AWS::ApiGateway::Deployment`, and the Custom Resource.
- `AWS::Lambda::Permission` resources (the `apigateway:Invoke` grants) live in `CisoCopilotAi` alongside the Lambdas they protect — CDK auto-generates these when `LambdaIntegration(fn)` is used; CFN ownership is correct.

## 5. Cross-stack mechanics

### 5.a Exporting from `CisoCopilotApi`

Add three named exports near the top of `api-stack.ts`'s `RestApi` block:

```typescript
new cdk.CfnOutput(this, 'RestApiId', {
  value: api.restApiId,
  exportName: 'CisoCopilotApi-RestApiId',
});
new cdk.CfnOutput(this, 'RestApiRootResourceId', {
  value: api.restApiRootResourceId,
  exportName: 'CisoCopilotApi-RootResourceId',
});
new cdk.CfnOutput(this, 'CognitoAuthorizerId', {
  value: cognitoAuthorizer.authorizerId,
  exportName: 'CisoCopilotApi-CognitoAuthorizerId',
});
```

Named exports (with `exportName`) — not synth-time JS refs — because they cross stacks and need CFN-level naming.

### 5.b Importing in `CisoCopilotAi`

```typescript
const api = apigw.RestApi.fromRestApiAttributes(this, 'ImportedApi', {
  restApiId:      cdk.Fn.importValue('CisoCopilotApi-RestApiId'),
  rootResourceId: cdk.Fn.importValue('CisoCopilotApi-RootResourceId'),
});

const authorizer = apigw.CognitoUserPoolsAuthorizer.fromCognitoUserPoolsAuthorizerAttributes(
  this, 'ImportedAuthz',
  {
    authorizerId:   cdk.Fn.importValue('CisoCopilotApi-CognitoAuthorizerId'),
    authorizerType: 'COGNITO_USER_POOLS',
  },
);

const authedOpts: apigw.MethodOptions = {
  authorizationType: apigw.AuthorizationType.COGNITO,
  authorizer,
};

const aiRes = api.root.addResource('ai');
aiRes.addResource('workspace').addResource('initiate')
     .addMethod('POST', new apigw.LambdaIntegration(workspaceInitiateFn), authedOpts);
```

### 5.c Stage re-pointing via `AwsCustomResource`

```typescript
const deployment = new apigw.Deployment(this, 'AiRoutesDeployment', { api });
// Force the logicalId to re-hash when any AI Lambda's ARN changes (i.e., when a route is added or modified)
deployment.addToLogicalId({
  workspaceInitiate: workspaceInitiateFn.functionArn,
  workspaceCallback: workspaceCallbackFn.functionArn,
  // ... extend per new Lambda
});

new cr.AwsCustomResource(this, 'PointStageAtNewDeployment', {
  onUpdate: {
    service: 'APIGateway',
    action:  'updateStage',
    parameters: {
      restApiId: cdk.Fn.importValue('CisoCopilotApi-RestApiId'),
      stageName: 'v1',
      patchOperations: [{ op: 'replace', path: '/deploymentId', value: deployment.deploymentId }],
    },
    physicalResourceId: cr.PhysicalResourceId.of(deployment.deploymentId),
  },
  policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
    resources: cr.AwsCustomResourcePolicy.ANY_RESOURCE,
  }),
});
```

The `physicalResourceId` keyed on `deployment.deploymentId` means the CR fires exactly when the deployment changes. Idempotent: re-running the same `cdk deploy` does not call `updateStage` again.

### 5.d The `CisoCopilotApi` re-deploy hazard

When `CisoCopilotApi` itself is later deployed (e.g., adding a non-AI route), its own `Deployment` resource re-hashes its logicalId from the methods it owns. The re-hashed deployment will not include the methods registered by `CisoCopilotAi`. If the stage is auto-pointed at that new deployment, the AI routes vanish from served traffic.

**Mitigation:** pin `CisoCopilotApi`'s `Deployment` logicalId with an explicit version key. In `api-stack.ts`, after the `RestApi` and its default deployment are constructed:

```typescript
// Defend against CisoCopilotAi's routes being dropped from the served stage
// when CisoCopilotApi is deployed independently. Bump 'aiStackExtensionVersion'
// only when a major route-layout change is intentional.
api.latestDeployment?.addToLogicalId({ aiStackExtensionVersion: 'v1' });
```

This keeps the logicalId stable across non-AI route additions in `CisoCopilotApi`. The `CisoCopilotAi`'s `AwsCustomResource` remains the authority on which deployment the stage serves. To intentionally drop the AI extension (e.g., reorganize routes), bump `'v1'` → `'v2'` and re-deploy both stacks in sequence.

### 5.e Deploy order

```bash
# First deploy of CisoCopilotAi (or any change to either stack's exports):
npx cdk deploy CisoCopilotApi CisoCopilotAi --require-approval never

# Subsequent AI-only changes (Lambda code/env only, no route changes):
npx cdk deploy CisoCopilotAi --require-approval never --hotswap

# AI route definitions changing → full deploy required (hotswap skips Custom Resources):
npx cdk deploy CisoCopilotAi --require-approval never
```

## 6. Verification

### 6.a Pre-deploy

1. `cdk synth CisoCopilotApi CisoCopilotAi` — both stacks synth clean. No circular dep.
2. `cdk diff CisoCopilotApi` — diff shows ONLY: 3 new `CfnOutput` resources + 1 `Deployment` logicalId change. Anything else means we touched the wrong code.
3. `cdk diff CisoCopilotAi` — shows the new Lambdas + methods + `Deployment` + `AwsCustomResource`. Specifically, NO `AWS::ApiGateway::RestApi`, NO `AWS::ApiGateway::Stage`, NO `AWS::Cognito::UserPool`.
4. Resource counts: `cdk synth CisoCopilotApi | grep -c "    Type: "` should be within ±3 of pre-change. `cdk synth CisoCopilotAi | grep -c "    Type: "` should be < 50 at first deploy.

### 6.b Post-deploy

```bash
# 1. Stage now points at CisoCopilotAi's deployment
STAGE_DEP_ID=$(aws apigateway get-stage --rest-api-id <ID> --stage-name v1 \
  --query 'deploymentId' --output text)
AI_DEP_ID=$(aws cloudformation describe-stacks --stack-name CisoCopilotAi \
  --query 'Stacks[0].Outputs[?OutputKey==`AiDeploymentId`].OutputValue' --output text)
[ "$STAGE_DEP_ID" = "$AI_DEP_ID" ] && echo OK || echo FAIL

# 2. New route is reachable (returns 401 from Cognito gate, not 404)
curl -i -o /dev/null -w "%{http_code}\n" https://api.shasta.io/v1/ai/_health
# Expect: 401. 404 = stage didn't re-point. 403 = authorizer not wired.

# 3. Existing AI route still works
curl -i -H "Authorization: Bearer $TOKEN" https://api.shasta.io/v1/ai/summary | head -1
# Expect: HTTP/2 200

# 4. Tail the Custom Resource Lambda for the UpdateStage call
aws logs tail /aws/lambda/<cr-fn-name> --since 10m | grep -i 'updateStage'
```

### 6.c Regression smoke

After the stub `/v1/ai/_health` route works:

1. Deploy a no-op change to `CisoCopilotApi` (e.g., update an unrelated Lambda env var). Verify `/v1/ai/_health` still returns 401, not 404. **This is the §5.d hazard test.**
2. Verify all existing routes (random sample of ~5 from `findings`, `events`, `risks`, `policies`, `me`) still return their expected codes.

## 7. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `CisoCopilotApi` later deploy drops AI routes (§5.d hazard) | Medium without the pin; near-zero with the `aiStackExtensionVersion: 'v1'` pin | Pin in §5.d; document loudly in HANDOFF.md; regression smoke (§6.c step 1) on first prod deploy |
| `AwsCustomResource` `UpdateStage` API call rate-limited or fails | Low | CFN auto-rollback on stack-level failure; manual `aws apigateway update-stage` is the unblock; CR Lambda logs to CloudWatch for diagnosis |
| Hotswap silently skips the Custom Resource on route changes | Certain (CDK behavior) | Document: "any deploy that adds/removes/renames a route in `CisoCopilotAi` requires full `cdk deploy`, not hotswap" — in HANDOFF.md and as a comment near the CR construct |
| Lambda permission cross-stack mis-ownership at runtime | Low | `LambdaIntegration` puts the `AWS::Lambda::Permission` in the Lambda's owning stack (CFN-correct). Verified via `cdk synth CisoCopilotAi | grep -A 3 'AWS::Lambda::Permission'`. |
| Future contributor adds a `/v1/ai/*` route to `CisoCopilotApi` by accident | Medium over time | ARCHITECTURE.md ADR documents the convention. A grep-based CI lint that fails if `api.root.addResource('ai')` appears in `api-stack.ts` would harden this — out of scope for v1, fold into spec follow-ups if it ever bites. |

## 8. Open questions

- **Stage name.** Today `api.shasta.io` serves a stage named `v1` (or potentially `prod` — verify before first deploy). The `patchOperations` `stageName` must match exactly. **TODO at implementation time**: read the actual stage name with `aws apigateway get-stages --rest-api-id <id>` and pin it in the CR construct.
- **Cognito authorizer name.** Confirm whether the existing authorizer is exposed as a JS-side const (most likely `cognitoAuthorizer`) or constructed inline. If inline, refactor to a named const first so we can `.authorizerId` it cleanly for the export.

Neither blocks design approval; both are 10-min implementation-time checks.

## 9. References

- [HANDOFF.md "Architectural blocker — DO BEFORE Sub-slice 1.4"](../../../HANDOFF.md) — names this work as a Slice 1.4 blocker
- [BACKLOG.md "AI Security Slice 1 — top-of-mind"](../../../BACKLOG.md) — flags it as `[BLOCKING — DO FIRST]`
- [`2026-06-04-ai-security-slice-1-design.md`](2026-06-04-ai-security-slice-1-design.md) — Sub-slice 1.4 spec; the consumer of the headroom this spec unlocks
- [AWS CDK API ref — `RestApi.fromRestApiAttributes`](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_apigateway.RestApi.html#static-fromwbrrestwbrapiwbrattributesscope-id-attrs) — the cross-stack import primitive
- [AWS CDK API ref — `AwsCustomResource`](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.custom_resources.AwsCustomResource.html) — the stage re-pointing mechanism
- [AWS CloudFormation limits — 500 resources per stack](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cloudformation-limits.html) — the hard cap this spec works around
