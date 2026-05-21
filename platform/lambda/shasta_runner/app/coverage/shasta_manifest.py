# app/coverage/shasta_manifest.py
"""Static manifest: which benchmark controls Shasta's existing AWS checks
already cover. The scorecard's baseline; the coverage engine's
deconfliction reference (do not re-implement a check listed here).

Built by reading ~/Projects/Shasta/src/shasta/aws/*.py — see spec §6
"Gap analysis". Refresh when the bundled Shasta version changes.

Keys are Shasta check_ids. Values map a benchmark name
(cis_aws | fsbp | pci_dss | nist_800_53) to the control ids covered.
A check with no benchmark mapping still appears, with an empty dict,
so the manifest is a complete inventory of Shasta's checks.

Provenance notes (read before refreshing):

- nist_800_53 / pci_dss values are lifted from app/framework_map.py:
  its `fedramp` entries ARE NIST SP 800-53 Rev 5 control ids and are
  recorded here under `nist_800_53`; its `pci_dss` entries are recorded
  verbatim under `pci_dss`. A check absent from FRAMEWORK_MAP gets no
  nist_800_53 / pci_dss key.

- cis_aws: Shasta's own `cis_aws_controls=` attribute is, for most
  checks, a placeholder wildcard ("2.x", "3.x", "5.x", "2.3.x",
  "4.x", "1.x", "5.4.x") — NOT a real CIS id. Only a handful of checks
  carry a concrete id. So cis_aws here is mapped by intent against the
  vendored CIS AWS Foundations Benchmark v3.0.0 catalog
  (coverage/benchmarks/cis_aws.json) and recorded only where a check's
  intent unambiguously matches a real leaf control id. Checks whose
  Shasta cis_aws_controls is a wildcard with no clear v3.0.0 match get
  no cis_aws key. Concrete real ids found in Shasta source (5.6, 1.18,
  2.4->2.4.1, 2.2->2.2.1, 1.10, 1.16, 1.20, 3.5, 3.2, 3.8, 4.16,
  4.1/4.2/4.3) were validated against the catalog and reconciled
  (e.g. "2.4" -> "2.4.1", encryption checks -> "2.3.1"/"2.2.1").

- fsbp: Shasta has no FSBP signal. Mapped by intent only where a check
  clearly matches a real FSBP control id from
  coverage/benchmarks/fsbp.json. Many checks have no fsbp key — that is
  honest partial coverage, not an omission.
"""
from __future__ import annotations

