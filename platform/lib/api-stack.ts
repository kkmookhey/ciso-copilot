import * as cdk from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as events from 'aws-cdk-lib/aws-events';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import { Construct } from 'constructs';
import * as path from 'path';

interface ApiStackProps extends cdk.StackProps {
  userPool:           cognito.UserPool;
  userPoolClient:     cognito.UserPoolClient;   // iOS
  webClient:          cognito.UserPoolClient;
  dbCluster:          rds.DatabaseCluster;
  eventBus:           events.EventBus;
  cdnDistribution:    cloudfront.Distribution;
  shastaRunnerEntra:  lambda.IFunction;
  gcpScanTaskDefFamily:             string;
  scanCluster:                 ecs.Cluster;
  // Task def family name (e.g. "ciso-copilot-aws-scan"). Passed as a plain
  // string to avoid a cross-stack CFN export on an ARN that changes every
  // revision. ECS RunTask accepts the family name and picks the latest active
  // revision; the IAM policy uses a wildcard (:*) over all revisions.
  scanTaskDefFamily:           string;
  scanTaskDefTaskRoleArn:      string;
  scanTaskDefExecutionRoleArn: string;
  azureScanTaskDefFamily:           string;
  vpc:                     ec2.IVpc;
  scanTaskSecurityGroupId: string;
  entraAppId:         string;
  entraScannerSecret: secretsmanager.ISecret;
  openaiApiKeySecret: secretsmanager.ISecret;
  aiScanQueue:        sqs.IQueue;
  cognitoDomain:      string;   // e.g. ciso-copilot.auth.us-east-1.amazoncognito.com
  webRedirectUri:     string;   // e.g. https://<cdn>/callback
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

