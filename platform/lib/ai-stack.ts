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

    // ── Atomic deploy-and-update via Custom Resource ──
    // We tried the CFN-managed apigw.Deployment + separate UpdateStage CR
    // path and hit a reproducible bug: the CFN-created Deployment's
    // CreateDeployment API call did NOT include cross-stack Methods in its
    // snapshot, even with explicit addDependency. Same call made manually
    // via aws-cli works fine. The cleanest workaround is to skip CFN's
    // Deployment entirely and have one CR do `createDeployment` with
    // `stageName: 'v1'`, which atomically creates a fresh snapshot (at CR
    // execution time, after all Methods are live) AND points the stage at
    // it in one API call. No CFN→APIGW timing window to lose.
    //
    // The CR runs on every stack update where any AI Lambda ARN changes,
    // because the `description` parameter embeds the ARNs — CR sees them as
    // changed inputs and re-fires. To force a redeploy when nothing else
    // changed (e.g., emergency recovery), bump REDEPLOY_TRIGGER below.
    const REDEPLOY_TRIGGER = 'v3-cr-managed-deployment';
    const aiLambdaFingerprint = [aiHealthFn.functionArn].join('|');

    new cr.AwsCustomResource(this, 'CreateDeploymentAndPointStage', {
      onUpdate: {
        service: 'APIGateway',
        action:  'createDeployment',
        parameters: {
          restApiId:   cdk.Fn.importValue('CisoCopilotApi-RestApiId'),
          stageName:   'v1',
          description: `CisoCopilotAi auto-deploy [${REDEPLOY_TRIGGER}] [${aiLambdaFingerprint}]`,
        },
        // The created deployment's id becomes the physicalResourceId. On
        // CFN update, if any input changes (description changes when an
        // ARN changes), CR fires and gets a new id.
        physicalResourceId: cr.PhysicalResourceId.fromResponse('id'),
      },
      // Same call shape on initial Create too (handles fresh-stack case).
      onCreate: {
        service: 'APIGateway',
        action:  'createDeployment',
        parameters: {
          restApiId:   cdk.Fn.importValue('CisoCopilotApi-RestApiId'),
          stageName:   'v1',
          description: `CisoCopilotAi auto-deploy [${REDEPLOY_TRIGGER}] [${aiLambdaFingerprint}]`,
        },
        physicalResourceId: cr.PhysicalResourceId.fromResponse('id'),
      },
      // Needs apigateway:POST on /deployments + apigateway:PATCH on /stages/v1
      // (createDeployment with stageName does both internally). HTTP-verb-based
      // IAM, not SDK-call-name-based — `fromSdkCalls` would generate the wrong
      // action name (`apigateway:createDeployment` is not a real IAM action).
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ['apigateway:POST', 'apigateway:PATCH'],
          resources: [
            `arn:aws:apigateway:${cdk.Stack.of(this).region}::/restapis/${cdk.Fn.importValue('CisoCopilotApi-RestApiId')}/deployments`,
            `arn:aws:apigateway:${cdk.Stack.of(this).region}::/restapis/${cdk.Fn.importValue('CisoCopilotApi-RestApiId')}/stages/v1`,
          ],
        }),
      ]),
    });

    // Method handle preserved so that addDependency could be added if we
    // ever switch back to a CFN-managed Deployment.
    void healthMethod;

    // Mute unused-variable warnings for handles consumed in Sub-slice 1.4
    void authedOpts;
    void props;
  }
}
