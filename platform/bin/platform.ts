#!/usr/bin/env node
import 'dotenv/config';
import * as cdk from 'aws-cdk-lib';
import { config } from '../lib/config';
import { NetworkStack } from '../lib/network-stack';
import { DataStack } from '../lib/data-stack';
import { AuthStack } from '../lib/auth-stack';
import { ApiStack } from '../lib/api-stack';
import { EcrStack } from '../lib/ecr-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region:  config.awsRegion,
};

const network = new NetworkStack(app, 'CisoCopilotNetwork', { env });

const data = new DataStack(app, 'CisoCopilotData', { env, vpc: network.vpc });

const auth = new AuthStack(app, 'CisoCopilotAuth', { env, dbCluster: data.cluster });

new ApiStack(app, 'CisoCopilotApi', {
  env,
  userPool:       auth.userPool,
  userPoolClient: auth.userPoolClient,
  dbCluster:      data.cluster,
});

// Phase A — container image repo for the Shasta scanner Lambda + Fargate fallback.
new EcrStack(app, 'CisoCopilotEcr', { env });
