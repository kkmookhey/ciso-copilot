import * as cdk from 'aws-cdk-lib';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as sqsEventSource from 'aws-cdk-lib/aws-lambda-event-sources';
import { Construct } from 'constructs';
import * as path from 'path';

interface EventsStackProps extends cdk.StackProps {
  dbCluster: rds.DatabaseCluster;
}

/// Real-time event pipeline. Customer AWS accounts forward GuardDuty /
/// Inspector / Security Hub / CloudTrail / AWS Config events to our central
/// EventBridge bus (cross-account PutEvents — permissions added per-tenant
/// by the onboarding Lambda). A single router Lambda fan-targets every
/// event: normalize → write to Aurora → archive to S3 raw → evaluate push
/// rules → fire APNs for critical.
///
/// Firehose is deliberately omitted for Phase A — we add it when volume
/// requires buffering. Sub-100/s the Lambda + Aurora Data API path is fine.
export class EventsStack extends cdk.Stack {
  public readonly eventBus: events.EventBus;
  public readonly rawEventsBucket: s3.Bucket;
  public readonly routerFn: lambda.Function;
  public readonly enrichmentQueue: sqs.Queue;
  public readonly enrichmentDlq:   sqs.Queue;
  public readonly spendCapTable:   dynamodb.Table;

