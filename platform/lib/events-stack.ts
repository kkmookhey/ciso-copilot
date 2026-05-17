import * as cdk from 'aws-cdk-lib';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
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
  }
}
