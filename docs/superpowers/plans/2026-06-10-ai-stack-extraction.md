# `CisoCopilotAi` Stack Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a new `CisoCopilotAi` CDK stack that shares the existing `CisoCopilotApi` RestApi via `fromRestApiAttributes`, with an `AwsCustomResource` re-pointing the `v1` stage when AI routes change. Prove the wiring end-to-end with a `GET /v1/ai/_health` stub Lambda so Sub-slice 1.4 can drop its Workspace OAuth Lambdas into the new stack without hitting the 500-resource CFN cap.

**Architecture:** Pure mechanics — no business logic in this plan. One-way dependency: `CisoCopilotAi → CisoCopilotApi`. `CisoCopilotApi` exports `restApiId` / `rootResourceId` / `cognitoAuthorizerId` as named CFN exports; `CisoCopilotAi` imports them, registers `/v1/ai/_health` (and future AI routes) on the imported root, owns its own `Deployment` whose `logicalId` hashes over AI Lambda ARNs, and runs an `AwsCustomResource` on every changed deploy to call `apigateway:UpdateStage` on the existing `v1` stage. A one-line `addToLogicalId({ aiStackExtensionVersion: 'v1' })` pin on `CisoCopilotApi`'s `latestDeployment` defends against unrelated `CisoCopilotApi` redeploys silently dropping AI routes.

**Tech Stack:** AWS CDK (TypeScript) — `aws-cdk-lib`. AWS CloudFormation custom resources via `aws-cdk-lib/custom-resources`. Python 3.12 for the stub Lambda. Aurora / KMS / Cognito / EventBridge already in place.

**Spec:** `docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md`. Re-read §5 (cross-stack mechanics) before starting; this plan executes that section verbatim.

**Resolved §8 open questions (verified 2026-06-10):**
- Stage name: `'v1'` (confirmed at `platform/lib/api-stack.ts:529`)
- Cognito authorizer const name: `cognitoAuth` (confirmed at `platform/lib/api-stack.ts:553`, construct ID `'CognitoAuthorizer'`)

---

## File Structure

**New files:**
- `platform/lib/ai-stack.ts` — new `CisoCopilotAi` stack class
- `platform/lambda/ai_health/main.py` — stub Lambda handler (`{ok: true, stack: "CisoCopilotAi"}`)
- `platform/lambda/ai_health/tests/test_main.py` — pytest unit test

**Modified files:**
- `platform/lib/api-stack.ts` — three new `CfnOutput`s + one-line `latestDeployment` pin
- `platform/bin/platform.ts` — instantiate `CisoCopilotAi`, declare deps on the four upstream stacks
- `HANDOFF.md` — new "Cross-stack RestApi extension" section with deploy-order + hotswap-limitation gotchas
- `ARCHITECTURE.md` — new ADR for the cross-stack pattern

---

## Task 1: Add three CfnOutputs to CisoCopilotApi

**Files:**
- Modify: `platform/lib/api-stack.ts:526-559` (insert after the `cognitoAuth` block ends at line 559)

Named CFN exports are how `CisoCopilotAi` will import these three handles. JS-side refs would only work if both stacks lived in one app instance — they do today, but named exports make the contract explicit at the CFN layer and survive any future split.

- [ ] **Step 1: Add the three CfnOutputs**

Insert immediately after `cognitoAuth` is constructed (i.e., after line 559, before `meRes` at line 571):

```typescript
    // Cross-stack exports for CisoCopilotAi (see docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md §5.a)
    new cdk.CfnOutput(this, 'RestApiIdExport', {
      value:      api.restApiId,
      exportName: 'CisoCopilotApi-RestApiId',
      description: 'Consumed by CisoCopilotAi via Fn.importValue',
    });
    new cdk.CfnOutput(this, 'RestApiRootResourceIdExport', {
      value:      api.restApiRootResourceId,
      exportName: 'CisoCopilotApi-RootResourceId',
      description: 'Consumed by CisoCopilotAi via Fn.importValue',
    });
    new cdk.CfnOutput(this, 'CognitoAuthorizerIdExport', {
      value:      cognitoAuth.authorizerId,
      exportName: 'CisoCopilotApi-CognitoAuthorizerId',
      description: 'Consumed by CisoCopilotAi via Fn.importValue',
    });
```

