import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as lambda_event from 'aws-cdk-lib/aws-lambda-event-sources';
import { Construct } from 'constructs';
import { config } from './config';

interface ScanStackProps extends cdk.StackProps {
  vpc:                   ec2.IVpc;
  dbCluster:             rds.DatabaseCluster;
  shastaRunnerRepo:      ecr.Repository;
  shastaRunnerAzureRepo: ecr.Repository;
  shastaRunnerEntraRepo: ecr.Repository;
  shastaRunnerGcpRepo:   ecr.Repository;
  aiScannerRepo:         ecr.Repository;
  // Autonomous broadcast pipeline (Slice 2.4). All three props are optional
  // so the stack can be deployed independently before DataStack exports exist.
  autonomousBroadcastQueue?:     sqs.IQueue;
  autonomousBroadcastSeenTable?: dynamodb.ITable;
  connectorTokensKey?:           kms.IKey;
}

/// shasta-runner Lambda. Container image from ECR (built + pushed by
/// platform/lambda/shasta_runner/build.sh). Invoked by:
///   1. POST /scans/trigger (manual re-scan via connectionsListFn)
///   2. EventBridge cron (nightly scheduled scans — Phase A.5 later)
///
/// NOTE: the initial onboarding scan (triggered by onboarding_aws_complete)
/// now runs as the `ciso-copilot-aws-scan` ECS Fargate task defined later
/// in this stack, not this Lambda. (Task B4 cutover.)
///
/// Permissions:
///   - sts:AssumeRole on any arn:aws:iam::*:role/CISOCopilotReader (scoped
///     by external_id at runtime — we never assume roles we don't have a
///     connection record for)
///   - rds-data:* on the platform Aurora cluster (via Data API)
///   - secretsmanager:GetSecretValue on ciso-copilot/connections/* (in
///     case we move credential pickup into the Lambda later)
export class ScanStack extends cdk.Stack {
  public readonly shastaRunner:       lambda.DockerImageFunction;
  public readonly shastaRunnerEntra:  lambda.DockerImageFunction;
  public readonly entraScannerSecret: secretsmanager.Secret;
  public readonly openaiApiKeySecret: secretsmanager.Secret;
  public readonly aiScanQueue:        sqs.Queue;
  public readonly aiScanner:          lambda.DockerImageFunction;
  public readonly scanCluster:        ecs.Cluster;
  public readonly scanTaskDef:        ecs.FargateTaskDefinition;
  public readonly azureScanTaskDef:   ecs.FargateTaskDefinition;
  public readonly gcpScanTaskDef:     ecs.FargateTaskDefinition;
  public readonly scanTaskSecurityGroupId: string;
  public readonly azureScanTaskDefFamily: string;
  public readonly gcpScanTaskDefFamily:   string;

