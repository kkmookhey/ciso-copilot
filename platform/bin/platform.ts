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
  dbCluster:             data.cluster,
  shastaRunnerRepo:      ecrStack.shastaRunner,
  shastaRunnerAzureRepo: ecrStack.shastaRunnerAzure,
  shastaRunnerEntraRepo: ecrStack.shastaRunnerEntra,
});

// API last — references events bus + CDN domain + shasta-runner Lambdas
new ApiStack(app, 'CisoCopilotApi', {
  env,
  userPool:          auth.userPool,
  userPoolClient:    auth.userPoolClient,
  dbCluster:         data.cluster,
  eventBus:          eventsStack.eventBus,
  cdnDistribution:   staticStack.cdnDistribution,
  shastaRunner:      scanStack.shastaRunner,
  shastaRunnerAzure: scanStack.shastaRunnerAzure,
  shastaRunnerEntra: scanStack.shastaRunnerEntra,
  entraAppId:        config.entraClientId,
});
