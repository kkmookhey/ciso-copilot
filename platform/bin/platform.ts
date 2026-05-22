#!/usr/bin/env node
import 'dotenv/config';
import * as cdk from 'aws-cdk-lib';
import { config } from '../lib/config';
import { NetworkStack } from '../lib/network-stack';
import { DataStack } from '../lib/data-stack';
import { AuthStack } from '../lib/auth-stack';
import { EcrStack } from '../lib/ecr-stack';
import { StaticStack } from '../lib/static-stack';
import { EventsStack } from '../lib/events-stack';
import { ScanStack } from '../lib/scan-stack';
import { ApiStack } from '../lib/api-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region:  config.awsRegion,
};

// Foundations
const network = new NetworkStack(app, 'CisoCopilotNetwork', { env });
const data    = new DataStack(app, 'CisoCopilotData', { env, vpc: network.vpc });
const auth    = new AuthStack(app, 'CisoCopilotAuth', { env, dbCluster: data.cluster });

// Phase A infra (API depends on these)
const ecrStack    = new EcrStack(app, 'CisoCopilotEcr', { env });
const staticStack = new StaticStack(app, 'CisoCopilotStatic', { env });
const eventsStack = new EventsStack(app, 'CisoCopilotEvents', { env, dbCluster: data.cluster });
const scanStack   = new ScanStack(app, 'CisoCopilotScan', {
  env,
  vpc:                   network.vpc,
  dbCluster:             data.cluster,
  shastaRunnerRepo:      ecrStack.shastaRunner,
  shastaRunnerAzureRepo: ecrStack.shastaRunnerAzure,
  shastaRunnerEntraRepo: ecrStack.shastaRunnerEntra,
  shastaRunnerGcpRepo:   ecrStack.shastaRunnerGcp,
  aiScannerRepo:         ecrStack.aiScanner,
});

// API last — references events bus + CDN domain + shasta-runner Lambdas
new ApiStack(app, 'CisoCopilotApi', {
  env,
  userPool:           auth.userPool,
  userPoolClient:     auth.userPoolClient,
  webClient:          auth.webClient,
  dbCluster:          data.cluster,
  eventBus:           eventsStack.eventBus,
  cdnDistribution:    staticStack.cdnDistribution,
  shastaRunnerEntra:  scanStack.shastaRunnerEntra,
  shastaRunnerGcp:    scanStack.shastaRunnerGcp,
  scanCluster:                 scanStack.scanCluster,
  // Use plain strings for the task-def family + role ARNs to avoid a
  // cross-stack CloudFormation export on the revision ARN (which changes on
  // every task-def update and can't be updated while the ApiStack imports it).
  // Family name is hardcoded (matches ScanStack's `family:` field).
  // Role ARNs are read from the ScanStack properties — IAM role ARNs are
  // stable (don't change on task-def revision) and their exports already exist
  // in the deployed stack.
  scanTaskDefFamily:           'ciso-copilot-aws-scan',
  scanTaskDefTaskRoleArn:      scanStack.scanTaskDef.taskRole.roleArn,
  scanTaskDefExecutionRoleArn: scanStack.scanTaskDef.executionRole!.roleArn,
  azureScanTaskDefFamily:           'ciso-copilot-azure-scan',
  vpc:                     network.vpc,
  scanTaskSecurityGroupId: scanStack.scanTaskSecurityGroupId,
  entraAppId:         config.entraClientId,
  entraScannerSecret: scanStack.entraScannerSecret,
  openaiApiKeySecret: scanStack.openaiApiKeySecret,
  aiScanQueue:        scanStack.aiScanQueue,
  cognitoDomain:      `ciso-copilot.auth.${config.awsRegion}.amazoncognito.com`,
  webRedirectUri:     'https://shasta.transilience.cloud/callback',
});
