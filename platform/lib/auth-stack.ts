import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
import { Construct } from 'constructs';
import * as path from 'path';
import { config } from './config';

interface AuthStackProps extends cdk.StackProps {
  dbCluster: rds.DatabaseCluster;
}

export class AuthStack extends cdk.Stack {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: AuthStackProps) {
    super(scope, id, props);

    // ===== Post-confirmation Lambda — runs on first sign-in via federation =====
    const postConfirmation = new lambda.Function(this, 'PostConfirmation', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'main.handler',
      code:    lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'post_confirmation')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        DB_CLUSTER_ARN:     props.dbCluster.clusterArn,
        DB_SECRET_ARN:      props.dbCluster.secret!.secretArn,
        DB_NAME:            'ciso_copilot',
        APPROVAL_RECIPIENT: config.approvalRecipient,
        DOMAIN:             config.domain,
        // Token signing key referenced from Secrets Manager once provisioned.
        // For now, default to a fixed name; create the secret separately.
        APPROVAL_TOKEN_SECRET_NAME: 'ciso-copilot/approval-signing-key',
      },
    });
    props.dbCluster.grantDataApiAccess(postConfirmation);
    postConfirmation.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['ses:SendEmail', 'ses:SendRawEmail'],
      resources: ['*'],
    }));
    postConfirmation.addToRolePolicy(new iam.PolicyStatement({
      actions:   ['secretsmanager:GetSecretValue'],
      resources: [`arn:aws:secretsmanager:${this.region}:${this.account}:secret:ciso-copilot/*`],
    }));

    // ===== User Pool =====
    this.userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName:    'ciso-copilot',
      signInAliases:   { email: true },
      autoVerify:      { email: true },
      standardAttributes: {
        email:      { required: true, mutable: false },
        givenName:  { required: false, mutable: true },
        familyName: { required: false, mutable: true },
      },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { sms: false, otp: true },
      passwordPolicy: { minLength: 12, requireDigits: true, requireSymbols: true },
      lambdaTriggers:  { postConfirmation },
      removalPolicy:   cdk.RemovalPolicy.RETAIN,
    });

    // ===== Microsoft Entra OIDC (multi-tenant) =====
    // "Multiple organizations" account type → issuer uses /organizations endpoint
    // which only accepts org accounts (rejects consumer MSAs at the Microsoft layer).
    const microsoft = new cognito.UserPoolIdentityProviderOidc(this, 'Microsoft', {
      userPool:     this.userPool,
      name:         'Microsoft',
      clientId:     config.entraClientId,
      clientSecret: config.entraClientSecret,
      issuerUrl:    'https://login.microsoftonline.com/organizations/v2.0',
      scopes:       ['openid', 'email', 'profile'],
      attributeMapping: {
        email:      cognito.ProviderAttribute.other('email'),
        givenName:  cognito.ProviderAttribute.other('given_name'),
        familyName: cognito.ProviderAttribute.other('family_name'),
      },
    });

    // ===== Google OIDC (Workspace) =====
    const google = new cognito.UserPoolIdentityProviderGoogle(this, 'Google', {
      userPool:     this.userPool,
      clientId:     config.googleClientId,
      clientSecretValue: cdk.SecretValue.unsafePlainText(config.googleClientSecret),
      scopes:       ['openid', 'email', 'profile'],
      attributeMapping: {
        email:      cognito.ProviderAttribute.GOOGLE_EMAIL,
        givenName:  cognito.ProviderAttribute.GOOGLE_GIVEN_NAME,
        familyName: cognito.ProviderAttribute.GOOGLE_FAMILY_NAME,
      },
    });

    // ===== Hosted UI domain — Cognito-managed subdomain for v0; switch to auth.<domain> in Phase A =====
    this.userPool.addDomain('CognitoDomain', {
      cognitoDomain: { domainPrefix: 'ciso-copilot' },
    });

    // ===== App client — used by iOS app =====
    this.userPoolClient = this.userPool.addClient('iOSClient', {
      generateSecret: false,                    // public client (mobile)
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.custom('Microsoft'),
        cognito.UserPoolClientIdentityProvider.GOOGLE,
      ],
      oAuth: {
        flows:  { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [
          `https://auth.${config.domain}/oauth2/idpresponse`,
          'cisocopilot://auth/callback',
        ],
        logoutUrls: [
          `https://${config.domain}/`,
          'cisocopilot://auth/logout',
        ],
      },
      accessTokenValidity:  cdk.Duration.minutes(60),
      idTokenValidity:      cdk.Duration.minutes(60),
      refreshTokenValidity: cdk.Duration.days(30),
    });
    this.userPoolClient.node.addDependency(microsoft);
    this.userPoolClient.node.addDependency(google);

    // ===== Web app client — used by the SPA at app.settlingforless.com =====
    const webClient = this.userPool.addClient('WebClient', {
      generateSecret: false,                    // public client (browser)
      supportedIdentityProviders: [
        cognito.UserPoolClientIdentityProvider.custom('Microsoft'),
        cognito.UserPoolClientIdentityProvider.GOOGLE,
      ],
      oAuth: {
        flows:  { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [
          `https://app.${config.domain}/callback`,
          'http://localhost:5173/callback',     // Vite dev server
        ],
        logoutUrls: [
          `https://app.${config.domain}/`,
          'http://localhost:5173/',
        ],
      },
      accessTokenValidity:  cdk.Duration.minutes(60),
      idTokenValidity:      cdk.Duration.minutes(60),
      refreshTokenValidity: cdk.Duration.days(30),
    });
    webClient.node.addDependency(microsoft);
    webClient.node.addDependency(google);

    new cdk.CfnOutput(this, 'UserPoolId',       { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
    new cdk.CfnOutput(this, 'WebClientId',      { value: webClient.userPoolClientId });
    new cdk.CfnOutput(this, 'CognitoDomain',    { value: `ciso-copilot.auth.${config.awsRegion}.amazoncognito.com` });
  }
}
