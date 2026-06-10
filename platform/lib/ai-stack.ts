// platform/lib/ai-stack.ts
import * as cdk from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
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

    // Cross-stack authorizer reuse. CDK v2 doesn't expose a fromAttributes
    // factory for Authorizer, but IAuthorizer is a tiny interface (id +
    // type) that we can satisfy with an inline object — no Construct, no
    // duplicate AWS::ApiGateway::Authorizer resource. The id is imported
    // from CisoCopilotApi's CfnOutput exported in api-stack.ts.
    const authorizer: apigw.IAuthorizer = {
      authorizerId:      cdk.Fn.importValue('CisoCopilotApi-CognitoAuthorizerId'),
      authorizationType: apigw.AuthorizationType.COGNITO,
    };

    const authedOpts: apigw.MethodOptions = {
      authorizationType: apigw.AuthorizationType.COGNITO,
      authorizer,
    };

    // /v1/ai already exists on the imported RestApi (created by CisoCopilotApi
    // at api-stack.ts:954, which owns the existing /v1/ai/* AI Lambdas). API
    // Gateway forbids two resources with the same name under one parent, so
    // we IMPORT the existing /ai resource here instead of creating a new one.
    // Children added below land as siblings of CisoCopilotApi's /ai/summary,
    // /ai/bom, /ai/connections, etc.
    const aiRes = apigw.Resource.fromResourceAttributes(this, 'ImportedAiResource', {
      restApi:    api,
      resourceId: cdk.Fn.importValue('CisoCopilotApi-AiResourceId'),
      path:       '/ai',
    });

    // ── Stub Lambda: proves end-to-end wiring; deleted by Sub-slice 1.4's first real route ──
    const aiHealthFn = new lambda.Function(this, 'AiHealthFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ai_health')),
      timeout: cdk.Duration.seconds(5),
      description: 'Stub /v1/ai/_health — verifies CisoCopilotAi → API Gateway wiring',
    });

    // GET /v1/ai/_health (no auth — wiring test, not user-facing)
    const healthMethod = aiRes.addResource('_health').addMethod(
      'GET', new apigw.LambdaIntegration(aiHealthFn),
    );

    // ── Deployment: re-hashes logicalId whenever an AI Lambda ARN changes ──
    // CRITICAL: must depend on the Method explicitly. The Deployment's
    // CreateDeployment API call snapshots the live API state at creation time,
    // so if CFN creates Deployment before Method (as it does by default in
    // parallel), the snapshot is empty and the route doesn't serve. The
    // high-level RestApi construct adds these deps automatically; with
    // fromRestApiAttributes we lose that and must add them by hand.
    const aiRoutesDeployment = new apigw.Deployment(this, 'AiRoutesDeployment', { api });
    aiRoutesDeployment.node.addDependency(healthMethod);
    aiRoutesDeployment.addToLogicalId({
      aiHealth: aiHealthFn.functionArn,
      // Extend per new AI Lambda registered on this stack (Sub-slice 1.4+).
      // When adding a new Method, also `aiRoutesDeployment.node.addDependency(newMethod)`.
    });

    // Expose deployment id so Task 8 verification can compare it to the
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
      // API Gateway management plane uses HTTP-verb IAM actions, NOT SDK-call
      // names. `fromSdkCalls` would infer `apigateway:updateStage` which is
      // not a valid IAM action — the actual permission needed is `apigateway:PATCH`
      // on the stage ARN (PATCH is the HTTP verb the SDK uses to call
      // UpdateStage under the hood). Scope tight to the imported RestApi's v1 stage.
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions:   ['apigateway:PATCH'],
          resources: [
            `arn:aws:apigateway:${cdk.Stack.of(this).region}::/restapis/${cdk.Fn.importValue('CisoCopilotApi-RestApiId')}/stages/v1`,
          ],
        }),
      ]),
    });

    // Mute unused-variable warnings for handles consumed in Sub-slice 1.4
    void authedOpts;
    void props;
  }
}
