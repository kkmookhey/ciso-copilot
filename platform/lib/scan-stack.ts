import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambda_event from 'aws-cdk-lib/aws-lambda-event-sources';
import { Construct } from 'constructs';
import { config } from './config';

interface ScanStackProps extends cdk.StackProps {
  dbCluster:             rds.DatabaseCluster;
  shastaRunnerRepo:      ecr.Repository;
  shastaRunnerAzureRepo: ecr.Repository;
  shastaRunnerEntraRepo: ecr.Repository;
  shastaRunnerGcpRepo:   ecr.Repository;
  aiScannerRepo:         ecr.Repository;
}

/// shasta-runner Lambda. Container image from ECR (built + pushed by
/// platform/lambda/shasta_runner/build.sh). Invoked by:
///   1. onboarding_aws_complete (for the initial scan when a customer
///      finishes connecting their AWS account)
///   2. POST /scans/trigger (manual re-scan)
///   3. EventBridge cron (nightly scheduled scans — Phase A.5 later)
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
  public readonly shastaRunnerAzure:  lambda.DockerImageFunction;
  public readonly shastaRunnerEntra:  lambda.DockerImageFunction;
  public readonly shastaRunnerGcp:    lambda.DockerImageFunction;
  public readonly entraScannerSecret: secretsmanager.Secret;
  public readonly openaiApiKeySecret: secretsmanager.Secret;
  public readonly aiScanQueue:        sqs.Queue;
  public readonly aiScanner:          lambda.DockerImageFunction;

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

    // ===== Azure scanner =====
    // No cross-cloud assume-role needed (Azure SDK uses SP credentials directly
    // from env vars, which we inject from Secrets Manager at invoke time).
    this.shastaRunnerAzure = new lambda.DockerImageFunction(this, 'AzureRunner', {
      functionName: 'ciso-copilot-shasta-runner-azure',
      code: lambda.DockerImageCode.fromEcr(props.shastaRunnerAzureRepo, { tagOrDigest: 'latest' }),
      timeout:    cdk.Duration.minutes(15),
      memorySize: 2048,
      architecture: lambda.Architecture.X86_64,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(this.shastaRunnerAzure);
    this.shastaRunnerAzure.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [secretsArn],
    }));

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
      },
    });
    props.dbCluster.grantDataApiAccess(this.shastaRunnerEntra);
    this.entraScannerSecret.grantRead(this.shastaRunnerEntra);

    // ===== GCP scanner =====
    // Pre-create the IAM role with a fixed name so customer's WIF binding
    // can stably reference 'arn:aws:sts::470226123496:assumed-role/ciso-copilot-gcp-scanner'.
    // Without a fixed name, CDK appends a random suffix and the binding breaks
    // every time the stack is replaced.
    const gcpScannerRole = new iam.Role(this, 'GcpScannerRole', {
      roleName: 'ciso-copilot-gcp-scanner',
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    this.shastaRunnerGcp = new lambda.DockerImageFunction(this, 'GcpRunner', {
      functionName: 'ciso-copilot-shasta-runner-gcp',
      role:         gcpScannerRole,
      code: lambda.DockerImageCode.fromEcr(props.shastaRunnerGcpRepo, { tagOrDigest: 'latest' }),
      timeout:    cdk.Duration.minutes(15),
      memorySize: 2048,
      architecture: lambda.Architecture.X86_64,
      environment: dbEnv,
    });
    props.dbCluster.grantDataApiAccess(this.shastaRunnerGcp);

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

    new cdk.CfnOutput(this, 'AiScanQueueUrl',  { value: this.aiScanQueue.queueUrl });
    new cdk.CfnOutput(this, 'AiScannerFnName', { value: this.aiScanner.functionName });

    new cdk.CfnOutput(this, 'ShastaRunnerArn',         { value: this.shastaRunner.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerFnName',      { value: this.shastaRunner.functionName });
    new cdk.CfnOutput(this, 'ShastaRunnerAzureArn',    { value: this.shastaRunnerAzure.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerAzureFnName', { value: this.shastaRunnerAzure.functionName });
    new cdk.CfnOutput(this, 'ShastaRunnerEntraArn',    { value: this.shastaRunnerEntra.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerEntraFnName', { value: this.shastaRunnerEntra.functionName });
    new cdk.CfnOutput(this, 'ShastaRunnerGcpArn',      { value: this.shastaRunnerGcp.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerGcpFnName',   { value: this.shastaRunnerGcp.functionName });
    new cdk.CfnOutput(this, 'ShastaRunnerGcpRoleArn',  { value: gcpScannerRole.roleArn });
  }
}