    // CFN Console requires an S3 URL (not CloudFront). We presign a short-lived
    // S3 GET in the Lambda instead; here we just pass bucket+key.
    const cfnTemplateBucket = `ciso-copilot-cdn-${this.account}`;
    const cfnTemplateKey    = 'cfn/aws-onboard.yaml';
    const cfnTemplateUrl    = `https://${props.cdnDistribution.distributionDomainName}/cfn/aws-onboard.yaml`;
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
        CFN_TEMPLATE_BUCKET:   cfnTemplateBucket,
        CFN_TEMPLATE_KEY:      cfnTemplateKey,
        COMPLETE_WEBHOOK_URL:  completeWebhookUrl,
        OUR_ACCOUNT_ID:        this.account,
        CENTRAL_EVENT_BUS_ARN: props.eventBus.eventBusArn,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingInitiateFn);
    onboardingInitiateFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['s3:GetObject'],
      resources: [`arn:aws:s3:::${cfnTemplateBucket}/${cfnTemplateKey}`],
    }));

    const onboardingCompleteFn = new lambda.Function(this, 'OnboardingAwsCompleteFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_aws_complete')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...dbEnv,
        CENTRAL_EVENT_BUS_ARN:  props.eventBus.eventBusArn,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        // Pass the family name; ECS RunTask resolves to the latest active
        // revision. This avoids a cross-stack CFN export on a revision ARN.
        SCAN_TASK_DEF_ARN:      props.scanTaskDefFamily,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
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
    // Allow starting the scanner Fargate task to kick off the initial scan.
    // Wildcard over all revisions (:*) so this policy survives task-def updates
    // without a cross-stack CFN export dependency.
    onboardingCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.scanTaskDefFamily}:*`],
    }));
    onboardingCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        props.scanTaskDefTaskRoleArn,
        props.scanTaskDefExecutionRoleArn,
      ],
    }));

    const connectionsListFn = new lambda.Function(this, 'ConnectionsListFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'connections_list')),
      timeout: cdk.Duration.seconds(15),
      environment: {
        ...dbEnv,
        AZURE_SCAN_TASK_DEF:    props.azureScanTaskDefFamily,
        ENTRA_RUNNER_FN:        props.shastaRunnerEntra.functionName,
        GCP_SCAN_TASK_DEF:      props.gcpScanTaskDefFamily,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_TASK_DEF_ARN:      props.scanTaskDefFamily,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
      },
    });
    props.dbCluster.grantDataApiAccess(connectionsListFn);
    // Rescan dispatches into the Entra scanner Lambda (AWS / Azure / GCP
    // rescans use ecs:RunTask) + reads/deletes the per-connection secret.
    props.shastaRunnerEntra.grantInvoke(connectionsListFn);
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'secretsmanager:GetSecretValue',
        'secretsmanager:DeleteSecret',
      ],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/connections/*`],
    }));
    // Allow the rescan path to start the v2 Fargate scanner task.
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.scanTaskDefFamily}:*`],
    }));
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.azureScanTaskDefFamily}:*`],
    }));
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        props.scanTaskDefTaskRoleArn,
        props.scanTaskDefExecutionRoleArn,
      ],
    }));
    // Azure scan task roles — name-pattern scoped (the Azure task def lives
    // in the Scan stack; a name pattern avoids a cross-stack export).
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [`arn:aws:iam::${this.account}:role/CisoCopilotScan-AzureScanTaskDef*`],
    }));
    // GCP scan task — RunTask on the gcp-scan family; PassRole for the
    // task role (the literal 'ciso-copilot-gcp-scanner' role) + the
    // CDK-named execution role (name-pattern scoped, no cross-stack export).
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.gcpScanTaskDefFamily}:*`],
    }));
    connectionsListFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        `arn:aws:iam::${this.account}:role/ciso-copilot-gcp-scanner`,
        `arn:aws:iam::${this.account}:role/CisoCopilotScan-GcpScanTaskDef*`,
      ],
    }));

    const findingsListFn = new lambda.Function(this, 'FindingsListFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'findings_list')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(findingsListFn);

    const eventsListFn = new lambda.Function(this, 'EventsListFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'events_list')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(eventsListFn);

    const complianceSummaryFn = new lambda.Function(this, 'ComplianceSummaryFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'compliance_summary')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(complianceSummaryFn);

    const findingsSummaryFn = new lambda.Function(this, 'FindingsSummaryFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'findings_summary')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(findingsSummaryFn);

    const aiSummaryFn = new lambda.Function(this, 'AiSummaryFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ai_summary')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(aiSummaryFn);

    const findingsRollupFn = new lambda.Function(this, 'FindingsRollupFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'findings_rollup')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 1024,    // aggregates ~500 rows in-memory; headroom for growth
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(findingsRollupFn);

    const risksFn = new lambda.Function(this, 'RisksFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'risks')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(risksFn);

    const policiesFn = new lambda.Function(this, 'PoliciesFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'policies')),
      timeout: cdk.Duration.minutes(5),      // generate-all enriches 8 policies in parallel
      memorySize: 1024,                      // ThreadPoolExecutor + 8 HTTPS connections
      environment: { ...dbEnv, ANTHROPIC_SECRET_NAME: 'ciso-copilot/anthropic-api-key' },
    });
    props.dbCluster.grantDataApiAccess(policiesFn);
    policiesFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/anthropic-api-key*`],
    }));

    const questionnairesFn = new lambda.Function(this, 'QuestionnairesFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'questionnaires')),
      timeout: cdk.Duration.seconds(45),     // Anthropic suggest can take ~5-15s
      memorySize: 512,
      environment: { ...dbEnv, ANTHROPIC_SECRET_NAME: 'ciso-copilot/anthropic-api-key' },
    });
    props.dbCluster.grantDataApiAccess(questionnairesFn);
    questionnairesFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/anthropic-api-key*`],
    }));

    const trustFn = new lambda.Function(this, 'TrustFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'trust')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(trustFn);

    const adminTenantsFn = new lambda.Function(this, 'AdminTenantsFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'admin_tenants')),
      timeout: cdk.Duration.seconds(15),
      memorySize: 512,
      environment: {
        ...dbEnv,
        ADMIN_EMAILS: 'kkmookhey@gmail.com,kkmookhey@transilience.ai,kkmookhey@networkintelligence.ai',
        DOMAIN:       'settlingforless.com',
      },
    });
    props.dbCluster.grantDataApiAccess(adminTenantsFn);
    adminTenantsFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ses:SendEmail', 'ses:SendRawEmail'],
      resources: ['*'],
    }));

    // ========================================================================
    // /v1/ai/connections/github/* — GitHub App install + listing
    // ========================================================================
    const aiGithubFn = new lambda.Function(this, 'AiGithubFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'ai_github'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          // Force the bundling container to linux/amd64 AND tell pip to pull
          // manylinux x86_64 wheels (not the host platform's). Without this,
          // pip on Apple-Silicon Macs installs darwin-arm64 cryptography
          // wheels and the Lambda fails at import with
          // "_rust.abi3.so: cannot open shared object file".
          platform: 'linux/amd64',
          command: [
            'bash', '-c',
            'pip install --no-cache-dir ' +
            '--platform manylinux2014_x86_64 ' +
            '--implementation cp ' +
            '--python-version 3.12 ' +
            '--only-binary=:all: ' +
            '-r requirements.txt -t /asset-output && ' +
            'cp -au . /asset-output',
          ],
        },
      }),
      timeout:    cdk.Duration.seconds(15),
      memorySize: 512,
      environment: {
        ...dbEnv,
        GITHUB_APP_SECRET_ARN: `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials`,
        STATE_JWT_SECRET_ARN:  `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/state-jwt-signing-key`,
        GITHUB_APP_SLUG:       'ciso-copilot',
        WEB_CALLBACK_URL:      'https://shasta.transilience.cloud/ai/install/callback',
      },
    });
    props.dbCluster.grantDataApiAccess(aiGithubFn);
    aiGithubFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials*`,
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/state-jwt-signing-key*`,
      ],
    }));

    // ========================================================================
    // /v1/ai/scans + /v1/entities — start scans, browse the unified inventory,
    // walk the per-entity trust graph. Replaces ai_scan_api (SP1).
    // ========================================================================
    const entitiesApiFn = new lambda.Function(this, 'EntitiesApiFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'entities_api')),
      timeout:    cdk.Duration.seconds(15),
      memorySize: 512,
      environment: {
        ...dbEnv,
        AI_SCAN_QUEUE_URL: props.aiScanQueue.queueUrl,
      },
    });
    props.dbCluster.grantDataApiAccess(entitiesApiFn);
    props.aiScanQueue.grantSendMessages(entitiesApiFn);

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

    // Gateway-level rejections (e.g., 401 from the Cognito authorizer on expired
    // tokens) don't reach our Lambdas, so they don't pick up the CORS header
    // those Lambdas now emit. Add them at the gateway response layer too,
    // otherwise the browser dies on CORS preflight before showing the real
    // error and the user just sees a generic bounce.
    const corsHeaders = {
      'Access-Control-Allow-Origin':  "'*'",
      'Access-Control-Allow-Headers': "'Content-Type,Authorization'",
    };
    api.addGatewayResponse('Default4xx',   { type: apigw.ResponseType.DEFAULT_4XX,   responseHeaders: corsHeaders });
    api.addGatewayResponse('Default5xx',   { type: apigw.ResponseType.DEFAULT_5XX,   responseHeaders: corsHeaders });
    api.addGatewayResponse('Unauthorized', { type: apigw.ResponseType.UNAUTHORIZED,  responseHeaders: corsHeaders, statusCode: '401' });
    api.addGatewayResponse('AccessDenied', { type: apigw.ResponseType.ACCESS_DENIED, responseHeaders: corsHeaders });

    const cognitoAuth = new apigw.CognitoUserPoolsAuthorizer(this, 'CognitoAuthorizer', {
      cognitoUserPools: [props.userPool],
    });
    const authedOpts: apigw.MethodOptions = {
      authorizer:        cognitoAuth,
      authorizationType: apigw.AuthorizationType.COGNITO,
    };

    // GET /me  — JWT-authed
    api.root.addResource('me').addMethod('GET', new apigw.LambdaIntegration(meFn), authedOpts);

    // /admin namespace — shared between the email-link decision endpoint
    // (token-authed via query string, no Cognito) and the in-app admin
    // endpoints (Cognito-authed, gated to ADMIN_EMAILS).
    const adminRes     = api.root.addResource('admin');
    const adminTenants = adminRes.addResource('tenants');
    const adminTenantById = adminTenants.addResource('{id}');

    // GET /admin/tenants/{id}/decision  — token-authed via query string (email link)
    adminTenantById
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

    // /connections — list + per-id rescan + delete (same Lambda dispatches).
    const connectionsRes  = api.root.addResource('connections');
    const connectionByIdRes = connectionsRes.addResource('{id}');
    connectionsRes.addMethod('GET', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addMethod('DELETE', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addMethod('PATCH', new apigw.LambdaIntegration(connectionsListFn), authedOpts);
    connectionByIdRes.addResource('rescan').addMethod(
      'POST', new apigw.LambdaIntegration(connectionsListFn), authedOpts,
    );

    // GET /findings
    api.root.addResource('events').addMethod(
      'GET', new apigw.LambdaIntegration(eventsListFn), authedOpts,
    );

    api.root.addResource('compliance').addResource('summary').addMethod(
      'GET', new apigw.LambdaIntegration(complianceSummaryFn), authedOpts,
    );


    const findingsRes = api.root.addResource('findings');
    // GET /admin/tenants            — list pending/all tenants (Cognito-authed)
    // POST /admin/tenants/{id}/action — body {decision:"approve"|"reject"}
    adminTenants.addMethod('GET', new apigw.LambdaIntegration(adminTenantsFn), authedOpts);
    adminTenantById.addResource('action').addMethod(
      'POST', new apigw.LambdaIntegration(adminTenantsFn), authedOpts,
    );

    // /risks — risk register CRUD
    const risksRes = api.root.addResource('risks');
    risksRes.addMethod('GET',  new apigw.LambdaIntegration(risksFn), authedOpts);
    risksRes.addMethod('POST', new apigw.LambdaIntegration(risksFn), authedOpts);
    const risksId = risksRes.addResource('{id}');
    risksId.addMethod('PATCH', new apigw.LambdaIntegration(risksFn), authedOpts);

    // /policies — policy templates + drafts (lifted from Shasta policies/)
    const policiesRes = api.root.addResource('policies');
    policiesRes.addMethod('GET',  new apigw.LambdaIntegration(policiesFn), authedOpts);
    policiesRes.addMethod('POST', new apigw.LambdaIntegration(policiesFn), authedOpts);
    policiesRes.addResource('templates').addMethod('GET', new apigw.LambdaIntegration(policiesFn), authedOpts);
    policiesRes.addResource('generate-all').addMethod('POST', new apigw.LambdaIntegration(policiesFn), authedOpts);
    const policyId = policiesRes.addResource('{id}');
    policyId.addMethod('GET',   new apigw.LambdaIntegration(policiesFn), authedOpts);
    policyId.addMethod('PATCH', new apigw.LambdaIntegration(policiesFn), authedOpts);
    policyId.addResource('enrich').addMethod('POST', new apigw.LambdaIntegration(policiesFn), authedOpts);

    // /trust (authed) + /public/trust/{slug} (UNAUTHED) — public posture page.
    const trustRes = api.root.addResource('trust');
    trustRes.addMethod('GET', new apigw.LambdaIntegration(trustFn), authedOpts);
    trustRes.addMethod('PUT', new apigw.LambdaIntegration(trustFn), authedOpts);
    const publicRes = api.root.addResource('public');
    publicRes.addResource('trust').addResource('{slug}').addMethod(
      'GET', new apigw.LambdaIntegration(trustFn),  // NO authedOpts — public
    );

    // /questionnaires — questionnaire banks + auto-fill from findings (Shasta questionnaire/ lift)
    const qsRes = api.root.addResource('questionnaires');
    qsRes.addMethod('GET',  new apigw.LambdaIntegration(questionnairesFn), authedOpts);
    qsRes.addMethod('POST', new apigw.LambdaIntegration(questionnairesFn), authedOpts);
    qsRes.addResource('templates').addMethod('GET', new apigw.LambdaIntegration(questionnairesFn), authedOpts);
    qsRes.addResource('from-excel').addMethod('POST', new apigw.LambdaIntegration(questionnairesFn), authedOpts);
    const qsId = qsRes.addResource('{id}');
    qsId.addMethod('GET', new apigw.LambdaIntegration(questionnairesFn), authedOpts);
    const qsItem = qsId.addResource('items').addResource('{iid}');
    qsItem.addMethod('PATCH', new apigw.LambdaIntegration(questionnairesFn), authedOpts);
    qsItem.addMethod('POST',  new apigw.LambdaIntegration(questionnairesFn), authedOpts);  // AI suggest

    findingsRes.addResource('summary').addMethod(
      'GET', new apigw.LambdaIntegration(findingsSummaryFn), authedOpts,
    );
    findingsRes.addResource('rollup').addMethod(
      'GET', new apigw.LambdaIntegration(findingsRollupFn), authedOpts,
    );
    findingsRes.addMethod(
      'GET', new apigw.LambdaIntegration(findingsListFn), authedOpts,
    );

    // ========================================================================
    // Phase B — Azure onboarding
    // ========================================================================

    const azureScriptUrl = `https://${props.cdnDistribution.distributionDomainName}/cfn/azure/onboard.sh`;

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
        AZURE_SCAN_TASK_DEF:    props.azureScanTaskDefFamily,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
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
    onboardingAzureCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.azureScanTaskDefFamily}:*`],
    }));
    onboardingAzureCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [`arn:aws:iam::${this.account}:role/CisoCopilotScan-AzureScanTaskDef*`],
    }));

    const onboardingAzure = onboarding.addResource('azure');
    onboardingAzure.addResource('initiate').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingAzureInitiateFn), authedOpts,
    );
    onboardingAzure.addResource('complete').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingAzureCompleteFn),
    );

    // ========================================================================
    // Phase C — Entra onboarding (admin-consent flow)
    // ========================================================================

    // Self-referential URL for the consent callback. Hardcoded API ID matches
    // the existing API Gateway; if we ever rotate it, update here.
    const entraCallbackUrl = `https://xoljryrb7i.execute-api.${this.region}.amazonaws.com/v1/onboarding/entra/callback`;

    const onboardingEntraInitiateFn = new lambda.Function(this, 'OnboardingEntraInitiateFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_entra_initiate')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        ...dbEnv,
        ENTRA_APP_ID:         props.entraAppId,
        ENTRA_CALLBACK_URL:   entraCallbackUrl,
        OUR_ACCOUNT_ID:       this.account,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingEntraInitiateFn);

    const onboardingEntraCallbackFn = new lambda.Function(this, 'OnboardingEntraCallbackFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_entra_callback')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...dbEnv,
        ENTRA_RUNNER_FN: props.shastaRunnerEntra.functionName,
        APP_DOMAIN:      `https://${props.cdnDistribution.distributionDomainName}`,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingEntraCallbackFn);
    props.shastaRunnerEntra.grantInvoke(onboardingEntraCallbackFn);

    const onboardingEntra = onboarding.addResource('entra');
    onboardingEntra.addResource('initiate').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingEntraInitiateFn), authedOpts,
    );
    // /callback is GET (Microsoft redirects user's browser here)
    onboardingEntra.addResource('callback').addMethod(
      'GET', new apigw.LambdaIntegration(onboardingEntraCallbackFn),
    );

    // ========================================================================
    // Phase D — GCP onboarding (Workload Identity Federation)
    // ========================================================================

    const gcpScriptUrl = `https://${props.cdnDistribution.distributionDomainName}/cfn/gcp/onboard.sh`;

    const onboardingGcpInitiateFn = new lambda.Function(this, 'OnboardingGcpInitiateFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_gcp_initiate')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        ...dbEnv,
        GCP_SCRIPT_URL: gcpScriptUrl,
        OUR_ACCOUNT_ID: this.account,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingGcpInitiateFn);

    const onboardingGcpCompleteFn = new lambda.Function(this, 'OnboardingGcpCompleteFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'onboarding_gcp_complete')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        ...dbEnv,
        GCP_SCAN_TASK_DEF:      props.gcpScanTaskDefFamily,
        SCAN_CLUSTER_ARN:       props.scanCluster.clusterArn,
        SCAN_SUBNET_IDS:        props.vpc.privateSubnets.map(s => s.subnetId).join(','),
        SCAN_SECURITY_GROUP_ID: props.scanTaskSecurityGroupId,
      },
    });
    props.dbCluster.grantDataApiAccess(onboardingGcpCompleteFn);
    onboardingGcpCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ecs:RunTask'],
      resources: [`arn:aws:ecs:${this.region}:${this.account}:task-definition/${props.gcpScanTaskDefFamily}:*`],
    }));
    onboardingGcpCompleteFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['iam:PassRole'],
      resources: [
        `arn:aws:iam::${this.account}:role/ciso-copilot-gcp-scanner`,
        `arn:aws:iam::${this.account}:role/CisoCopilotScan-GcpScanTaskDef*`,
      ],
    }));

    const onboardingGcp = onboarding.addResource('gcp');
    onboardingGcp.addResource('initiate').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingGcpInitiateFn), authedOpts,
    );
    onboardingGcp.addResource('complete').addMethod(
      'POST', new apigw.LambdaIntegration(onboardingGcpCompleteFn),
    );

    // ========================================================================
    // Phase E — Voice session (OpenAI Realtime ephemeral key mint)
    // ========================================================================

    const voiceSessionFn = new lambda.Function(this, 'VoiceSessionFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'voice_session')),
      timeout: cdk.Duration.seconds(15),
      environment: {
        ...dbEnv,
        OPENAI_SECRET_NAME: props.openaiApiKeySecret.secretName,
      },
    });
    props.dbCluster.grantDataApiAccess(voiceSessionFn);
    props.openaiApiKeySecret.grantRead(voiceSessionFn);

    api.root.addResource('voice').addResource('session').addMethod(
      'POST', new apigw.LambdaIntegration(voiceSessionFn), authedOpts,
    );

    // ========================================================================
    // /auth/discover-tenant — email → Cognito IdP routing (UNAUTHED, pre-login)
    // ========================================================================
    // Lazily creates per-tenant Microsoft IdPs in Cognito so we can federate
    // multi-tenant Microsoft (Cognito validates id_token issuer strictly, so
    // each customer's tenant needs its own IdP entry).

    const authDiscoverFn = new lambda.Function(this, 'AuthDiscoverFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'auth_discover')),
      timeout: cdk.Duration.seconds(10),
      environment: {
        USER_POOL_ID:                props.userPool.userPoolId,
        USER_POOL_CLIENT_ID:         props.userPoolClient.userPoolClientId,  // iOS
        WEB_POOL_CLIENT_ID:          props.webClient.userPoolClientId,
        COGNITO_DOMAIN:              props.cognitoDomain,
        MICROSOFT_CLIENT_ID:         props.entraAppId,
        MICROSOFT_CLIENT_SECRET_ARN: props.entraScannerSecret.secretArn,
        WEB_REDIRECT_URI:            props.webRedirectUri,
      },
    });
    props.entraScannerSecret.grantRead(authDiscoverFn);
    authDiscoverFn.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'cognito-idp:DescribeIdentityProvider',
        'cognito-idp:CreateIdentityProvider',
        'cognito-idp:DescribeUserPoolClient',
        'cognito-idp:UpdateUserPoolClient',
      ],
      resources: [props.userPool.userPoolArn],
    }));

    const authRes = api.root.addResource('auth');
    authRes.addResource('discover-tenant').addMethod(
      'POST', new apigw.LambdaIntegration(authDiscoverFn),
    );

    // /v1/ai/connections — GitHub App install + listing
    const aiRes      = api.root.addResource('ai');
    const aiConns    = aiRes.addResource('connections');
    const aiConnId   = aiConns.addResource('{id}');
    const aiGithub   = aiConns.addResource('github');

    aiConns.addMethod( 'GET',    new apigw.LambdaIntegration(aiGithubFn), authedOpts);
    aiConnId.addMethod('DELETE', new apigw.LambdaIntegration(aiGithubFn), authedOpts);
    aiConnId.addResource('repos').addMethod(
      'GET', new apigw.LambdaIntegration(aiGithubFn), authedOpts,
    );
    aiGithub.addResource('install_url').addMethod(
      'POST', new apigw.LambdaIntegration(aiGithubFn), authedOpts,
    );
    aiGithub.addResource('complete').addMethod(
      'POST', new apigw.LambdaIntegration(aiGithubFn), authedOpts,
    );

    // /v1/ai/scans (unchanged) + /v1/entities/* (new) — entities_api Lambda.
    // /v1/ai/assets and /v1/ai/assets/{id} retired (replaced by /v1/entities*).
    const aiScans   = aiRes.addResource('scans');
    const aiScanId  = aiScans.addResource('{id}');
    aiScans.addMethod( 'POST', new apigw.LambdaIntegration(entitiesApiFn), authedOpts);
    aiScans.addMethod( 'GET',  new apigw.LambdaIntegration(entitiesApiFn), authedOpts);
    aiScanId.addMethod('GET',  new apigw.LambdaIntegration(entitiesApiFn), authedOpts);

    // /v1/ai/summary — AI Visibility v2 Slice 1 rollup (counts + top sources/people)
    aiRes.addResource('summary').addMethod(
      'GET', new apigw.LambdaIntegration(aiSummaryFn), authedOpts,
    );

    const entities     = api.root.addResource('entities');
    const entityId     = entities.addResource('{id}');
    const entityGraph  = entityId.addResource('graph');
    const entityRels   = entityId.addResource('relationships');
    entities.addMethod(    'GET', new apigw.LambdaIntegration(entitiesApiFn), authedOpts);
    entityId.addMethod(    'GET', new apigw.LambdaIntegration(entitiesApiFn), authedOpts);
    entityGraph.addMethod( 'GET', new apigw.LambdaIntegration(entitiesApiFn), authedOpts);
    entityRels.addMethod(  'GET', new apigw.LambdaIntegration(entitiesApiFn), authedOpts);

    // ========================================================================
    // V2-8 — GET /v1/scans/{scan_id} — scan progress (tier/status/phase/scope)
    // ========================================================================
    const scansStatusFn = new lambda.Function(this, 'ScansStatusFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'scans_status')),
      timeout:    cdk.Duration.seconds(10),
      memorySize: 256,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(scansStatusFn);

    const scansRes   = api.root.addResource('scans');
    const scanById   = scansRes.addResource('{scan_id}');
    scanById.addMethod('GET', new apigw.LambdaIntegration(scansStatusFn), authedOpts);

    // ========================================================================
    // SP4 Phase 4a — chat_session (conversation CRUD + voice mint) + streaming
    //
    // Two Lambdas off the SAME code asset:
    //   ChatSessionFn — main.handler, on API Gateway REST, 7 routes.
    //   ChatStreamFn  — messages_stream.handler, Lambda Function URL with
    //                   RESPONSE_STREAM, serves only the streaming text turn.
    // Both share dbEnv + OpenAI/Anthropic secret access + USER_POOL_ID, so
    // they live here (not a separate stack) to avoid cross-stack imports.
    // ========================================================================
    const chatEnv = {
      ...dbEnv,
      OPENAI_SECRET_NAME:    props.openaiApiKeySecret.secretName,
      ANTHROPIC_SECRET_NAME: 'ciso-copilot/anthropic-api-key',
      USER_POOL_ID:          props.userPool.userPoolId,
    };
    // ChatSessionFn's code asset. The streaming-only files (app.py, run.sh,
    // messages_stream.py, tools_dispatch.py) live in the same source directory
    // but are NOT imported by main.handler — excluding them keeps
    // ChatSessionFn's asset hash (and thus its deployed artifact) unchanged
    // when the streaming rework adds/edits those files.
    const chatCodeAsset = lambda.Code.fromAsset(
      path.join(__dirname, '..', 'lambda', 'chat_session'),
      { exclude: [
        'app.py', 'run.sh', 'messages_stream.py', 'tools_dispatch.py',
        'requirements.txt',
        'tests', '__pycache__', '.pytest_cache', '*.pyc',
      ] },
    );
    const anthropicSecretRead = new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/anthropic-api-key*`],
    });

    const chatSessionFn = new lambda.Function(this, 'ChatSessionFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       chatCodeAsset,
      timeout:    cdk.Duration.seconds(30),
      memorySize: 512,
      environment: chatEnv,
    });
    props.dbCluster.grantDataApiAccess(chatSessionFn);
    props.openaiApiKeySecret.grantRead(chatSessionFn);
    chatSessionFn.addToRolePolicy(anthropicSecretRead);

    // /conversations — CRUD + per-id messages + voice mint.
    const conversationsRes = api.root.addResource('conversations');
    const conversationById = conversationsRes.addResource('{id}');
    conversationsRes.addMethod('POST', new apigw.LambdaIntegration(chatSessionFn), authedOpts);
    conversationsRes.addMethod('GET',  new apigw.LambdaIntegration(chatSessionFn), authedOpts);
    conversationById.addMethod('GET',    new apigw.LambdaIntegration(chatSessionFn), authedOpts);
    conversationById.addMethod('PATCH',  new apigw.LambdaIntegration(chatSessionFn), authedOpts);
    conversationById.addMethod('DELETE', new apigw.LambdaIntegration(chatSessionFn), authedOpts);
    const conversationMessages = conversationById.addResource('messages');
    conversationMessages.addMethod('POST', new apigw.LambdaIntegration(chatSessionFn), authedOpts);
    conversationMessages.addResource('{message_id}').addMethod(
      'PATCH', new apigw.LambdaIntegration(chatSessionFn), authedOpts,
    );
    conversationById.addResource('voice').addMethod(
      'POST', new apigw.LambdaIntegration(chatSessionFn), authedOpts,
    );

    // Streaming text turn — Lambda Function URL, RESPONSE_STREAM.
    //
    // Managed Python runtimes CANNOT stream a response (AWS-confirmed), so
    // this Lambda runs the Lambda Web Adapter (LWA): a Starlette ASGI app
    // (app.py) is served by uvicorn, and the LWA layer proxies the Function
    // URL request to it. With InvokeMode=RESPONSE_STREAM on the Function URL
    // and AWS_LWA_INVOKE_MODE=response_stream, a Starlette StreamingResponse
    // is flushed chunk-by-chunk — real token-by-token SSE.
    //
    // The code asset is bundled SEPARATELY from ChatSessionFn's chatCodeAsset
    // so adding starlette+uvicorn here does not change ChatSessionFn's asset
    // hash. ChatSessionFn keeps its plain (unbundled) chatCodeAsset.
    //
    // LWA layer ARN: official AWS layer, account 753240598075, x86_64.
    const lwaLayer = lambda.LayerVersion.fromLayerVersionArn(
      this, 'LwaLayer',
      `arn:aws:lambda:${this.region}:753240598075:layer:LambdaAdapterLayerX86:27`,
    );
    const chatStreamAsset = lambda.Code.fromAsset(
      path.join(__dirname, '..', 'lambda', 'chat_session'),
      {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          // Force linux/amd64 + manylinux x86_64 wheels. Without this, pip on
          // Apple-Silicon Macs installs the wrong-arch cryptography wheel
          // (pulled in by PyJWT[crypto]) and the Lambda fails JWT verification
          // with "_rust.abi3.so: cannot open shared object file". Same fix as
          // AiGithubFn above.
          platform: 'linux/amd64',
          command: [
            'bash', '-c',
            'pip install --no-cache-dir '
            + '--platform manylinux2014_x86_64 '
            + '--implementation cp '
            + '--python-version 3.12 '
            + '--only-binary=:all: '
            + '-r requirements.txt -t /asset-output && '
            + 'cp -au . /asset-output && '
            + 'chmod +x /asset-output/run.sh',
          ],
        },
      },
    );
    const chatStreamFn = new lambda.Function(this, 'ChatStreamFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      // LWA: the handler is the startup script that launches the web server.
      handler:    'run.sh',
      code:       chatStreamAsset,
      layers:     [lwaLayer],
      timeout:    cdk.Duration.seconds(60),   // Anthropic streaming is slower
      memorySize: 512,
      environment: {
        ...chatEnv,
        AWS_LAMBDA_EXEC_WRAPPER:  '/opt/bootstrap',
        AWS_LWA_INVOKE_MODE:      'response_stream',
        AWS_LWA_PORT:             '8080',
      },
    });
    props.dbCluster.grantDataApiAccess(chatStreamFn);
    chatStreamFn.addToRolePolicy(anthropicSecretRead);

    const chatStreamUrl = chatStreamFn.addFunctionUrl({
      authType:   lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
      cors: {
        allowedOrigins: ['*'],
        allowedMethods: [lambda.HttpMethod.POST],
        allowedHeaders: ['authorization', 'content-type'],
      },
    });

    new cdk.CfnOutput(this, 'ApiUrl',           { value: api.url });
    new cdk.CfnOutput(this, 'EntraCallbackUrl', { value: entraCallbackUrl });
    new cdk.CfnOutput(this, 'GcpScriptUrl',     { value: gcpScriptUrl });
    new cdk.CfnOutput(this, 'ChatStreamUrl',    { value: chatStreamUrl.url });
  }
}
