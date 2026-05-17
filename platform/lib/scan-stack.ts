import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
import { Construct } from 'constructs';

interface ScanStackProps extends cdk.StackProps {
  dbCluster:           rds.DatabaseCluster;
  shastaRunnerRepo:    ecr.Repository;
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
  public readonly shastaRunner: lambda.DockerImageFunction;

  constructor(scope: Construct, id: string, props: ScanStackProps) {
    super(scope, id, props);

    this.shastaRunner = new lambda.DockerImageFunction(this, 'ShastaRunner', {
      functionName: 'ciso-copilot-shasta-runner',
      code: lambda.DockerImageCode.fromEcr(props.shastaRunnerRepo, {
        tagOrDigest: 'latest',
      }),
      timeout:    cdk.Duration.minutes(15),    // Lambda's max
      memorySize: 2048,                        // Shasta scans many resources
      architecture: lambda.Architecture.X86_64,
      environment: {
        DB_CLUSTER_ARN: props.dbCluster.clusterArn,
        DB_SECRET_ARN:  props.dbCluster.secret!.secretArn,
        DB_NAME:        'ciso_copilot',
      },
    });

    props.dbCluster.grantDataApiAccess(this.shastaRunner);

    // STS AssumeRole on customer roles named CISOCopilotReader.
    this.shastaRunner.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['sts:AssumeRole'],
      resources: ['arn:aws:iam::*:role/CISOCopilotReader'],
    }));

    // Read connection-credentials secrets (currently only used by the
    // onboarding/complete flow; the runner gets role+external from the
    // invocation payload, but reserving the permission for future use).
    this.shastaRunner.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/connections/*`],
    }));

    new cdk.CfnOutput(this, 'ShastaRunnerArn',      { value: this.shastaRunner.functionArn });
    new cdk.CfnOutput(this, 'ShastaRunnerFnName',   { value: this.shastaRunner.functionName });
  }
}