- [ ] **Step 2: Run cdk synth and confirm the exports land in the template**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk synth CisoCopilotApi 2>/dev/null | grep -A 1 'CisoCopilotApi-RestApiId\|CisoCopilotApi-RootResourceId\|CisoCopilotApi-CognitoAuthorizerId'
```

Expected: three matching `Name:` lines, each followed by a `Value:` line referencing the corresponding resource. Failure mode: missing exports = typo in the code; circular dep error = wrong file (you're editing something other than api-stack.ts).

- [ ] **Step 3: Confirm resource count didn't move materially**

```bash
npx cdk synth CisoCopilotApi 2>/dev/null | grep -c '^    Type: '
```

Expected: previous count + ≤ 3 (the CfnOutputs are `Output` entries, not `Resource` entries — count should actually be unchanged). Record the number; you'll re-check it in Task 2.

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lib/api-stack.ts
git commit -m "$(cat <<'EOF'
feat(cdk): export RestApi handles from CisoCopilotApi for cross-stack import

Three named CFN exports (RestApiId, RootResourceId, CognitoAuthorizerId)
consumed by the upcoming CisoCopilotAi stack via Fn.importValue. See
docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md §5.a.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Pin CisoCopilotApi's Deployment logicalId

**Files:**
- Modify: `platform/lib/api-stack.ts` — one line near the `api` construct

This is the §5.d hazard mitigation. When `CisoCopilotApi` is later deployed for some unrelated reason (a new `/v1/foo` route, say), its `latestDeployment` re-hashes its logicalId. Without this pin, the re-hashed deployment doesn't include `CisoCopilotAi`'s methods → the stage would drop our AI routes. The version-key pin keeps `CisoCopilotApi`'s deployment logicalId stable across non-AI route changes, leaving `CisoCopilotAi`'s `AwsCustomResource` as the authority on which deployment the stage serves.

- [ ] **Step 1: Add the pin immediately after the `api` construct**

Insert after the `new apigw.RestApi(...)` block ends (line 537) and before the `corsHeaders` block at line 544:

```typescript
    // Pin the deployment logicalId so CisoCopilotApi-only redeploys don't
    // clobber routes added by CisoCopilotAi. Bump 'v1' → 'v2' only on
    // intentional major route-layout changes. See
    // docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md §5.d
    api.latestDeployment?.addToLogicalId({ aiStackExtensionVersion: 'v1' });
```

- [ ] **Step 2: cdk synth and confirm template still synthesizes cleanly**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk synth CisoCopilotApi 2>&1 | tail -5
```

Expected: no errors. The Deployment resource's `LogicalID` changes (you can see it in `cdk.out/CisoCopilotApi.template.json`); resource count is unchanged.

- [ ] **Step 3: Confirm via cdk diff against a fresh synth (sanity check)**

```bash
npx cdk diff CisoCopilotApi 2>&1 | head -30
```

Expected: the diff shows the Deployment resource has a new logicalId (CFN reads as a replacement of one Deployment with another — that's correct; the old stage will be re-pointed at the new deployment, but stage continuity is preserved by CFN's update semantics for Stage→Deployment refs).

- [ ] **Step 4: Commit**

```bash
git add platform/lib/api-stack.ts
git commit -m "$(cat <<'EOF'
feat(cdk): pin CisoCopilotApi deployment logicalId for cross-stack stability

One-line pin defending against CisoCopilotApi-only redeploys silently
dropping routes registered by CisoCopilotAi. See spec §5.d.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Create the stub AI health Lambda

**Files:**
- Create: `platform/lambda/ai_health/main.py`
- Create: `platform/lambda/ai_health/tests/test_main.py`
- Create: `platform/lambda/ai_health/tests/__init__.py` (empty)

A trivial Lambda whose only job is to prove that a route registered from `CisoCopilotAi` actually reaches a Lambda owned by `CisoCopilotAi`. Replaced by Sub-slice 1.4's first real Workspace OAuth route, but kept until then because it's the only thing that lets us run end-to-end verification without coupling this plan to 1.4.

- [ ] **Step 1: Write the failing test**

```python
# platform/lambda/ai_health/tests/test_main.py
import json

from ..main import handler


def test_handler_returns_ok():
    response = handler({}, None)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body == {"ok": True, "stack": "CisoCopilotAi"}


def test_handler_returns_cors_headers():
    response = handler({}, None)
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    assert response["headers"]["Content-Type"] == "application/json"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda
python -m pytest ai_health/tests/test_main.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_health.main'` or `ImportError` — the module doesn't exist yet.

- [ ] **Step 3: Implement the minimal handler**

