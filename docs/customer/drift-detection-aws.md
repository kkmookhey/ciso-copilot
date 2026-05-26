# AWS Drift Detection — what we install, what it costs

When you enable "Drift detection" on your AWS connection, the
CloudFormation stack we provision in your account adds:

## AWS Config recorder (essentials profile by default)

Records configuration-state changes for ~25 security-critical resource
types — IAM (users, roles, groups, policies, access keys), networking
(security groups, NACLs, VPCs, subnets), compute (EC2, Lambda, EKS),
storage (S3 buckets, RDS instances, DynamoDB tables), crypto (KMS keys),
secrets (Secrets Manager), and audit infrastructure (CloudTrail trails,
Config recorders).

**Customer cost:** ~$30-80/month in a typical mid-size AWS account.
AWS Config charges per configuration item recorded (currently $0.003
per item). The essentials profile keeps cost low; if you want full
all-resources recording, deploy the stack with `ConfigRecordingMode=all`
(typically 3-10x more cost).

**Without this:** drift is detected at posture-scan cadence (daily)
instead of within 60 seconds.

## EventBridge rule

Forwards GuardDuty findings, Inspector findings, Security Hub
aggregated alerts, AWS Config item changes, and specific
security-relevant CloudTrail write events (security group changes,
IAM mutations, MFA changes, S3 bucket policy changes) to our central
event bus.

**Customer cost:** $0. Cross-account `PutEvents` is free; the rule
itself is also free.

## What we do NOT enable

- CloudTrail data events (S3 object-level reads/writes, Lambda
  invocations) — too high volume and cost.
- VPC Flow Logs — too high volume.
- Inline traffic inspection or endpoint agents — wrong product shape.
- AWS Config "all resources" recording — only the essentials list by
  default. Opt in via `ConfigRecordingMode=all` if you want it.

## How to opt out

Re-deploy the CloudFormation stack with `EnableAwsConfig=false`. The
Config recorder, delivery channel, and delivery bucket are dropped.
Existing event history we've already ingested stays queryable in our
backend; new drift events stop landing for that connection.