  constructor(scope: Construct, id: string, props: ScanStackProps) {
    super(scope, id, props);

    const dbEnv = {
      DB_CLUSTER_ARN: props.dbCluster.clusterArn,
      DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
      DB_NAME:        'ciso_copilot',
    };

    const secretsArn = `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/connections/*`;

    // ===== AWS scanner =====
    this.shastaRunner = new lambda.DockerImageFunction(this, 'ShastaRunner', {
      functionName: 'ciso-copilot-shasta-runner',
      code: lambda.DockerImageCode.fromEcr(props.shastaRunnerRepo, { tagOrDigest: 'latest' }),
      timeout:    cdk.Duration.minutes(15),
      memorySize: 2048,
      architecture: lambda.Architecture.X86_64,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(this.shastaRunner);
    this.shastaRunner.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['sts:AssumeRole'],
      resources: ['arn:aws:iam::*:role/CISOCopilotReader'],
    }));
    this.shastaRunner.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [secretsArn],
    }));

    // ===== AWS scanner — Fargate task =====
    // The uplifted scan (Medium/Deep tiers) exceeds Lambda's 15-min ceiling,
    // so the scanner also runs as a Fargate task. Same image, different
    // entrypoint: the task overrides entryPoint+command to `python run.py`,
    // which reads scan params from container env overrides set by RunTask.
    const scanCluster = new ecs.Cluster(this, 'ScanCluster', {
      clusterName: 'ciso-copilot-scan',
      vpc:         props.vpc,
    });

    const scanTaskDef = new ecs.FargateTaskDefinition(this, 'ScanTaskDef', {
      family:         'ciso-copilot-aws-scan',
      cpu:            4096,   // 4 vCPU — I/O-bound but ~16 worker threads each holding boto3 clients want the headroom
      memoryLimitMiB: 8192,
    });

    scanTaskDef.addContainer('scanner', {
      image: ecs.ContainerImage.fromEcrRepository(props.shastaRunnerRepo, 'latest'),
      // Override the Lambda base-image entrypoint — run the Fargate script.
      entryPoint: ['python'],
      command:    ['run.py'],
      environment: dbEnv,
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'aws-scan',
        logRetention: logs.RetentionDays.ONE_MONTH,
      }),
    });

    // Same permissions the scanner Lambda has: assume the customer reader
    // role, Aurora Data API, read connection secrets.
    props.dbCluster.grantDataApiAccess(scanTaskDef.taskRole);
    scanTaskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions:   ['sts:AssumeRole'],
      resources: ['arn:aws:iam::*:role/CISOCopilotReader'],
    }));
    scanTaskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [secretsArn],
    }));

    // Security group for the scanner task — egress only (no inbound).
    const scanTaskSg = new ec2.SecurityGroup(this, 'ScanTaskSg', {
      vpc:         props.vpc,
      description: 'AWS scanner Fargate task - egress only',
    });
    this.scanTaskSecurityGroupId = scanTaskSg.securityGroupId;

    this.scanCluster = scanCluster;
    this.scanTaskDef = scanTaskDef;

    new cdk.CfnOutput(this, 'ScanClusterArn',   { value: scanCluster.clusterArn });
    new cdk.CfnOutput(this, 'ScanTaskDefArn',   { value: scanTaskDef.taskDefinitionArn });
    new cdk.CfnOutput(this, 'ScanTaskSgId',     { value: scanTaskSg.securityGroupId });

    // ===== Azure scanner — Fargate task =====
    // Mirrors the AWS ScanTaskDef. Runs `python run.py` in the same Azure ECR
    // image that the Lambda above uses. Scan parameters arrive as RunTask
    // container overrides — not baked into the task def. No sts:AssumeRole
    // needed (Azure uses service-principal creds from Secrets Manager).
    const azureScanTaskDef = new ecs.FargateTaskDefinition(this, 'AzureScanTaskDef', {
      family:         'ciso-copilot-azure-scan',
      cpu:            4096,
      memoryLimitMiB: 8192,
    });

    azureScanTaskDef.addContainer('scanner', {
      image: ecs.ContainerImage.fromEcrRepository(props.shastaRunnerAzureRepo, 'latest'),
      entryPoint: ['python'],
      command:    ['run.py'],
      environment: dbEnv,
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'azure-scan',
        logRetention: logs.RetentionDays.ONE_MONTH,
      }),
    });

    props.dbCluster.grantDataApiAccess(azureScanTaskDef.taskRole);
    azureScanTaskDef.taskRole.addToPrincipalPolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [secretsArn],
    }));

    this.azureScanTaskDef        = azureScanTaskDef;
    this.azureScanTaskDefFamily  = 'ciso-copilot-azure-scan';

    new cdk.CfnOutput(this, 'AzureScanTaskDefArn',    { value: azureScanTaskDef.taskDefinitionArn });
    new cdk.CfnOutput(this, 'AzureScanTaskDefFamily', { value: 'ciso-copilot-azure-scan' });

    // ===== Entra scanner credentials =====
    // Shared across all customer Entra connections (our app reg's credentials).
    // Value is bootstrapped from .env at deploy time via unsafePlainText — fine
    // for dev. Rotate via the AWS console or secretsmanager:RotateSecret in prod.
    this.entraScannerSecret = new secretsmanager.Secret(this, 'EntraScannerCreds', {
      secretName: 'ciso-copilot/entra-scanner-creds',
      description: 'Microsoft Entra app credentials for Graph API client-credentials flow against customer tenants.',
      secretObjectValue: {
        client_id:     cdk.SecretValue.unsafePlainText(config.entraClientId),
        client_secret: cdk.SecretValue.unsafePlainText(config.entraClientSecret),
      },
    });

    // ===== OpenAI API key (Phase E voice) =====
    // Empty placeholder — populate post-deploy with:
    //   aws secretsmanager put-secret-value --secret-id ciso-copilot/openai-api-key \
    //     --secret-string '{"api_key":"sk-..."}'
    // /voice/session returns 503 with instructions until populated.
    this.openaiApiKeySecret = new secretsmanager.Secret(this, 'OpenAiApiKey', {
      secretName: 'ciso-copilot/openai-api-key',
      description: 'OpenAI API key for Realtime voice + future LLM features.',
      generateSecretString: {
        secretStringTemplate: '{"api_key": ""}',
        generateStringKey:    'placeholder',
      },
    });

    // ===== Entra scanner Lambda =====
    this.shastaRunnerEntra = new lambda.DockerImageFunction(this, 'EntraRunner', {
      functionName: 'ciso-copilot-shasta-runner-entra',
      code: lambda.DockerImageCode.fromEcr(props.shastaRunnerEntraRepo, { tagOrDigest: 'latest' }),
      timeout:    cdk.Duration.minutes(15),
      memorySize: 2048,
      architecture: lambda.Architecture.X86_64,
      environment: {
        ...dbEnv,
        ENTRA_SCANNER_SECRET_NAME: this.entraScannerSecret.secretName,
        APNS_PLATFORM_APP_ARN:     process.env.APNS_PLATFORM_APP_ARN ?? '',
      },
    });
    props.dbCluster.grantDataApiAccess(this.shastaRunnerEntra);
    this.entraScannerSecret.grantRead(this.shastaRunnerEntra);
    // APNs push on new personal-tier findings (Task 15).
    this.shastaRunnerEntra.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['sns:CreatePlatformEndpoint', 'sns:Publish'],
      resources: ['*'],
    }));

    // ===== GCP scanner =====
    // Shared by the legacy GCP Lambda AND the new Fargate task. The
    // customer's WIF provider (cfn/gcp/onboard.sh) trusts the AWS role
    // named 'ciso-copilot-gcp-scanner' — the assumed-role identity of
    // whatever runs the scan must carry that name, so this single role
    // is used as both the Lambda role and the Fargate task role. The
    // trust policy admits both service principals.
    const gcpScannerRole = new iam.Role(this, 'GcpScannerRole', {
      roleName: 'ciso-copilot-gcp-scanner',
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('lambda.amazonaws.com'),
        new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      ),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    // The v2 GCP scanner runs as the Fargate task defined below; the
    // legacy GcpRunner Lambda has been retired (Slice 1b). gcpScannerRole
    // is still the GCP scan identity (the Fargate task's taskRole), so
    // grant Aurora Data API access directly on the role.
    props.dbCluster.grantDataApiAccess(gcpScannerRole);

    // ===== GCP scanner — v2 Fargate task =====
    // The customer WIF provider trusts the 'ciso-copilot-gcp-scanner'
    // role; gcpScannerRole IS that role, used here as the task role so
    // google-auth's GetCallerIdentity reflects the trusted name.
    const gcpScanTaskDef = new ecs.FargateTaskDefinition(this, 'GcpScanTaskDef', {
      family:         'ciso-copilot-gcp-scan',
      cpu:            4096,
      memoryLimitMiB: 8192,
      taskRole:       gcpScannerRole,
    });

    gcpScanTaskDef.addContainer('scanner', {
      image: ecs.ContainerImage.fromEcrRepository(props.shastaRunnerGcpRepo, 'latest'),
      entryPoint: ['python'],
      command:    ['run.py'],
      environment: dbEnv,
      logging: ecs.LogDriver.awsLogs({
        streamPrefix: 'gcp-scan',
        logRetention: logs.RetentionDays.ONE_MONTH,
      }),
    });

    // gcpScannerRole has Aurora Data API access granted directly above.
    // The WIF GetCallerIdentity call requires no IAM policy (a principal
    // may always describe itself).

    this.gcpScanTaskDef       = gcpScanTaskDef;
    this.gcpScanTaskDefFamily = 'ciso-copilot-gcp-scan';

    new cdk.CfnOutput(this, 'GcpScanTaskDefArn',    { value: gcpScanTaskDef.taskDefinitionArn });
    new cdk.CfnOutput(this, 'GcpScanTaskDefFamily', { value: 'ciso-copilot-gcp-scan' });

    // ========================================================================
    // ai-scan-queue — SQS work queue for the AI scanner Lambda
    // ========================================================================
    const aiScanDlq = new sqs.Queue(this, 'AiScanDlq', {
      queueName:        'ai-scan-dlq',
      retentionPeriod:  cdk.Duration.days(14),
    });

    this.aiScanQueue = new sqs.Queue(this, 'AiScanQueue', {
      queueName:               'ai-scan-queue',
      visibilityTimeout:       cdk.Duration.seconds(720),  // > Lambda timeout (600s)
      retentionPeriod:         cdk.Duration.days(4),
      deadLetterQueue: {
        queue:           aiScanDlq,
        maxReceiveCount: 3,
      },
    });

    // ========================================================================
    // ai_scanner Lambda — container image, consumes ai-scan-queue, clones a
    // customer's GitHub repo via the installation token, runs the 8 detectors
    // + correlator, writes assets/relationships/findings.
    // ========================================================================
    this.aiScanner = new lambda.DockerImageFunction(this, 'AiScanner', {
      functionName:         'ciso-copilot-ai-scanner',
      code:                 lambda.DockerImageCode.fromEcr(props.aiScannerRepo, { tagOrDigest: 'latest' }),
      timeout:              cdk.Duration.seconds(600),
      memorySize:           2048,
      ephemeralStorageSize: cdk.Size.gibibytes(4),
      architecture:         lambda.Architecture.X86_64,
      environment: {
        ...dbEnv,
        GITHUB_APP_SECRET_ARN: `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials`,
        SCANNER_VERSION:       '0.1.0',
      },
    });
    props.dbCluster.grantDataApiAccess(this.aiScanner);
    this.aiScanner.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/github-app/credentials*`,
      ],
    }));

    // Wire SQS as the event source. batchSize:1 because each repo scan is a
    // long-running unit; maxConcurrency:5 caps blast radius if many scans
    // are triggered in a burst.
    this.aiScanner.addEventSource(new lambda_event.SqsEventSource(this.aiScanQueue, {
      batchSize:      1,
      maxConcurrency: 5,
    }));

    // ========================================================================
    // ai-supply-chain-matcher — SQS queue + Lambda
    //
    // Triggered after ai_scanner commits sca_vuln findings. Joins CVEs with
    // the ai_framework→ai_agent edge graph and the KEV threat_indicators table.
    // When both conditions hold (KEV-listed AND actively imported), emits an
    // ai_supply_chain_active finding at CRITICAL severity and fires an APNs push.
    //
    // PREREQUISITE: run `./build.sh` in lambda/ai_supply_chain_matcher/ before
    // `cdk deploy`. build.sh vendors _shared/push.py into dist/matcher.zip.
    // ========================================================================
    const matcherDlq = new sqs.Queue(this, 'AiSupplyChainMatcherDlq', {
      queueName:       'ai-supply-chain-matcher-dlq',
      retentionPeriod: cdk.Duration.days(14),
    });

    const matcherQueue = new sqs.Queue(this, 'AiSupplyChainMatcherQueue', {
      queueName:        'ai-supply-chain-matcher-queue',
      visibilityTimeout: cdk.Duration.seconds(60),
      retentionPeriod:  cdk.Duration.days(4),
      deadLetterQueue: {
        queue:           matcherDlq,
        maxReceiveCount: 3,
      },
    });

    const matcherFn = new lambda.Function(this, 'AiSupplyChainMatcherFn', {
      functionName: 'ciso-copilot-ai-supply-chain-matcher',
      runtime:      lambda.Runtime.PYTHON_3_12,
      handler:      'main.handler',
      // Zip is built by lambda/ai_supply_chain_matcher/build.sh into dist/.
      // Point CDK at the zip so the asset hash stays stable across deploys.
      code:         lambda.Code.fromAsset(
                      path.join(__dirname, '..', 'lambda', 'ai_supply_chain_matcher', 'dist', 'matcher.zip')
                    ),
      timeout:      cdk.Duration.seconds(30),
      memorySize:   512,
      environment: {
        ...dbEnv,
        APNS_PLATFORM_APP_ARN: process.env.APNS_PLATFORM_APP_ARN ?? '',
      },
    });
    // Consume the matcher queue (one message per scan, sequential — no blast radius concern).
    matcherFn.addEventSource(new lambda_event.SqsEventSource(matcherQueue, {
      batchSize: 1,
    }));
    props.dbCluster.grantDataApiAccess(matcherFn);
    // SNS: create platform endpoint + publish for APNs push.
    matcherFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['sns:CreatePlatformEndpoint', 'sns:Publish'],
      resources: ['*'],
    }));

    // Grant the AI scanner permission to send messages and inject the queue URL.
    matcherQueue.grantSendMessages(this.aiScanner);
    this.aiScanner.addEnvironment('AI_SUPPLY_CHAIN_MATCHER_QUEUE_URL', matcherQueue.queueUrl);

    // ── Autonomous broadcast pipeline (Slice 2.4) ──────────────────────────
    // Grant all scanner runtimes sqs:SendMessage on the broadcast queue.
    // The broadcast_fanout module short-circuits when AUTONOMOUS_BROADCAST_QUEUE_URL
    // is absent, so gating on the optional prop is safe — stack deploys before
    // the queue exists are unaffected.
    //
    // Lambda scanners: env var injected here via addEnvironment.
    // ECS Fargate tasks (azure, gcp): IAM grant is here; AUTONOMOUS_BROADCAST_QUEUE_URL
    // is passed as a RunTask container override at scan-dispatch time (same
    // pattern as CONN_ID and other per-scan params). CDK ContainerDefinition
    // does not support post-construction env mutation.
    if (props.autonomousBroadcastQueue) {
      const q = props.autonomousBroadcastQueue;

      // Lambda scanners — env var + IAM grant
      for (const fn of [this.shastaRunner, this.shastaRunnerEntra, this.aiScanner]) {
        fn.addEnvironment('AUTONOMOUS_BROADCAST_QUEUE_URL', q.queueUrl);
        q.grantSendMessages(fn);
      }

      // ECS task roles — IAM grant only; env var supplied via RunTask overrides
      q.grantSendMessages(azureScanTaskDef.taskRole);
      q.grantSendMessages(gcpScannerRole);
    }

    // findings_subscriber Lambda — SQS-triggered, batch=1. Consumes the
    // autonomous broadcast queue, deduplicates via the seen DDB table, and
    // posts a Slack alert for critical findings.
    //
    // Placed in ScanStack (not ApiStack) to stay below ApiStack's 500-resource
    // CFN limit. All required resources (queue, seen-table, KMS key) are props.
    //
    // The placeholder main.py / requirements.txt under lambda/findings_subscriber/
    // let cdk synth succeed now; Task 14 replaces them with the real implementation.
    if (props.autonomousBroadcastQueue && props.autonomousBroadcastSeenTable && props.connectorTokensKey) {
      const findingsSubscriberFn = new lambda.Function(this, 'FindingsSubscriberFn', {
        runtime:    lambda.Runtime.PYTHON_3_12,
        handler:    'main.handler',
        code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda'), {
          bundling: {
            image:    lambda.Runtime.PYTHON_3_12.bundlingImage,
            platform: 'linux/amd64',
            command: [
              'bash', '-c',
              'pip install --no-cache-dir ' +
              '--platform manylinux2014_x86_64 --implementation cp ' +
              '--python-version 3.12 --only-binary=:all: ' +
              '-r findings_subscriber/requirements.txt -t /asset-output && ' +
              'cp -r findings_subscriber/. /asset-output/ && ' +
              'cp -r _shared/mcp_oauth /asset-output/',
            ],
          },
        }),
        timeout:    cdk.Duration.seconds(30),
        memorySize: 256,
        environment: {
          ...dbEnv,
          CONNECTOR_TOKENS_KEY_ARN:        props.connectorTokensKey.keyArn,
          AUTONOMOUS_BROADCAST_SEEN_TABLE: props.autonomousBroadcastSeenTable.tableName,
          AUTONOMOUS_RULE_SSM_PARAM:       '/cisocopilot/autonomous_rule/enabled',
          WEB_BASE_URL:                    config.appDomain,
        },
      });
      findingsSubscriberFn.addEventSource(new lambda_event.SqsEventSource(
        props.autonomousBroadcastQueue, { batchSize: 1 },
      ));
      props.dbCluster.grantDataApiAccess(findingsSubscriberFn);
      props.connectorTokensKey.grantEncryptDecrypt(findingsSubscriberFn);
      props.autonomousBroadcastSeenTable.grantReadWriteData(findingsSubscriberFn);
      // SSM kill switch + Slack OAuth client creds (for token refresh path).
      findingsSubscriberFn.addToRolePolicy(new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [
          `arn:aws:ssm:${this.region}:${this.account}:parameter/cisocopilot/autonomous_rule/enabled`,
          `arn:aws:ssm:${this.region}:${this.account}:parameter/cisocopilot/connectors/slack/client-id`,
          `arn:aws:ssm:${this.region}:${this.account}:parameter/cisocopilot/connectors/slack/client-secret`,
        ],
      }));
      findingsSubscriberFn.addToRolePolicy(new iam.PolicyStatement({
        actions:   ['kms:Decrypt'],
        resources: [`arn:aws:kms:${this.region}:${this.account}:alias/aws/ssm`],
      }));

      new cdk.CfnOutput(this, 'FindingsSubscriberFnName', { value: findingsSubscriberFn.functionName });
    }

    new cdk.CfnOutput(this, 'AiSupplyChainMatcherQueueUrl', { value: matcherQueue.queueUrl });
    new cdk.CfnOutput(this, 'AiSupplyChainMatcherFnName',   { value: matcherFn.functionName });

    new cdk.CfnOutput(this, 'AiScanQueueUrl',  { value: this.aiScanQueue.queueUrl });
    new cdk.CfnOutput(this, 'AiScannerFnName', { value: this.aiScanner.functionName });

    new cdk.CfnOutput(this, 'ShastaRunnerArn',         { value: this.shastaRunner.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerFnName',      { value: this.shastaRunner.functionName });
    new cdk.CfnOutput(this, 'ShastaRunnerEntraArn',    { value: this.shastaRunnerEntra.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerEntraFnName', { value: this.shastaRunnerEntra.functionName });
    new cdk.CfnOutput(this, 'ShastaRunnerGcpRoleArn',  { value: gcpScannerRole.roleArn });
  }
}
