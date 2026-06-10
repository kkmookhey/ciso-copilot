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
    // Suppress unused-variable warning in skeleton state; consumed in Task 5
    void aiRes;
    void authedOpts;
    void props;
  }
}
