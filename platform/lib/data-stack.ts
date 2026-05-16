import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as kms from 'aws-cdk-lib/aws-kms';
import { Construct } from 'constructs';

interface DataStackProps extends cdk.StackProps {
  vpc: ec2.Vpc;
}

export class DataStack extends cdk.Stack {
  public readonly cluster: rds.DatabaseCluster;
  public readonly storageKey: kms.Key;

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
  }
}