  constructor(scope: Construct, id: string, props: EventsStackProps) {
    super(scope, id, props);

    // ============================================================
    // Central event bus
    // ============================================================
    this.eventBus = new events.EventBus(this, 'CentralEventBus', {
      eventBusName: 'ciso-copilot-events',
    });

    // ============================================================
    // Raw event archive — every event, partitioned by date in S3
    // ============================================================
    this.rawEventsBucket = new s3.Bucket(this, 'RawEventsBucket', {
      bucketName:        `ciso-copilot-raw-events-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption:        s3.BucketEncryption.S3_MANAGED,
      enforceSSL:        true,
      removalPolicy:     cdk.RemovalPolicy.RETAIN,
      lifecycleRules: [
        {
          id:          'tier-down-aged-events',
          transitions: [
            { storageClass: s3.StorageClass.INFREQUENT_ACCESS,        transitionAfter: cdk.Duration.days(30)  },
            { storageClass: s3.StorageClass.GLACIER_INSTANT_RETRIEVAL, transitionAfter: cdk.Duration.days(90) },
          ],
        },
      ],
    });

    // ============================================================
    // Router Lambda — every event from the central bus lands here
    // ============================================================
    this.routerFn = new lambda.Function(this, 'EventRouter', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'event_router')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      environment: {
        DB_CLUSTER_ARN:    props.dbCluster.clusterArn,
        DB_SECRET_ARN:     props.dbCluster.secret!.secretArn,
        DB_NAME:           'ciso_copilot',
        RAW_EVENTS_BUCKET: this.rawEventsBucket.bucketName,
        APNS_KEY_SECRET_NAME:    'ciso-copilot/apns-key-p8',
        APNS_CONFIG_SECRET_NAME: 'ciso-copilot/apns-config',
      },
    });

    props.dbCluster.grantDataApiAccess(this.routerFn);
    this.rawEventsBucket.grantWrite(this.routerFn);
    this.routerFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/*`],
    }));

    this.routerFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['sns:CreatePlatformEndpoint', 'sns:Publish'],
      resources: ['*'],
    }));

    // APNs Platform Application ARN — provisioned once via CLI (requires .p8 contents that
    // cannot safely live in CDK source). The ARN is stable; it's safe to inline here as a
    // non-secret identifier.
    const APNS_PLATFORM_APP_ARN = 'arn:aws:sns:us-east-1:470226123496:app/APNS_SANDBOX/CISOCopilotAPNSSandbox';
    this.routerFn.addEnvironment('APNS_PLATFORM_APPLICATION_ARN', APNS_PLATFORM_APP_ARN);

    // ============================================================
    // SOC enrichment queue (DLQ + main) — router enqueues, soc_enrichment consumes
    // ============================================================
    this.enrichmentDlq = new sqs.Queue(this, 'SocEnrichmentDlq', {
      queueName:       'soc-enrichment-dlq',
      retentionPeriod: cdk.Duration.days(14),
    });
    this.enrichmentQueue = new sqs.Queue(this, 'SocEnrichmentQueue', {
      queueName:         'soc-enrichment-queue',
      visibilityTimeout: cdk.Duration.seconds(120),
      deadLetterQueue:   { queue: this.enrichmentDlq, maxReceiveCount: 3 },
    });

    // ============================================================
    // Per-tenant daily LLM spend counter (cents) — also used for push rate-limit (different sort-key prefix)
    // ============================================================
    this.spendCapTable = new dynamodb.Table(this, 'SocLlmSpendDaily', {
      tableName: 'soc_llm_spend_daily',
      partitionKey: { name: 'tenant_id', type: dynamodb.AttributeType.STRING },
      sortKey:      { name: 'day',       type: dynamodb.AttributeType.STRING },
      billingMode:  dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'expires_at',
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Grant router permission to enqueue + read/write spend counters
    this.enrichmentQueue.grantSendMessages(this.routerFn);
    this.routerFn.addEnvironment('ENRICHMENT_QUEUE_URL', this.enrichmentQueue.queueUrl);
    this.routerFn.addEnvironment('SPEND_CAP_TABLE_NAME', this.spendCapTable.tableName);
    this.spendCapTable.grantReadWriteData(this.routerFn);

    // ============================================================
    // SOC enrichment Lambda — consumes the queue
    // ============================================================
    const enrichmentFn = new lambda.Function(this, 'SocEnrichmentFn', {
      runtime:    lambda.Runtime.PYTHON_3_12,
      handler:    'main.handler',
      code:       lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'soc_enrichment', 'dist', 'soc_enrichment.zip')),
      timeout:    cdk.Duration.seconds(90),
      memorySize: 1024,
      environment: {
        DB_CLUSTER_ARN:            props.dbCluster.clusterArn,
        DB_SECRET_ARN:             props.dbCluster.secret!.secretArn,
        DB_NAME:                   'ciso_copilot',
        SPEND_CAP_TABLE_NAME:      this.spendCapTable.tableName,
        SOC_ENRICHMENT_LLM_MODEL:  'claude-sonnet-4-6',
        ANTHROPIC_API_KEY_SECRET_NAME: 'ciso-copilot/anthropic-api-key',
      },
    });
    props.dbCluster.grantDataApiAccess(enrichmentFn);
    this.spendCapTable.grantReadWriteData(enrichmentFn);
    enrichmentFn.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/anthropic-api-key*`],
    }));
    enrichmentFn.addEventSource(new sqsEventSource.SqsEventSource(this.enrichmentQueue, {
      batchSize: 5,
      maxBatchingWindow: cdk.Duration.seconds(2),
      reportBatchItemFailures: true,
    }));
    new cdk.CfnOutput(this, 'SocEnrichmentFnName', { value: enrichmentFn.functionName });

    // ============================================================
    // Fan every event from the central bus into the router Lambda
    // ============================================================
    new events.Rule(this, 'FanToRouter', {
      eventBus:    this.eventBus,
      ruleName:    'fan-all-to-router',
      description: 'Catch-all: every event on the central bus routes through the Lambda.',
      eventPattern: {
        // Pattern matches if the event has *any* account — i.e. everything.
        account: [{ 'exists': true } as any],
      },
      targets: [new targets.LambdaFunction(this.routerFn)],
    });

    new cdk.CfnOutput(this, 'EventBusArn',          { value: this.eventBus.eventBusArn });
    new cdk.CfnOutput(this, 'EventBusName',         { value: this.eventBus.eventBusName });
    new cdk.CfnOutput(this, 'RawEventsBucketName',  { value: this.rawEventsBucket.bucketName });
    new cdk.CfnOutput(this, 'EventRouterFnName',    { value: this.routerFn.functionName });
    new cdk.CfnOutput(this, 'EnrichmentQueueUrl',  { value: this.enrichmentQueue.queueUrl });
    new cdk.CfnOutput(this, 'EnrichmentDlqUrl',    { value: this.enrichmentDlq.queueUrl });
    new cdk.CfnOutput(this, 'SpendCapTableName',   { value: this.spendCapTable.tableName });
  }
}