```python
# platform/lambda/ai_health/main.py
"""Stub /v1/ai/_health endpoint — proves CisoCopilotAi → API Gateway wiring.

Replace with the first real Sub-slice 1.4 route once the stack is verified
in prod. See docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md §3.
"""
import json


def handler(event, _context):
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"ok": True, "stack": "CisoCopilotAi"}),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform/lambda
python -m pytest ai_health/tests/test_main.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lambda/ai_health/
git commit -m "$(cat <<'EOF'
feat(ai_health): stub Lambda for CisoCopilotAi wiring verification

Returns {ok: true, stack: "CisoCopilotAi"} from GET /v1/ai/_health. Lives
only until Sub-slice 1.4's first real Workspace OAuth route replaces it
on the same stack.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Create the CisoCopilotAi stack skeleton

**Files:**
- Create: `platform/lib/ai-stack.ts`

Builds the stack class with no Lambdas or routes yet — just imports, props interface, and the `fromRestApiAttributes` import wiring. We add the Lambda + route in Task 5, then the Deployment + Custom Resource in Task 6.

- [ ] **Step 1: Create the file**

```typescript
// platform/lib/ai-stack.ts
import * as cdk from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cr from 'aws-cdk-lib/custom-resources';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * Houses NEW AI-domain Lambdas + their /v1/ai/* routes on the same
 * API Gateway as CisoCopilotApi, via cross-stack import. Created to
 * dodge the CloudFormation 500-resource cap on CisoCopilotApi. See
 * docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md.
 *
 * "New work only" — the four existing AI Lambdas (AiSummaryFn,
 * AiBomExportFn, AiGithubFn, EntitiesApiFn) stay in CisoCopilotApi.
 */
interface AiStackProps extends cdk.StackProps {
  vpc:       ec2.IVpc;            // for any future AI Lambda touching Aurora
  dbCluster: rds.IDatabaseCluster; // for grantDataApiAccess
  userPool:  cognito.IUserPool;   // referenced for parity; not used until 1.4
}

export class AiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AiStackProps) {
    super(scope, id, props);

    // ── Cross-stack import: existing RestApi + Cognito authorizer ──
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

    // /v1/ai resource — Lambdas + routes attached in subsequent tasks
    const aiRes = api.root.addResource('ai');
    // Suppress unused-variable warning in skeleton state; consumed in Task 5
    void aiRes;
    void authedOpts;
    void props;
  }
}
```

- [ ] **Step 2: Confirm TypeScript compiles**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx tsc --noEmit
```

Expected: no errors. If you see `Cannot find module 'aws-cdk-lib/custom-resources'`, that submodule import was added in CDK v2.x — confirm `package.json` has `aws-cdk-lib` ≥ 2.0 (it does; this is just a sanity check).

- [ ] **Step 3: Commit**

```bash
git add platform/lib/ai-stack.ts
git commit -m "$(cat <<'EOF'
feat(cdk): CisoCopilotAi stack skeleton with cross-stack RestApi import

Empty stack that imports the existing CisoCopilotApi RestApi + Cognito
authorizer via Fn.importValue. Lambdas + routes wired in subsequent
commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Mount the stub Lambda and /v1/ai/_health route in CisoCopilotAi

**Files:**
- Modify: `platform/lib/ai-stack.ts`

- [ ] **Step 1: Replace the `void` placeholders with the Lambda + route**

Replace the three `void` lines at the end of the constructor with:

```typescript
    // ── Stub Lambda: proves end-to-end wiring; deleted by Sub-slice 1.4's first real route ──
    const aiHealthFn = new lambda.Function(this, 'AiHealthFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ai_health')),
      timeout: cdk.Duration.seconds(5),
      description: 'Stub /v1/ai/_health — verifies CisoCopilotAi → API Gateway wiring',
    });

    // GET /v1/ai/_health (no auth — wiring test, not user-facing)
    aiRes.addResource('_health').addMethod(
      'GET', new apigw.LambdaIntegration(aiHealthFn),
    );

    // Mute unused-variable warnings for handles consumed in Task 6 / Sub-slice 1.4
    void authedOpts;
    void props;

    // ── Track Lambdas for the Deployment logicalId hash (Task 6) ──
    // (Constructed in Task 6; reference forward-declared here for clarity.)
```

- [ ] **Step 2: cdk synth and confirm both the resource + route + Lambda permission land**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk synth CisoCopilotAi 2>/dev/null > /tmp/ai-stack-synth.yaml
grep -E "(AiHealthFn|AWS::ApiGateway::Resource|AWS::ApiGateway::Method|AWS::Lambda::Permission)" /tmp/ai-stack-synth.yaml | head -20
```

