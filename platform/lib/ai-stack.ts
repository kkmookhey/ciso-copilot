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

    // /v1/ai resource — Lambdas + routes attached in subsequent tasks
    const aiRes = api.root.addResource('ai');

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

    // ── Deployment: re-hashes logicalId whenever an AI Lambda ARN changes ──
    const aiRoutesDeployment = new apigw.Deployment(this, 'AiRoutesDeployment', { api });
    aiRoutesDeployment.addToLogicalId({
      aiHealth: aiHealthFn.functionArn,
      // Extend per new AI Lambda registered on this stack (Sub-slice 1.4+).
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
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: cr.AwsCustomResourcePolicy.ANY_RESOURCE,
      }),
    });

    // Mute unused-variable warnings for handles consumed in Sub-slice 1.4
    void authedOpts;
    void props;
  }
}
