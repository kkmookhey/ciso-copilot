import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
import { Construct } from 'constructs';

interface ScanStackProps extends cdk.StackProps {
  dbCluster:             rds.DatabaseCluster;
  shastaRunnerRepo:      ecr.Repository;
  shastaRunnerAzureRepo: ecr.Repository;
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
  public readonly shastaRunner:      lambda.DockerImageFunction;
  public readonly shastaRunnerAzure: lambda.DockerImageFunction;

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

    new cdk.CfnOutput(this, 'ShastaRunnerArn',         { value: this.shastaRunner.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerFnName',      { value: this.shastaRunner.functionName });
    new cdk.CfnOutput(this, 'ShastaRunnerAzureArn',    { value: this.shastaRunnerAzure.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerAzureFnName', { value: this.shastaRunnerAzure.functionName });
  }
}