Expected: at least one each of `AWS::Lambda::Function` (for AiHealthFn), `AWS::ApiGateway::Resource` (for `/ai`, `/ai/_health`), `AWS::ApiGateway::Method` (for GET), and `AWS::Lambda::Permission` (auto-generated by `LambdaIntegration`). The `Permission` resource must live in `CisoCopilotAi` (this template) — that's CFN-correct cross-stack pattern.

- [ ] **Step 3: Confirm CisoCopilotAi has < 50 resources at this point**

```bash
grep -c '^    Type: ' /tmp/ai-stack-synth.yaml
```

Expected: well under 50 (probably 6-10 at this stage; Custom Resource lands in Task 6).

- [ ] **Step 4: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lib/ai-stack.ts
git commit -m "$(cat <<'EOF'
feat(ai-stack): mount stub /v1/ai/_health Lambda + route

Proves CisoCopilotAi can register routes on the imported RestApi and that
the auto-generated AWS::Lambda::Permission lands in the correct stack.
Deployment + UpdateStage Custom Resource arrive in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add Deployment + AwsCustomResource for stage re-point

**Files:**
- Modify: `platform/lib/ai-stack.ts`

The load-bearing piece. The `Deployment` is owned by `CisoCopilotAi` and `addToLogicalId`'d over every AI Lambda's ARN — so any route addition forces a new physical Deployment. The `AwsCustomResource` calls `apigateway:UpdateStage` to point the existing `v1` stage (which lives in `CisoCopilotApi`) at the new deployment.

- [ ] **Step 1: Append the Deployment + Custom Resource to the constructor**

After the `aiRes.addResource('_health').addMethod(...)` line, add:

```typescript
    // ── Deployment: re-hashes logicalId whenever an AI Lambda ARN changes ──
    const aiRoutesDeployment = new apigw.Deployment(this, 'AiRoutesDeployment', { api });
    aiRoutesDeployment.addToLogicalId({
      aiHealth: aiHealthFn.functionArn,
      // Extend per new AI Lambda registered on this stack (Sub-slice 1.4+).
    });

    // Expose deployment id so Task 7 verification can compare it to the
    // currently-served stage deployment.
    new cdk.CfnOutput(this, 'AiDeploymentId', {
      value:       aiRoutesDeployment.deploymentId,
      description: 'CisoCopilotAi-managed deployment; the v1 stage is re-pointed here on every changed deploy',
    });

    // ── Custom Resource: apigateway:UpdateStage on every changed deploy ──
    new cr.AwsCustomResource(this, 'PointStageAtNewDeployment', {
      onUpdate: {
        service: 'APIGateway',
        action:  'updateStage',
        parameters: {
          restApiId: cdk.Fn.importValue('CisoCopilotApi-RestApiId'),
          stageName: 'v1',
          patchOperations: [
            { op: 'replace', path: '/deploymentId', value: aiRoutesDeployment.deploymentId },
          ],
        },
        // physicalResourceId keyed on deploymentId means the CR fires
        // exactly when the deployment is re-hashed; no-op otherwise.
        physicalResourceId: cr.PhysicalResourceId.of(aiRoutesDeployment.deploymentId),
      },
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: cr.AwsCustomResourcePolicy.ANY_RESOURCE,
      }),
    });
```

- [ ] **Step 2: Drop the `void authedOpts; void props;` placeholders**

`authedOpts` is consumed by Sub-slice 1.4; for now, mark it `_authedOpts` so linting is happy without dropping it. Same for props if needed.

Actually simpler — just remove the two `void` lines and let TypeScript complain only on truly unused names; both are reachable for Sub-slice 1.4. If `tsc --noEmit` errors on unused locals, prefix them with `_` (`const _authedOpts = …`) instead.

