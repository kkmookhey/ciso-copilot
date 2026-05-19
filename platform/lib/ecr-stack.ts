import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import { Construct } from 'constructs';

/// ECR repos for our container images. shasta-runner is the Lambda container
/// that bundles Shasta's scanner library; Fargate uses the same image for
/// long-running scans that exceed Lambda's 15-min cap.
export class EcrStack extends cdk.Stack {
  public readonly shastaRunner:      ecr.Repository;
  public readonly shastaRunnerAzure: ecr.Repository;
  public readonly shastaRunnerEntra: ecr.Repository;
  public readonly shastaRunnerGcp:   ecr.Repository;
  public readonly aiScanner:         ecr.Repository;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const repo = (id: string, name: string) =>
      new ecr.Repository(this, id, {
        repositoryName: name,
        imageScanOnPush: true,
        lifecycleRules: [{ maxImageCount: 10 }],
        removalPolicy: cdk.RemovalPolicy.RETAIN,
        emptyOnDelete: false,
      });

    this.shastaRunner      = repo('ShastaRunner',      'shasta-runner');
    this.shastaRunnerAzure = repo('ShastaRunnerAzure', 'shasta-runner-azure');
    this.shastaRunnerEntra = repo('ShastaRunnerEntra', 'shasta-runner-entra');
    this.shastaRunnerGcp   = repo('ShastaRunnerGcp',   'shasta-runner-gcp');
    this.aiScanner         = repo('AiScanner',         'ai-scanner');

    new cdk.CfnOutput(this, 'ShastaRunnerRepoUri',      { value: this.shastaRunner.repositoryUri });
    new cdk.CfnOutput(this, 'ShastaRunnerAzureRepoUri', { value: this.shastaRunnerAzure.repositoryUri });
    new cdk.CfnOutput(this, 'ShastaRunnerEntraRepoUri', { value: this.shastaRunnerEntra.repositoryUri });
    new cdk.CfnOutput(this, 'ShastaRunnerGcpRepoUri',   { value: this.shastaRunnerGcp.repositoryUri });
    new cdk.CfnOutput(this, 'AiScannerRepoUri',        { value: this.aiScanner.repositoryUri });
  }
}
