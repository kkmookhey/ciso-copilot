"""Question library (lifted + condensed from Shasta SIG_LITE + CAIQ banks).

Each question has check_ids that the auto-fill engine pattern-matches against
the findings table — if ALL findings tagged with any of these check_ids pass,
answer is 'yes' (high confidence); if all fail, 'no'; if mixed, 'partial';
if 0 results, 'manual'.

This is a starter set. Extend by lifting more from Shasta as needed.
"""
from __future__ import annotations

BANKS: dict[str, dict] = {
    "sig_lite": {
        "name": "SIG Lite (condensed)",
        "questions": [
            {"id": "SIG-A.01.01", "category": "Access Control",
             "text": "Is multi-factor authentication required for all users with access to sensitive data?",
             "check_ids": ["iam-user-mfa", "iam-root-mfa", "azure-conditional-access-mfa"]},
            {"id": "SIG-A.01.02", "category": "Access Control",
             "text": "Is a password policy enforced (minimum length, complexity)?",
             "check_ids": ["iam-password-policy"]},
            {"id": "SIG-A.01.03", "category": "Access Control",
             "text": "Are user permissions managed via groups/roles, not direct user policies?",
             "check_ids": ["iam-no-direct-policies", "azure-rbac-least-privilege"]},
            {"id": "SIG-A.01.04", "category": "Access Control",
             "text": "Is the principle of least privilege enforced?",
             "check_ids": ["iam-overprivileged-user", "azure-rbac-least-privilege", "gcp-iam-least-privilege"]},
            {"id": "SIG-A.01.05", "category": "Access Control",
             "text": "Are unused IAM credentials disabled within 90 days?",
             "check_ids": ["iam-unused-credentials"]},
            {"id": "SIG-N.01.01", "category": "Network Security",
             "text": "Are security groups restricted to required ports only?",
             "check_ids": ["ec2-security-group-open", "azure-nsg-open", "gcp-firewall-open"]},
            {"id": "SIG-N.01.02", "category": "Network Security",
             "text": "Are inbound rules limited to known IP ranges (no 0.0.0.0/0 on management ports)?",
             "check_ids": ["ec2-ssh-open-internet", "ec2-rdp-open-internet"]},
            {"id": "SIG-N.01.03", "category": "Network Security",
             "text": "Are VPC flow logs enabled for all VPCs?",
             "check_ids": ["vpc-flow-logs-enabled"]},
            {"id": "SIG-E.01.01", "category": "Encryption",
             "text": "Is data at rest encrypted using AES-256 or equivalent?",
             "check_ids": ["s3-bucket-encryption", "ebs-volume-encryption", "rds-encryption-at-rest"]},
            {"id": "SIG-E.01.02", "category": "Encryption",
             "text": "Is data in transit protected with TLS 1.2 or higher?",
             "check_ids": ["s3-bucket-secure-transport", "elb-tls-version"]},
            {"id": "SIG-E.01.03", "category": "Encryption",
             "text": "Are encryption keys managed via a dedicated KMS?",
             "check_ids": ["kms-key-rotation", "azure-key-vault-soft-delete"]},
            {"id": "SIG-L.01.01", "category": "Logging & Monitoring",
             "text": "Is centralized audit logging enabled?",
             "check_ids": ["cloudtrail-enabled", "cloudtrail-multi-region"]},
            {"id": "SIG-L.01.02", "category": "Logging & Monitoring",
             "text": "Are audit logs protected from tampering?",
             "check_ids": ["cloudtrail-log-file-validation", "s3-mfa-delete"]},
            {"id": "SIG-L.01.03", "category": "Logging & Monitoring",
             "text": "Are real-time threat detection services enabled?",
             "check_ids": ["guardduty-enabled", "azure-defender-enabled"]},
            {"id": "SIG-S.01.01", "category": "Storage",
             "text": "Are object storage buckets private by default (no public read)?",
             "check_ids": ["s3-bucket-public-access-block", "gcs-bucket-public-access"]},
            {"id": "SIG-S.01.02", "category": "Storage",
             "text": "Is bucket versioning enabled for critical data?",
             "check_ids": ["s3-bucket-versioning"]},
            {"id": "SIG-O.01.01", "category": "Organization",
             "text": "Is the root/global-admin account protected with MFA and not used for daily ops?",
             "check_ids": ["iam-root-mfa", "iam-root-access-keys"]},
        ],
    },
    "caiq_lite": {
        "name": "CAIQ Lite (cloud-specific subset)",
        "questions": [
            {"id": "CAIQ-AIS-01", "category": "Application & Interface Security",
             "text": "Are application security testing tools in place?",
             "check_ids": []},
            {"id": "CAIQ-CCC-01", "category": "Change Control",
             "text": "Are infrastructure changes peer-reviewed before deployment?",
             "check_ids": []},
            {"id": "CAIQ-DSI-01", "category": "Data Security",
             "text": "Is customer data encrypted at rest?",
             "check_ids": ["s3-bucket-encryption", "rds-encryption-at-rest"]},
            {"id": "CAIQ-DSI-02", "category": "Data Security",
             "text": "Is customer data encrypted in transit?",
             "check_ids": ["s3-bucket-secure-transport", "elb-tls-version"]},
            {"id": "CAIQ-EKM-01", "category": "Encryption & Key Management",
             "text": "Are encryption keys rotated periodically?",
             "check_ids": ["kms-key-rotation"]},
            {"id": "CAIQ-IAM-01", "category": "Identity & Access Management",
             "text": "Is MFA enforced for all privileged accounts?",
             "check_ids": ["iam-user-mfa", "iam-root-mfa"]},
            {"id": "CAIQ-IVS-01", "category": "Infrastructure & Virtualization",
             "text": "Are infrastructure components patched within defined SLAs?",
             "check_ids": ["ec2-instance-not-patched", "azure-vm-patching"]},
            {"id": "CAIQ-LOG-01", "category": "Logging & Monitoring",
             "text": "Is centralized audit logging in place?",
             "check_ids": ["cloudtrail-enabled"]},
            {"id": "CAIQ-TVM-01", "category": "Threat & Vulnerability Mgmt",
             "text": "Is real-time threat detection enabled?",
             "check_ids": ["guardduty-enabled"]},
        ],
    },
}


def list_banks() -> list[dict]:
    return [{"key": k, "name": v["name"], "question_count": len(v["questions"])} for k, v in BANKS.items()]


def get_bank(key: str) -> dict | None:
    return BANKS.get(key)