- [ ] **Step 3: cdk synth and confirm the four new resources land**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk synth CisoCopilotAi 2>/dev/null | grep -E "(AiRoutesDeployment|PointStageAtNewDeployment|AiDeploymentId)" | head -10
```

Expected: one `AWS::ApiGateway::Deployment` resource (AiRoutesDeployment) and one `Custom::AWS` resource (PointStageAtNewDeployment) plus its backing `AWS::Lambda::Function` + `AWS::IAM::Role` (the AwsCustomResource expands to 3 resources internally). Plus the `AiDeploymentId` CfnOutput.

- [ ] **Step 4: Confirm CisoCopilotAi resource count is still < 50**

```bash
npx cdk synth CisoCopilotAi 2>/dev/null | grep -c '^    Type: '
```

Expected: roughly 10-13.

- [ ] **Step 5: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/lib/ai-stack.ts
git commit -m "$(cat <<'EOF'
feat(ai-stack): Deployment + AwsCustomResource for cross-stack stage re-point

Deployment.addToLogicalId hashes over AI Lambda ARNs so route changes force
a new deployment. AwsCustomResource calls apigateway:UpdateStage on every
changed deploy to point the v1 stage (owned by CisoCopilotApi) at the new
deployment. See spec §5.c.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire CisoCopilotAi into the CDK app entry

**Files:**
- Modify: `platform/bin/platform.ts:80` (append after the existing `new ApiStack(...)` call)

- [ ] **Step 1: Add the import**

At the top of `platform/bin/platform.ts`, after the existing `ApiStack` import:

```typescript
import { AiStack } from '../lib/ai-stack';
```

- [ ] **Step 2: Instantiate CisoCopilotAi after the ApiStack block**

After the `new ApiStack(app, 'CisoCopilotApi', { ... });` block (ends at line 79), add:

```typescript

// AI-domain extension stack — shares the CisoCopilotApi RestApi via cross-stack
// import. New AI Lambdas (Sub-slice 1.4+) land here to dodge CisoCopilotApi's
// 500-resource CFN cap. See docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md.
const aiStack = new AiStack(app, 'CisoCopilotAi', {
  env,
  vpc:       network.vpc,
  dbCluster: data.cluster,
  userPool:  auth.userPool,
});
// CisoCopilotAi imports CFN exports from CisoCopilotApi; CDK auto-detects
// the dependency from Fn.importValue, but addDependency makes it explicit
// and protects against future export-name drift.
aiStack.addDependency(app.node.findChild('CisoCopilotApi') as cdk.Stack);
```

- [ ] **Step 3: cdk synth both stacks and confirm dependency order**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk synth CisoCopilotApi CisoCopilotAi 2>&1 | head -20
npx cdk list 2>&1
```

Expected `cdk list` output includes both `CisoCopilotApi` and `CisoCopilotAi`. No circular dep warnings in synth.

- [ ] **Step 4: cdk diff CisoCopilotApi — confirm minimal delta**

```bash
npx cdk diff CisoCopilotApi 2>&1 | head -40
```

Expected: changes are limited to (a) the three new `CfnOutput`s, (b) the Deployment logicalId changing from the `addToLogicalId` pin. No Lambda changes, no Method changes, no IAM changes.

- [ ] **Step 5: cdk diff CisoCopilotAi — confirm shape**

```bash
npx cdk diff CisoCopilotAi 2>&1 | head -60
```

