import * as cdk from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import { Construct } from 'constructs';
import * as path from 'path';

interface ApiStackProps extends cdk.StackProps {
  userPool:          cognito.UserPool;
  userPoolClient:    cognito.UserPoolClient;
  dbCluster:         rds.DatabaseCluster;
  eventBus:          events.EventBus;
  cdnDistribution:   cloudfront.Distribution;
  shastaRunner:      lambda.IFunction;
  shastaRunnerAzure: lambda.IFunction;
}

export class ApiStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    const dbEnv = {
      DB_CLUSTER_ARN: props.dbCluster.clusterArn,
      DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
      DB_NAME:        'ciso_copilot',
    };

    // ========================================================================
    // /me — caller's user + tenant status
    // ========================================================================
    const meFn = new lambda.Function(this, 'MeFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'me')),
      timeout: cdk.Duration.seconds(10),
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(meFn);

    // ========================================================================
    // /admin/tenants/{id}/decision — token-authed approval flow
    // ========================================================================
    const adminDecisionFn = new lambda.Function(this, 'AdminDecisionFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'admin_decision')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        ...dbEnv,
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

    // ========================================================================
    // Phase A endpoints
    // ========================================================================

    const cfnTemplateUrl = `https://${props.cdnDistribution.distributionDomainName}/cfn/aws-onboard.yaml`;
    // Built using the v1 prefix from the existing API stage. We use a self-referential
    // string for the complete-webhook because we don't have the API URL until after
    // synth — accepting the indirection.
    const completeWebhookUrl = `https://xoljryrb7i.execute-api.${this.region}.amazonaws.com/v1/onboarding/aws/complete`;

    const onboardingInitiateFn = new lambda.Function(this, 'OnboardingAwsInitiateFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_aws_initiate')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        ...dbEnv,
        CFN_TEMPLATE_URL:      cfnTemplateUrl,
        COMPLETE_WEBHOOK_URL:  completeWebhookUrl,
        OUR_ACCOUNT_ID:        this.account,
        CENTRAL_EVENT_BUS_ARN: props.eventBus.eventBusArn,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingInitiateFn);

    const onboardingCompleteFn = new lambda.Function(this, 'OnboardingAwsCompleteFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_aws_complete')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...dbEnv,
        CENTRAL_EVENT_BUS_ARN: props.eventBus.eventBusArn,
        SHASTA_RUNNER_FN:      props.shastaRunner.functionName,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingCompleteFn);
    onboardingCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'secretsmanager:CreateSecret',
        'secretsmanager:PutSecretValue',
        'secretsmanager:GetSecretValue',
      ],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/connections/*`],
    }));
    onboardingCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['events:PutPermission', 'events:DescribeEventBus'],
      resources: [props.eventBus.eventBusArn],
    }));
    // Allow async-invoke of shasta-runner to kick off the initial scan.
    props.shastaRunner.grantInvoke(onboardingCompleteFn);

    const connectionsListFn = new lambda.Function(this, 'ConnectionsListFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'connections_list')),
      timeout: cdk.Duration.seconds(10),
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(connectionsListFn);

    const findingsListFn = new lambda.Function(this, 'FindingsListFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'findings_list')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(findingsListFn);

    // ========================================================================
    // REST API + authorizer
    // ========================================================================
    const api = new apigw.RestApi(this, 'RestApi', {
      restApiName: 'ciso-copilot',
      deployOptions: {
        stageName:    'v1',
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
    const authedOpts: apigw.MethodOptions = {
      authorizer:        cognitoAuth,
      authorizationType: apigw.AuthorizationType.COGNITO,
    };

    // GET /me  — JWT-authed
    api.root.addResource('me').addMethod('GET', new apigw.LambdaIntegration(meFn), authedOpts);

    // GET /admin/tenants/{id}/decision  — token-authed via query string
    api.root
      .addResource('admin')
      .addResource('tenants')
      .addResource('{id}')
      .addResource('decision')
      .addMethod('GET', new apigw.LambdaIntegration(adminDecisionFn));

    // Onboarding
    const onboarding   = api.root.addResource('onboarding');
    const onboardingAws = onboarding.addResource('aws');
    onboardingAws.addResource('initiate').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingInitiateFn), authedOpts,
    );
    // /complete is NOT JWT-authed — authenticated via external_id in the body.
    onboardingAws.addResource('complete').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingCompleteFn),
    );

    // GET /connections
    api.root.addResource('connections').addMethod(
      'GET', new apigw.LambdaIntegration(connectionsListFn), authedOpts,
    );

    // GET /findings
    api.root.addResource('findings').addMethod(
      'GET', new apigw.LambdaIntegration(findingsListFn), authedOpts,
    );

    // ========================================================================
    // Phase B — Azure onboarding
    // ========================================================================

    const azureScriptUrl = `https://${props.cdnDistribution.distributionDomainName}/azure/onboard.sh`;

    const onboardingAzureInitiateFn = new lambda.Function(this, 'OnboardingAzureInitiateFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_azure_initiate')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        ...dbEnv,
        AZURE_SCRIPT_URL: azureScriptUrl,
        OUR_ACCOUNT_ID:   this.account,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingAzureInitiateFn);

    const onboardingAzureCompleteFn = new lambda.Function(this, 'OnboardingAzureCompleteFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_azure_complete')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...dbEnv,
        AZURE_RUNNER_FN: props.shastaRunnerAzure.functionName,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingAzureCompleteFn);
    onboardingAzureCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'secretsmanager:CreateSecret',
        'secretsmanager:PutSecretValue',
        'secretsmanager:GetSecretValue',
      ],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/connections/*`],
    }));
    props.shastaRunnerAzure.grantInvoke(onboardingAzureCompleteFn);

    const onboardingAzure = onboarding.addResource('azure');
    onboardingAzure.addResource('initiate').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingAzureInitiateFn), authedOpts,
    );
    onboardingAzure.addResource('complete').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingAzureCompleteFn),
    );

    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
  }
}
