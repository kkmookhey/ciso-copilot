import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';

/// ECR repos for our container images. shasta-runner is the Lambda container
/// that bundles Shasta's scanner library; Fargate uses the same image for
/// long-running scans that exceed Lambda's 15-min cap.
export class EcrStack extends cdk.Stack {
  public readonly shastaRunner: ecr.Repository;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.shastaRunner = new ecr.Repository(this, 'ShastaRunner', {
      repositoryName: 'shasta-runner',
      imageScanOnPush: true,
      lifecycleRules: [
        { maxImageCount: 10, description: 'keep last 10 images' },
      ],
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      emptyOnDelete: false,
    });

    new cdk.CfnOutput(this, 'ShastaRunnerRepoUri', {
      value: this.shastaRunner.repositoryUri,
    });
  }
}