Expected: all-new resources (CisoCopilotAi doesn't exist in prod yet). Specifically NO `AWS::ApiGateway::RestApi`, NO `AWS::ApiGateway::Stage`, NO `AWS::Cognito::UserPool` — only the new Lambda, Method, Resource, Deployment, and Custom Resource family.

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/bin/platform.ts
git commit -m "$(cat <<'EOF'
feat(cdk): wire CisoCopilotAi into the CDK app entry

Instantiates the new stack with vpc + dbCluster + userPool from upstream
stacks. Explicit addDependency on CisoCopilotApi to make the cross-stack
import contract obvious to readers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Deploy both stacks and verify end-to-end

**Files:** none (live deploy + verification)

Run all sub-steps before declaring this task complete. If any fail, do not proceed to Task 9 — diagnose, fix, redeploy.

- [ ] **Step 1: Source env and deploy both stacks**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
set -a && . platform/.env && set +a
cd platform
npx cdk deploy CisoCopilotApi CisoCopilotAi --require-approval never 2>&1 | tee /tmp/ai-stack-deploy.log
```

Expected: both stacks complete with `✅`. Look for `CisoCopilotAi.AiDeploymentId = <id>` in the output — record this `<id>`.

- [ ] **Step 2: Confirm the v1 stage now points at the new deployment**

```bash
REST_API_ID=$(aws cloudformation describe-stacks --stack-name CisoCopilotApi \
  --query 'Stacks[0].Outputs[?ExportName==`CisoCopilotApi-RestApiId`].OutputValue' \
  --output text)
STAGE_DEP_ID=$(aws apigateway get-stage --rest-api-id "$REST_API_ID" --stage-name v1 \
  --query 'deploymentId' --output text)
AI_DEP_ID=$(aws cloudformation describe-stacks --stack-name CisoCopilotAi \
  --query 'Stacks[0].Outputs[?OutputKey==`AiDeploymentId`].OutputValue' \
  --output text)
echo "Stage deployment:  $STAGE_DEP_ID"
echo "Ai stack deployment: $AI_DEP_ID"
[ "$STAGE_DEP_ID" = "$AI_DEP_ID" ] && echo "OK — stage re-pointed" || echo "FAIL — stage did NOT re-point"
```

Expected: the two ids match → `OK — stage re-pointed`. Failure means the AwsCustomResource didn't fire — see Step 5 for log triage.

- [ ] **Step 3: Confirm /v1/ai/_health is reachable**

```bash
curl -i -s -o /dev/null -w "%{http_code}\n" "https://${API_DOMAIN:-api.shasta.io}/v1/ai/_health"
```

Expected: `200` (we made it unauthenticated specifically so this test can run without a token). `404` = stage didn't re-point (revisit Step 2). `403` = something is gating it; if you went the auth'd route, that's expected and you need a token.

- [ ] **Step 4: Confirm an existing AI route still works**

```bash
TOKEN=$(cat ~/.shasta/secrets/dev-jwt.txt 2>/dev/null || echo "set me")
curl -i -s -H "Authorization: Bearer $TOKEN" \
  "https://${API_DOMAIN:-api.shasta.io}/v1/ai/summary" | head -1
```

Expected: `HTTP/2 200`. Anything else means the deployment re-point broke existing routes — `cdk deploy CisoCopilotApi` to restore (the pin should prevent this; if it failed, the spec's §5.d hazard is materializing and we need to revisit).

- [ ] **Step 5: Tail the AwsCustomResource Lambda logs**

```bash
CR_FN=$(aws lambda list-functions --query \
  "Functions[?contains(FunctionName, 'CisoCopilotAi-AWS') && contains(FunctionName, 'CustomResource')].FunctionName | [0]" \
  --output text)
aws logs tail "/aws/lambda/$CR_FN" --since 30m | grep -i 'updateStage\|error\|FAIL'
```

Expected: a successful `updateStage` log line, no errors. The Lambda name will include something like `CisoCopilotAi-AWS679f53fac002430cb0da5b7982bd2287` — that's the AWS-generated naming for the AwsCustomResource handler.

- [ ] **Step 6: Commit nothing (deploy task)**

This task creates no diffs. If you find a fix is needed, that's a new task — don't fold it in here.

---

## Task 9: §5.d regression test — CisoCopilotApi-only redeploy

**Files:** none (verification of the §5.d hazard mitigation)

The whole point of the `aiStackExtensionVersion: 'v1'` pin is to keep AI routes alive when `CisoCopilotApi` is deployed independently. Test it.

- [ ] **Step 1: Trigger a no-op CisoCopilotApi redeploy**

Touch any Lambda env var in `api-stack.ts` (e.g., add a `DEPLOY_TRIGGER=task9-test` env to `meFn` temporarily), then:

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk deploy CisoCopilotApi --require-approval never 2>&1 | tail -10
```

- [ ] **Step 2: Re-run the /v1/ai/_health curl from Task 8 Step 3**

```bash
curl -i -s -o /dev/null -w "%{http_code}\n" "https://${API_DOMAIN:-api.shasta.io}/v1/ai/_health"
```

Expected: still `200`. If it returns `404`, the pin is not holding — investigate immediately (likely the `addToLogicalId` line didn't actually persist; re-check `cdk.out/CisoCopilotApi.template.json` Deployment resource).

- [ ] **Step 3: Revert the no-op env var change and redeploy**

Remove the `DEPLOY_TRIGGER=task9-test` env you added in Step 1, then:

```bash
npx cdk deploy CisoCopilotApi --require-approval never 2>&1 | tail -5
```

- [ ] **Step 4: One final /v1/ai/_health curl to confirm clean state**

```bash
curl -i -s -o /dev/null -w "%{http_code}\n" "https://${API_DOMAIN:-api.shasta.io}/v1/ai/_health"
```

Expected: `200`. The hazard mitigation is now proven in prod.

- [ ] **Step 5: Commit nothing (verification task)**

If the regression test passed, no code change is needed. If it failed, that's a separate fix-and-retest cycle.

---

## Task 10: Document the gotchas

**Files:**
- Modify: `HANDOFF.md` (new section near the top, after the AI Security Slice 1 status block)
- Modify: `ARCHITECTURE.md` (new ADR appended to the ADR list)

- [ ] **Step 1: Add the HANDOFF.md operational block**

Insert immediately after the `## 🤖 AI Security Slice 1 — …` block (find it at the top of HANDOFF.md). Add this new section before the `## 🔔 MCP Connectors Slice 2 — …` block:

```markdown
## 🏗️ CisoCopilotAi stack — cross-stack RestApi extension (shipped 2026-06-10)

`CisoCopilotApi` was at 494/500 CFN resources. Sub-slice 1.4 would have
blown the cap. Fixed by extracting NEW AI-domain Lambdas into
`CisoCopilotAi`, which shares the existing `RestApi` + Cognito authorizer
via `Fn.importValue`. The four existing AI Lambdas (AiSummaryFn,
AiBomExportFn, AiGithubFn, EntitiesApiFn) stay in CisoCopilotApi — "new
work only" scope.

**Spec:** `docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md`
**Plan:** `docs/superpowers/plans/2026-06-10-ai-stack-extraction.md`

**Operational rules — read before deploying:**

- **Deploy order, first time:** `npx cdk deploy CisoCopilotApi CisoCopilotAi --require-approval never`. CDK auto-resolves the order from `Fn.importValue` references; the explicit `addDependency` in `bin/platform.ts` is belt-and-suspenders.
- **AI route changes require full deploy.** `cdk deploy CisoCopilotAi --hotswap` works for Lambda code/env-only changes. It does NOT fire the `AwsCustomResource` that re-points the stage, so any route addition/removal/rename needs `cdk deploy CisoCopilotAi` (no `--hotswap`).
- **The `aiStackExtensionVersion: 'v1'` pin in `api-stack.ts` is load-bearing.** It keeps `CisoCopilotApi`'s `latestDeployment` logicalId stable when `CisoCopilotApi` is deployed independently — without it, a `CisoCopilotApi`-only redeploy would silently drop all routes added by `CisoCopilotAi`. Bump `'v1'` → `'v2'` only on intentional major route-layout changes.
- **`/v1/ai/_health` is a stub.** It exists only to prove the wiring. Sub-slice 1.4's first real Workspace OAuth route should delete it.
- **Both stacks together must stay under 500 resources each.** CisoCopilotApi: ~494. CisoCopilotAi: ~12 after this work. Headroom for Sub-slice 1.4+1.5: ~480 in CisoCopilotAi, ~6 in CisoCopilotApi. Watch the latter — non-AI features will eat that fast.
```

- [ ] **Step 2: Add the ARCHITECTURE.md ADR**

Find the existing ADR list in `ARCHITECTURE.md` (search for `ADR-001` or similar). Append a new ADR with the next sequential number (likely ADR-016 or wherever the list ends):

```markdown
### ADR-NNN. Cross-stack RestApi extension via `fromRestApiAttributes`

**Context.** `CisoCopilotApi` was approaching the CloudFormation 500-resource hard cap (494/500 as of 2026-06-08). AI Security Sub-slice 1.4 alone added ~12-16 resources, which would exceed the cap. Continued route-deletion to free space was unsustainable.

**Decision.** New AI-domain Lambdas land in a separate `CisoCopilotAi` stack that imports the existing `RestApi` + Cognito authorizer via named CFN exports (`Fn.importValue`). One API Gateway, one `/v1` stage, one CORS config, one authorizer. The Workspace OAuth Lambdas (Sub-slice 1.4) and all subsequent AI features go here by default.

**Mitigation of known limitations:**
- API Gateway stages don't auto-redeploy when routes are added from a different stack. Mitigated by an `AwsCustomResource` in `CisoCopilotAi` that calls `apigateway:UpdateStage` on every deploy where the AI `Deployment`'s logicalId changes.
- `CisoCopilotApi`-only redeploys would otherwise overwrite the served deployment and drop AI routes. Mitigated by a `latestDeployment?.addToLogicalId({ aiStackExtensionVersion: 'v1' })` pin in `CisoCopilotApi` that keeps its deployment logicalId stable across non-AI changes.

**Rejected alternatives:**
- Separate RestApi at `ai-api.shasta.io` — split CORS, split rate-limit, web/iOS clients need to know which host to call per route.
- CloudFront path-routing to two API Gateways — extra failure mode, doubles monitoring surface.
- Move all existing AI Lambdas now — bigger blast radius for a deploy that was primarily about unblocking Sub-slice 1.4.

**Boundary discipline:** "New work only." Existing AI Lambdas stay in `CisoCopilotApi`. Opportunistic migration is allowed when `CisoCopilotApi` hits the cap again, not before.

**Spec:** `docs/superpowers/specs/2026-06-10-ai-stack-extraction-design.md`.
```

(Replace `NNN` with the next free ADR number after counting the existing ADRs in the file.)

- [ ] **Step 3: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add HANDOFF.md ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
docs(handoff,architecture): document CisoCopilotAi stack + cross-stack ADR

HANDOFF gets operational rules (deploy order, hotswap-skip-CR gotcha,
'v1' pin discipline, headroom watch).  ARCHITECTURE gets the ADR for
cross-stack RestApi extension via fromRestApiAttributes +
AwsCustomResource stage re-point.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Optional — remove the `_health` stub once Sub-slice 1.4's first real route lands

**Files (when triggered):**
- Delete: `platform/lambda/ai_health/`
- Modify: `platform/lib/ai-stack.ts` — remove `AiHealthFn` + the `_health` route + the entry from the Deployment `addToLogicalId` hash

Trigger: Sub-slice 1.4 has a working `POST /v1/ai/workspace/initiate` route deployed and curl-verified. Do NOT remove the stub before that — it's the only thing keeping `CisoCopilotAi` non-empty and therefore the only thing that exercises the AwsCustomResource on routine deploys.

**Skip this task and merge as-is** if Sub-slice 1.4 work is starting immediately after this plan — the stub gets organically replaced.

- [ ] **Step 1: Confirm Sub-slice 1.4 has a real route in prod**

```bash
curl -i -s -o /dev/null -w "%{http_code}\n" "https://${API_DOMAIN:-api.shasta.io}/v1/ai/workspace/initiate"
```

Expected: `401` (Cognito-gated) or `405` (method-not-allowed if you used the wrong verb). NOT `404`.

- [ ] **Step 2: Remove the stub Lambda directory**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
rm -rf platform/lambda/ai_health/
```

- [ ] **Step 3: Remove the references from `ai-stack.ts`**

Delete the `AiHealthFn` `new lambda.Function(...)` block, the `aiRes.addResource('_health').addMethod(...)` line, and the `aiHealth: aiHealthFn.functionArn` entry inside `aiRoutesDeployment.addToLogicalId({...})`.

- [ ] **Step 4: cdk synth + deploy**

```bash
cd /Users/kkmookhey/Projects/CISOBrief/platform
npx cdk diff CisoCopilotAi 2>&1 | head -20
npx cdk deploy CisoCopilotAi --require-approval never
```

- [ ] **Step 5: Verify the stub is gone and real routes still work**

```bash
curl -i -s -o /dev/null -w "%{http_code}\n" "https://${API_DOMAIN:-api.shasta.io}/v1/ai/_health"
# Expect: 404 (route is gone — that's the goal)

curl -i -s -o /dev/null -w "%{http_code}\n" "https://${API_DOMAIN:-api.shasta.io}/v1/ai/workspace/initiate"
# Expect: 401 (still alive)
```

- [ ] **Step 6: Commit**

```bash
cd /Users/kkmookhey/Projects/CISOBrief
git add platform/
git commit -m "$(cat <<'EOF'
chore(ai-stack): drop /v1/ai/_health stub; Sub-slice 1.4 routes are live

The stub Lambda's job (proving the cross-stack wiring) is done. Real
AI routes from Sub-slice 1.4 are now the load-bearers of the
AwsCustomResource exercise.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review checklist

- ✅ Spec §0 baseline + §1-7 requirements all map to tasks: exports (T1), pin (T2), stub Lambda (T3), skeleton (T4), route mount (T5), Deployment + CR (T6), wire-up (T7), deploy + verify (T8), §5.d regression test (T9), docs (T10), cleanup (T11).
- ✅ Every code step has exact file path + line refs OR complete code block. No "implement appropriate error handling" hand-waves.
- ✅ Type/identifier consistency: `aiHealthFn`, `aiRoutesDeployment`, `aiRes` used consistently across T5 + T6 + T11.
- ✅ Cognito authorizer construct name `cognitoAuth` matches `platform/lib/api-stack.ts:553` — confirmed before writing.
- ✅ Stage name `'v1'` matches `platform/lib/api-stack.ts:529` — confirmed before writing.
- ✅ TDD applied where it makes sense: T3 Lambda has failing-test-first. CDK tasks use `cdk synth` + `cdk diff` + curl as the "test" because the codebase has no CDK assertion-test infra (out-of-scope per spec §3).
- ✅ Frequent commits: one commit per task (10 tasks → 10 commits except T8/T9 which are verification-only).
