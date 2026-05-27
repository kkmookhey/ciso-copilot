import * as cdk from 'aws-cdk-lib';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import { Construct } from 'constructs';
import * as path from 'path';
import { config } from './config';

// ACM cert + canonical domain come from platform/.env so the repo stays
// operator-agnostic. The legacy `app.settlingforless.com` stop-gap is kept
// as an optional alternate domain — empty string disables it.
const APP_CERT_ARN  = config.appCertArn;
const APP_DOMAIN    = config.legacyAppDomain;
const SHASTA_DOMAIN = config.shastaDomain;

/// Static hosting for:
///  • cdn.settlingforless.com — public CloudFormation templates customers
///    deep-link to during onboarding. Auto-deployed from platform/cfn/.
///  • app.settlingforless.com — the Vite + React SPA. Bucket is created now;
///    `npm run build` output gets uploaded from the web/ project in a later
///    pass once that exists.
///
/// Custom domains require ACM cert + DNS. For now we expose the
/// CloudFront-generated *.cloudfront.net hostnames; KK adds CNAME records
/// to settlingforless.com when ready and we add an alternate domain name
/// at that point.
export class StaticStack extends cdk.Stack {
  public readonly cdnDistribution: cloudfront.Distribution;
  public readonly appDistribution: cloudfront.Distribution;
  public readonly cdnBucket: s3.Bucket;
  public readonly appBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ============================================================
    // CDN — CloudFormation templates for customer onboarding
    // ============================================================

    this.cdnBucket = new s3.Bucket(this, 'CdnBucket', {
      bucketName:        `ciso-copilot-cdn-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption:        s3.BucketEncryption.S3_MANAGED,
      versioned:         true,                       // history of CFN template revisions
      enforceSSL:        true,
      removalPolicy:     cdk.RemovalPolicy.RETAIN,
    });

    this.cdnDistribution = new cloudfront.Distribution(this, 'CdnDist', {
      comment: 'CISO Copilot CDN — CFN templates',
      defaultBehavior: {
        origin:               origins.S3BucketOrigin.withOriginAccessControl(this.cdnBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy:          cloudfront.CachePolicy.CACHING_OPTIMIZED,
        allowedMethods:       cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      },
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,  // NA + EU only — cheaper
    });

    new s3deploy.BucketDeployment(this, 'CfnTemplatesDeployment', {
      sources:               [s3deploy.Source.asset(path.join(__dirname, '..', 'cfn'))],
      destinationBucket:     this.cdnBucket,
      destinationKeyPrefix:  'cfn',
      distribution:          this.cdnDistribution,
      distributionPaths:     ['/cfn/*'],
    });

    // ============================================================
    // App — Vite SPA at app.settlingforless.com
    // ============================================================

    this.appBucket = new s3.Bucket(this, 'AppBucket', {
      bucketName:        `ciso-copilot-app-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption:        s3.BucketEncryption.S3_MANAGED,
      enforceSSL:        true,
      removalPolicy:     cdk.RemovalPolicy.RETAIN,
    });

    const appCert = acm.Certificate.fromCertificateArn(this, 'AppCert', APP_CERT_ARN);

    this.appDistribution = new cloudfront.Distribution(this, 'AppDist', {
      comment: 'CISO Copilot web SPA',
      defaultBehavior: {
        origin:               origins.S3BucketOrigin.withOriginAccessControl(this.appBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy:          cloudfront.CachePolicy.CACHING_OPTIMIZED,
        allowedMethods:       cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      },
      defaultRootObject: 'index.html',
      // SPA client-side routing — 403/404 rewrite to index.html so React Router
      // can resolve the path itself.
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.seconds(0) },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html', ttl: cdk.Duration.seconds(0) },
      ],
      priceClass:  cloudfront.PriceClass.PRICE_CLASS_100,
      domainNames: APP_DOMAIN ? [SHASTA_DOMAIN, APP_DOMAIN] : [SHASTA_DOMAIN],
      certificate: appCert,
    });

    new cdk.CfnOutput(this, 'CdnDomain', { value: this.cdnDistribution.distributionDomainName });
    new cdk.CfnOutput(this, 'AppDomain', { value: this.appDistribution.distributionDomainName });
    new cdk.CfnOutput(this, 'CdnBucketName', { value: this.cdnBucket.bucketName });
    new cdk.CfnOutput(this, 'AppBucketName', { value: this.appBucket.bucketName });
  }
}