SHASTA_CHECKS: dict[str, dict[str, list[str]]] = {
    # --- API Gateway, ACM (serverless.py, encryption.py) ---
    "apigw-authorizer": {
        "nist_800_53": ["AC-3"],
        "pci_dss":     ["7.2.1"],
        "fsbp":        ["APIGateway.8"],
    },
    "apigw-client-cert": {
        "nist_800_53": ["IA-3", "SC-8"],
    },
    "apigw-logging": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["APIGateway.1", "APIGateway.9"],
    },
    "apigw-request-validation": {
        "nist_800_53": ["SI-10"],
    },
    "apigw-throttling": {
        "nist_800_53": ["SC-5"],
    },
    "apigw-waf": {
        "nist_800_53": ["SC-7"],
        "pci_dss":     ["6.4.2"],
        "fsbp":        ["APIGateway.4"],
    },
    "acm-expiring-certs": {
        "nist_800_53": ["SC-12", "SC-17"],
        "pci_dss":     ["4.2.1"],
        "fsbp":        ["ACM.1"],
    },

    # --- Backup (backup.py) ---
    "aws-backup-cross-region-copy": {
        "nist_800_53": ["CP-9"],
    },
    "aws-backup-plans": {
        "nist_800_53": ["CP-9"],
    },
    "aws-backup-vault-access-policy": {
        "nist_800_53": ["CP-9", "AC-3"],
    },
    "aws-backup-vault-cmk": {
        "nist_800_53": ["CP-9", "SC-28"],
        "fsbp":        ["Backup.1"],
    },
    "aws-backup-vault-exists": {
        "nist_800_53": ["CP-9"],
    },
    "aws-backup-vault-lock": {
        "nist_800_53": ["CP-9", "AU-9"],
    },

    # --- CloudFront (cloudfront.py) ---
    "cloudfront-geo-restrictions": {
        "nist_800_53": ["AC-3", "SC-7"],
    },
    "cloudfront-https-only": {
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
        "fsbp":        ["CloudFront.3"],
    },
    "cloudfront-min-tls": {
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
        "fsbp":        ["CloudFront.10", "CloudFront.15"],
    },
    "cloudfront-oac": {
        "nist_800_53": ["AC-3"],
        "fsbp":        ["CloudFront.13"],
    },
    "cloudfront-waf": {
        "nist_800_53": ["SC-7"],
        "pci_dss":     ["6.4.2"],
        "fsbp":        ["CloudFront.6"],
    },

    # --- CloudWatch Logs (cloudwatch_logs.py) ---
    "cwl-kms-encryption": {
        "nist_800_53": ["AU-9", "SC-28"],
        "pci_dss":     ["10.3.2"],
    },
    "cwl-retention": {
        "nist_800_53": ["AU-11"],
        "pci_dss":     ["10.5.1"],
    },

    # --- Compute: EC2, ECS, EKS (compute.py) ---
    "ec2-ami-age": {
        "nist_800_53": ["SI-2"],
        "pci_dss":     ["6.3.3"],
    },
    "ec2-imdsv2-enforced": {
        "cis_aws":     ["5.6"],
        "nist_800_53": ["CM-7", "AC-6"],
        "fsbp":        ["EC2.8"],
    },
    "ec2-instance-profile": {
        "cis_aws":     ["1.18"],
        "nist_800_53": ["AC-6", "IA-2"],
    },
    "ec2-public-ips": {
        "nist_800_53": ["SC-7"],
        "pci_dss":     ["1.4.1"],
        "fsbp":        ["EC2.9"],
    },
    "ecs-task-privileged": {
        "nist_800_53": ["AC-6", "CM-7"],
        "fsbp":        ["ECS.4"],
    },
    "ecs-task-root-user": {
        "nist_800_53": ["AC-6", "CM-7"],
        "fsbp":        ["ECS.20"],
    },
    "eks-audit-logging": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["EKS.8"],
    },
    "eks-private-endpoint": {
        "nist_800_53": ["SC-7"],
        "pci_dss":     ["1.4.1"],
        "fsbp":        ["EKS.1"],
    },
    "eks-secrets-encryption": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["EKS.3"],
    },

    # --- Data warehouse: Redshift, ElastiCache, Neptune (data_warehouse.py) ---
    "elasticache-at-rest-encryption": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["ElastiCache.4"],
    },
    "elasticache-auth-token": {
        "nist_800_53": ["IA-2", "AC-3"],
    },
    "elasticache-transit-encryption": {
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
        "fsbp":        ["ElastiCache.5"],
    },
    "neptune-encryption": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["Neptune.1"],
    },
    "redshift-audit-logging": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["Redshift.4"],
    },
    "redshift-encryption": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["Redshift.10"],
    },
    "redshift-public-access": {
        "nist_800_53": ["SC-7"],
        "pci_dss":     ["1.4.1"],
        "fsbp":        ["Redshift.1"],
    },
    "redshift-require-ssl": {
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
        "fsbp":        ["Redshift.2"],
    },

    # --- Databases: RDS, DocumentDB, DynamoDB (databases.py) ---
    "docdb-audit-logs": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["DocumentDB.4"],
    },
    "docdb-encryption": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["DocumentDB.1"],
    },
    "dynamodb-kms": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
    },
    "dynamodb-pitr": {
        "nist_800_53": ["CP-9"],
        "fsbp":        ["DynamoDB.2"],
    },
    "rds-auto-minor-upgrade": {
        "cis_aws":     ["2.3.2"],
        "nist_800_53": ["SI-2"],
        "pci_dss":     ["6.3.3"],
        "fsbp":        ["RDS.13"],
    },
    "rds-deletion-protection": {
        "nist_800_53": ["CP-9"],
        "fsbp":        ["RDS.8"],
    },
    "rds-force-ssl": {
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
    },
    "rds-iam-auth": {
        "nist_800_53": ["IA-2", "AC-3"],
        "pci_dss":     ["8.2.1"],
        "fsbp":        ["RDS.10"],
    },
    "rds-min-tls": {
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
    },
    "rds-pi-kms": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
    },
    "rds-postgres-log-settings": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["RDS.36"],
    },

    # --- Encryption: EFS, SNS, SQS, Secrets Manager, EBS, RDS (encryption.py) ---
    "efs-encryption": {
        "cis_aws":     ["2.4.1"],
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["EFS.1"],
    },
    "sns-encryption": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
    },
    "sqs-encryption": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["SQS.1"],
    },
    "secrets-manager-rotation": {
        "nist_800_53": ["IA-5"],
        "pci_dss":     ["8.3.9"],
        "fsbp":        ["SecretsManager.1", "SecretsManager.4"],
    },
    "ebs-encryption-by-default": {
        "cis_aws":     ["2.2.1"],
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["EC2.7"],
    },
    "ebs-volume-encrypted": {
        "cis_aws":     ["2.2.1"],
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["EC2.3"],
    },
    "rds-encryption-at-rest": {
        "cis_aws":     ["2.3.1"],
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
        "fsbp":        ["RDS.3"],
    },
    "rds-no-public-access": {
        "cis_aws":     ["2.3.3"],
        "nist_800_53": ["SC-7"],
        "pci_dss":     ["1.4.1"],
        "fsbp":        ["RDS.2"],
    },
    "rds-backup-enabled": {
        "nist_800_53": ["CP-9"],
        "fsbp":        ["RDS.11"],
    },

    # --- IAM (iam.py) ---
    "iam-access-key-rotation": {
        "cis_aws":     ["1.14"],
        "nist_800_53": ["IA-5"],
        "pci_dss":     ["8.3.9"],
        "fsbp":        ["IAM.3"],
    },
    "iam-inactive-user": {
        "cis_aws":     ["1.12"],
        "nist_800_53": ["AC-2"],
        "pci_dss":     ["8.2.6"],
        "fsbp":        ["IAM.8"],
    },
    "iam-no-direct-policies": {
        "cis_aws":     ["1.15"],
        "nist_800_53": ["AC-6"],
        "pci_dss":     ["7.2.1"],
        "fsbp":        ["IAM.2"],
    },
    "iam-overprivileged-user": {
        "nist_800_53": ["AC-6"],
        "pci_dss":     ["7.2.1"],
    },
    "iam-password-policy": {
        "cis_aws":     ["1.8", "1.9"],
        "nist_800_53": ["IA-5"],
        "pci_dss":     ["8.3.6"],
        "fsbp":        ["IAM.7"],
    },
    "iam-policy-wildcards": {
        "cis_aws":     ["1.16"],
        "nist_800_53": ["AC-6"],
        "pci_dss":     ["7.2.1"],
        "fsbp":        ["IAM.1", "IAM.21"],
    },
    "iam-role-trust-external": {
        "nist_800_53": ["AC-3", "AC-6"],
        "pci_dss":     ["7.2.1"],
    },
    "iam-root-access-keys": {
        "cis_aws":     ["1.4"],
        "nist_800_53": ["AC-6", "IA-2"],
        "pci_dss":     ["8.6.1"],
        "fsbp":        ["IAM.4"],
    },
    "iam-root-mfa": {
        "cis_aws":     ["1.5", "1.6"],
        "nist_800_53": ["IA-2"],
        "pci_dss":     ["8.4.1"],
        "fsbp":        ["IAM.6"],
    },
    "iam-root-not-used": {
        "cis_aws":     ["1.7"],
        "nist_800_53": ["AC-6"],
        "pci_dss":     ["7.2.5"],
    },
    "iam-unused-roles": {
        "nist_800_53": ["AC-2"],
        "pci_dss":     ["7.2.1"],
    },
    "iam-user-mfa": {
        "cis_aws":     ["1.10"],
        "nist_800_53": ["IA-2"],
        "pci_dss":     ["8.4.1"],
        "fsbp":        ["IAM.5"],
    },

    # --- KMS (kms.py) ---
    "kms-key-policy-wildcards": {
        "nist_800_53": ["AC-6", "SC-12"],
        "fsbp":        ["KMS.1", "KMS.2"],
    },
    "kms-key-rotation": {
        "cis_aws":     ["3.6"],
        "nist_800_53": ["SC-12"],
        "pci_dss":     ["3.7.4"],
    },
    "kms-no-unrestricted-principal": {
        "nist_800_53": ["AC-6", "SC-12"],
        "fsbp":        ["KMS.5"],
    },
    "kms-scheduled-deletion": {
        "nist_800_53": ["SC-12"],
        "fsbp":        ["KMS.3"],
    },

    # --- Logging / detection (logging_checks.py) ---
    "aws-config-conformance-packs": {
        "nist_800_53": ["CM-6", "CA-7"],
    },
    "cloudtrail-enabled": {
        "cis_aws":     ["3.1"],
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["CloudTrail.1"],
    },
    "cloudtrail-kms-encryption": {
        "cis_aws":     ["3.5"],
        "nist_800_53": ["AU-9", "SC-28"],
        "pci_dss":     ["10.3.2"],
        "fsbp":        ["CloudTrail.2"],
    },
    "cloudtrail-log-validation": {
        "cis_aws":     ["3.2"],
        "nist_800_53": ["AU-9"],
        "pci_dss":     ["10.3.4"],
        "fsbp":        ["CloudTrail.4"],
    },
    "cloudtrail-s3-object-lock": {
        "nist_800_53": ["AU-9"],
        "pci_dss":     ["10.3.2"],
    },
    "cloudwatch-alarms-cis-4": {
        "cis_aws":     ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7",
                        "4.8", "4.9", "4.10", "4.11", "4.12", "4.13",
                        "4.14", "4.15"],
        "nist_800_53": ["AU-6", "SI-4"],
        "pci_dss":     ["10.4.1"],
    },
    "config-enabled": {
        "cis_aws":     ["3.3"],
        "nist_800_53": ["CM-2", "CM-3", "CA-7"],
        "fsbp":        ["Config.1"],
    },
    "guardduty-enabled": {
        "nist_800_53": ["SI-4"],
        "pci_dss":     ["11.5.1"],
        "fsbp":        ["GuardDuty.1"],
    },
    "guardduty-no-active-findings": {
        "nist_800_53": ["SI-4", "IR-4"],
        "pci_dss":     ["11.5.1"],
    },
    "iam-access-analyzer": {
        "cis_aws":     ["1.20"],
        "nist_800_53": ["AC-6", "CA-7"],
        "pci_dss":     ["7.2.1"],
    },
    "security-hub-enabled": {
        "cis_aws":     ["4.16"],
        "nist_800_53": ["CA-7", "SI-4"],
    },

    # --- Networking: ELB, security groups, VPC flow logs (networking.py) ---
    "elb-access-logs": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["ELB.5"],
    },
    "elb-drop-invalid-headers": {
        "nist_800_53": ["SC-7", "SI-10"],
        "fsbp":        ["ELB.4"],
    },
    "elb-listener-tls": {
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
        "fsbp":        ["ELB.1"],
    },
    "sg-default-restricted": {
        "cis_aws":     ["5.4"],
        "nist_800_53": ["SC-7", "AC-4"],
        "pci_dss":     ["1.2.1"],
        "fsbp":        ["EC2.2"],
    },
    "sg-no-unrestricted-ingress": {
        "cis_aws":     ["5.2", "5.3"],
        "nist_800_53": ["SC-7", "AC-4"],
        "pci_dss":     ["1.3.1"],
        "fsbp":        ["EC2.18", "EC2.19"],
    },
    "vpc-flow-logs-enabled": {
        "cis_aws":     ["3.7"],
        "nist_800_53": ["AU-2", "SI-4"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["EC2.6"],
    },

    # --- Organizations (organizations.py) ---
    "aws-backup-policy": {
        "nist_800_53": ["CP-9"],
    },
    "aws-delegated-admin": {},
    "aws-org-enabled": {},
    "aws-org-scps": {
        "nist_800_53": ["AC-3", "CM-7"],
    },
    "aws-tag-policy": {
        "nist_800_53": ["CM-8"],
    },

    # --- Pentest / vulnerabilities (pentest.py, vulnerabilities.py) ---
    "inspector-network-reachability": {
        "nist_800_53": ["RA-5", "SC-7"],
        "pci_dss":     ["11.3.1"],
    },
    "inspector-critical-findings": {
        "nist_800_53": ["RA-5", "SI-2"],
        "pci_dss":     ["6.3.1", "11.3.1"],
    },
    "inspector-enabled": {
        "nist_800_53": ["RA-5"],
        "pci_dss":     ["11.3.1"],
        "fsbp":        ["Inspector.1", "Inspector.2"],
    },

    # --- Serverless: Lambda, API Gateway, Step Functions (serverless.py) ---
    "lambda-code-signing": {
        "nist_800_53": ["SI-7", "CM-5"],
    },
    "lambda-dlq": {
        "nist_800_53": ["SI-11"],
    },
    "lambda-env-kms": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
    },
    "lambda-function-url-auth": {
        "nist_800_53": ["AC-3", "IA-2"],
        "pci_dss":     ["7.2.1"],
        "fsbp":        ["Lambda.1"],
    },
    "lambda-layer-origin": {
        "nist_800_53": ["SI-7", "SR-3"],
    },
    "lambda-runtime-eol": {
        "nist_800_53": ["SI-2"],
        "pci_dss":     ["6.3.3"],
        "fsbp":        ["Lambda.2"],
    },
    "sfn-logging": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["StepFunctions.1"],
    },

    # --- S3 (storage.py) ---
    "s3-access-logging": {
        "nist_800_53": ["AU-2", "AU-12"],
        "pci_dss":     ["10.2.1"],
        "fsbp":        ["S3.9"],
    },
    "s3-encryption-at-rest": {
        "nist_800_53": ["SC-28"],
        "pci_dss":     ["3.5.1"],
    },
    "s3-kms-cmk": {
        "nist_800_53": ["SC-28", "SC-12"],
        "pci_dss":     ["3.5.1"],
    },
    "s3-object-ownership": {
        "nist_800_53": ["AC-3"],
        "fsbp":        ["S3.12"],
    },
    "s3-public-access-block": {
        "cis_aws":     ["2.1.4"],
        "nist_800_53": ["SC-7", "AC-3"],
        "pci_dss":     ["1.4.1"],
        "fsbp":        ["S3.1", "S3.8"],
    },
    "s3-ssl-only": {
        "cis_aws":     ["2.1.1"],
        "nist_800_53": ["SC-8", "SC-13"],
        "pci_dss":     ["4.2.1"],
        "fsbp":        ["S3.5"],
    },
    "s3-versioning": {
        "nist_800_53": ["CP-9"],
    },

    # --- VPC endpoints (vpc_endpoints.py) ---
    "aws-vpc-endpoints": {
        "nist_800_53": ["SC-7"],
    },
}
