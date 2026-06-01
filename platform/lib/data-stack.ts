import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import { Construct } from 'constructs';

interface DataStackProps extends cdk.StackProps {
  vpc: ec2.Vpc;
}

export class DataStack extends cdk.Stack {
  public readonly cluster: rds.DatabaseCluster;
  public readonly storageKey: kms.Key;
  public readonly connectorTokensKey: kms.Key;
  public readonly pkceVerifierTable: dynamodb.Table;
  // Autonomous broadcast pipeline (Slice 2.4)
  public readonly autonomousBroadcastQueue: sqs.Queue;
  public readonly autonomousBroadcastDlq: sqs.Queue;
  public readonly autonomousBroadcastSeenTable: dynamodb.Table;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);

    this.storageKey = new kms.Key(this, 'StorageKey', {
      description: 'CISO Copilot platform DB encryption key',
      enableKeyRotation: true,
    });

    this.cluster = new rds.DatabaseCluster(this, 'AuroraPg', {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_4,
      }),
      vpc: props.vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      writer: rds.ClusterInstance.serverlessV2('writer'),
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: 4,
      defaultDatabaseName: 'ciso_copilot',
      storageEncryptionKey: this.storageKey,
      enableDataApi: true,
      backup: { retention: cdk.Duration.days(30) },
      removalPolicy: cdk.RemovalPolicy.SNAPSHOT,
    });

    this.connectorTokensKey = new kms.Key(this, 'ConnectorTokensKey', {
      alias: 'cisocopilot-connector-tokens',
      description: 'Envelope key for MCP connector OAuth tokens (per-row pgcrypto)',
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.pkceVerifierTable = new dynamodb.Table(this, 'PkceVerifierTable', {
      tableName: 'cisocopilot-pkce-verifiers',
      partitionKey: { name: 'nonce', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ── Autonomous broadcast pipeline (Slice 2.4) ──────────────────────────
    // DLQ retains messages for 14 days so failed broadcasts can be replayed.
    this.autonomousBroadcastDlq = new sqs.Queue(this, 'AutonomousBroadcastDlq', {
      retentionPeriod: cdk.Duration.days(14),
    });

    // Main queue: short visibility timeout (Lambda finishes in <30s) and a
    // 4-day retention so a cold weekend doesn't silently drop messages.
    this.autonomousBroadcastQueue = new sqs.Queue(this, 'AutonomousBroadcastQueue', {
      visibilityTimeout: cdk.Duration.seconds(30),
      retentionPeriod: cdk.Duration.days(4),
      deadLetterQueue: {
        queue: this.autonomousBroadcastDlq,
        maxReceiveCount: 5,
      },
    });

    // Per-finding deduplication table — TTL-based expiry via ttl_epoch so
    // back-to-back scans within the window don't re-broadcast the same finding.
    this.autonomousBroadcastSeenTable = new dynamodb.Table(this, 'AutonomousBroadcastSeen', {
      partitionKey: { name: 'seen_key', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl_epoch',
    });

    // Alarm fires if any message lands in the DLQ for 5 consecutive 1-minute
    // periods — meaning the subscriber failed all 5 retries.
    new cloudwatch.Alarm(this, 'AutonomousBroadcastDlqAlarm', {
      metric: this.autonomousBroadcastDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.minutes(1),
      }),
      threshold: 1,
      evaluationPeriods: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      alarmDescription: 'Autonomous broadcast DLQ has messages — subscriber failed 5x',
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
  }
}
