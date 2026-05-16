import * as cdk from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as path from 'path';

interface ApiStackProps extends cdk.StackProps {
  userPool:       cognito.UserPool;
  userPoolClient: cognito.UserPoolClient;
  dbCluster:      rds.DatabaseCluster;
}

export class ApiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    // ===== /me Lambda — returns caller's user + tenant status (used by iOS poll) =====
    const meFn = new lambda.Function(this, 'MeFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'me')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        DB_CLUSTER_ARN: props.dbCluster.clusterArn,
        DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
        DB_NAME:        'ciso_copilot',
      },
    });
    props.dbCluster.grantDataApiAccess(meFn);

    // ===== /admin/tenants/{id}/decision Lambda — token-authed via query string =====
    const adminDecisionFn = new lambda.Function(this, 'AdminDecisionFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'admin_decision')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        DB_CLUSTER_ARN: props.dbCluster.clusterArn,
        DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
        DB_NAME:        'ciso_copilot',
        APPROVAL_TOKEN_SECRET_NAME: 'ciso-copilot/approval-signing-key',
      },
    });
    props.dbCluster.grantDataApiAccess(adminDecisionFn);
    adminDecisionFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ses:SendEmail', 'ses:SendRawEmail'],
      resources: ['*'],
    }));
    adminDecisionFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/*`],
    }));

    // ===== REST API =====
    const api = new apigw.RestApi(this, 'RestApi', {
      restApiName: 'ciso-copilot',
      deployOptions: {
        stageName: 'v1',
        loggingLevel: apigw.MethodLoggingLevel.INFO,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
    });

    const cognitoAuth = new apigw.CognitoUserPoolsAuthorizer(this, 'CognitoAuthorizer', {
      cognitoUserPools: [props.userPool],
    });

    // GET /me  — JWT-authed
    const me = api.root.addResource('me');
    me.addMethod('GET', new apigw.LambdaIntegration(meFn), {
      authorizer:        cognitoAuth,
      authorizationType: apigw.AuthorizationType.COGNITO,
    });

    // GET /admin/tenants/{id}/decision  — token-authed via query string (no JWT)
    const admin = api.root
      .addResource('admin')
      .addResource('tenants')
      .addResource('{id}')
      .addResource('decision');
    admin.addMethod('GET', new apigw.LambdaIntegration(adminDecisionFn));

    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
  }
}
