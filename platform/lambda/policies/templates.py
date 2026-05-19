"""Policy template library (lifted + condensed from Shasta's
shasta/policies/generator.py).

Each template renders via Python str.format(), so placeholders use
{double-braces}. Keep templates short — these are starter docs the user
edits afterwards, not exhaustive policies.
"""
from __future__ import annotations

TEMPLATES: dict[str, dict] = {
    "access_control": {
        "title":         "Access Control Policy",
        "soc2_controls": ["CC6.1", "CC6.2", "CC6.3", "CC5.1"],
        "body": (
            "# Access Control Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Security Team\n"
            "**SOC 2 Controls:** CC6.1, CC6.2, CC6.3, CC5.1\n\n"
            "## 1. Purpose\n\n"
            "This policy establishes requirements for controlling access to "
            "{company_name}'s information systems, applications, and data to "
            "protect against unauthorized access.\n\n"
            "## 2. Scope\n\n"
            "Applies to all employees, contractors, and third parties with "
            "access to {company_name}'s systems and data.\n\n"
            "## 3. Access Management\n\n"
            "### 3.1 Principle of Least Privilege\n"
            "All access is granted based on the principle of least privilege. "
            "Users receive only the access required to perform their job.\n\n"
            "### 3.2 User Account Management\n"
            "- New user accounts require manager approval before provisioning.\n"
            "- All accounts are disabled within 24 hours of employee departure.\n"
            "- Quarterly access reviews are conducted by department managers.\n\n"
            "### 3.3 Authentication\n"
            "- Multi-factor authentication (MFA) is required for all administrative "
            "accounts and external-facing systems.\n"
            "- Passwords must meet complexity requirements: minimum 12 characters, "
            "mixed case, numbers, and symbols.\n"
            "- Shared accounts are prohibited.\n\n"
            "### 3.4 Privileged Access\n"
            "- Privileged access is granted only when required and revoked when no "
            "longer needed.\n"
            "- All privileged access is logged and reviewed monthly.\n\n"
            "## 4. Monitoring and Enforcement\n\n"
            "Access logs are reviewed monthly. Violations result in disciplinary "
            "action up to and including termination.\n\n"
            "## 5. Review\n\n"
            "This policy is reviewed annually or upon significant changes to "
            "{company_name}'s systems.\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
    "incident_response": {
        "title":         "Incident Response Policy",
        "soc2_controls": ["CC7.3", "CC7.4", "CC7.5"],
        "body": (
            "# Incident Response Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Security Team\n"
            "**SOC 2 Controls:** CC7.3, CC7.4, CC7.5\n\n"
            "## 1. Purpose\n\n"
            "Defines how {company_name} detects, responds to, and recovers from "
            "security incidents.\n\n"
            "## 2. Incident Classification\n\n"
            "| Severity  | Examples                                            | Initial Response |\n"
            "| --------- | --------------------------------------------------- | ---------------- |\n"
            "| Critical  | Active data breach, ransomware, prod outage         | Within 15 min    |\n"
            "| High      | Privileged credential compromise, malware           | Within 1 hour    |\n"
            "| Medium    | Phishing attempt, suspicious activity               | Within 4 hours   |\n"
            "| Low       | Policy violation, low-risk vulnerability            | Within 24 hours  |\n\n"
            "## 3. Response Workflow\n\n"
            "1. **Detection** — Alerts via SIEM / CISO Copilot / user reports.\n"
            "2. **Triage** — On-call engineer classifies severity and assembles team.\n"
            "3. **Containment** — Isolate affected systems; preserve evidence.\n"
            "4. **Eradication** — Remove the threat (patch, rotate creds, etc.).\n"
            "5. **Recovery** — Restore services; verify integrity.\n"
            "6. **Post-mortem** — Within 5 business days for Critical/High.\n\n"
            "## 4. Communication\n\n"
            "- Customer notification: within 72 hours for incidents affecting customer data.\n"
            "- Regulatory notification: as required by applicable law (GDPR, HIPAA, state laws).\n"
            "- Internal: Slack #incident channel + leadership briefing.\n\n"
            "## 5. Roles\n\n"
            "- **Incident Commander**: {ir_lead}\n"
            "- **Communications Lead**: {comms_lead}\n"
            "- **Legal Counsel**: {legal_contact}\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
    "data_classification": {
        "title":         "Data Classification Policy",
        "soc2_controls": ["CC6.1", "CC6.7", "C1.1"],
        "body": (
            "# Data Classification Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Security Team\n"
            "**SOC 2 Controls:** CC6.1, CC6.7, C1.1\n\n"
            "## 1. Purpose\n\n"
            "Defines classification levels for {company_name}'s data and the "
            "handling requirements per level.\n\n"
            "## 2. Classification Levels\n\n"
            "| Level         | Examples                                  | Encryption     | Access            |\n"
            "| ------------- | ----------------------------------------- | -------------- | ----------------- |\n"
            "| Restricted    | Customer PII, credentials, secrets        | Required (at rest + in transit) | Need-to-know |\n"
            "| Confidential  | Internal financials, source code          | Required at rest | Employees only    |\n"
            "| Internal      | Internal docs, non-sensitive code         | Recommended    | All employees     |\n"
            "| Public        | Marketing, public docs                    | Optional       | Anyone            |\n\n"
            "## 3. Storage Requirements\n\n"
            "- Restricted data: only in approved systems (KMS-encrypted S3, RDS, etc.).\n"
            "- No restricted data in chat, email attachments, or local laptops.\n"
            "- Backups follow the same classification as the source data.\n\n"
            "## 4. Retention and Disposal\n\n"
            "- Customer data: retained per contract; deleted upon termination.\n"
            "- Audit logs: 1 year minimum.\n"
            "- Disposal: secure wipe for media; crypto-shred for cloud data.\n\n"
            "## 5. Review\n\n"
            "Reviewed annually by the Security and Legal teams.\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
    "vendor_management": {
        "title":         "Vendor Management Policy",
        "soc2_controls": ["CC9.2", "CC9.1"],
        "body": (
            "# Vendor Management Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Security Team\n"
            "**SOC 2 Controls:** CC9.2, CC9.1\n\n"
            "## 1. Purpose\n\n"
            "Defines how {company_name} evaluates, onboards, and monitors "
            "third-party vendors that access company or customer data.\n\n"
            "## 2. Vendor Tiering\n\n"
            "- **Critical**: Production data access, payment processing, identity providers.\n"
            "  Review: annual SOC 2 / ISO 27001 report + penetration test summary.\n"
            "- **Important**: Internal SaaS with employee data.\n"
            "  Review: security questionnaire + annual.\n"
            "- **Standard**: Other vendors with limited data exposure.\n"
            "  Review: questionnaire on onboarding.\n\n"
            "## 3. Onboarding\n\n"
            "Before granting any production data access:\n"
            "1. Security team approves the vendor.\n"
            "2. DPA / BAA signed where applicable.\n"
            "3. Vendor added to the asset inventory.\n\n"
            "## 4. Offboarding\n\n"
            "- Disable access within 24 hours of contract termination.\n"
            "- Verify data deletion per contract terms.\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
    "security_awareness": {
        "title":         "Security Awareness & Training Policy",
        "soc2_controls": ["CC2.2", "CC2.3"],
        "body": (
            "# Security Awareness & Training Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Security Team\n"
            "**SOC 2 Controls:** CC2.2, CC2.3\n\n"
            "## 1. Purpose\n\n"
            "Defines how {company_name} ensures all employees, contractors, and "
            "third parties understand their security responsibilities and are "
            "equipped to act on them.\n\n"
            "## 2. Training requirements\n\n"
            "- **New-hire**: Security training within the first week. Must be "
            "completed before production-system access is granted.\n"
            "- **Annual refresh**: All employees, contractors, and third-party "
            "personnel with access to production data complete annual training.\n"
            "- **Phishing simulation**: Quarterly simulated phishing campaigns; "
            "remedial training for repeat clickers.\n"
            "- **Role-specific**: Engineers receive secure-coding training; "
            "support / customer-success receive social-engineering training; "
            "admins receive privileged-access training.\n\n"
            "## 3. Content\n\n"
            "Topics covered:\n"
            "- Password hygiene + MFA\n"
            "- Phishing + social engineering\n"
            "- Data classification + handling (see Data Classification Policy)\n"
            "- Incident reporting (see Incident Response Policy)\n"
            "- Acceptable use\n\n"
            "## 4. Tracking + enforcement\n\n"
            "Completion tracked in the HRIS. Non-completion within 30 days of "
            "due date triggers manager + Security notification. Repeat failure "
            "leads to access suspension.\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
    "bcp_dr": {
        "title":         "Business Continuity & Disaster Recovery Policy",
        "soc2_controls": ["A1.2", "A1.3"],
        "body": (
            "# Business Continuity & Disaster Recovery Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Engineering / SRE\n"
            "**SOC 2 Controls:** A1.2, A1.3\n\n"
            "## 1. Purpose\n\n"
            "Defines how {company_name} maintains service availability and "
            "recovers from incidents that disrupt operations.\n\n"
            "## 2. RTO / RPO targets\n\n"
            "| Tier | Service                         | RTO     | RPO     |\n"
            "| ---- | ------------------------------- | ------- | ------- |\n"
            "| 1    | Customer-facing production APIs | 1 hr    | 15 min  |\n"
            "| 2    | Internal admin systems          | 4 hr    | 1 hr    |\n"
            "| 3    | Reporting / batch jobs          | 24 hr   | 24 hr   |\n\n"
            "## 3. Backups\n\n"
            "- Production data: continuous backups via cloud-native snapshots "
            "(e.g., RDS PITR, S3 versioning).\n"
            "- Retention: 35 days for PITR, 1 year for cross-region snapshots.\n"
            "- Encryption: at rest with KMS; access restricted to break-glass IAM role.\n\n"
            "## 4. Testing\n\n"
            "- Quarterly: tabletop exercise simulating a major-incident scenario.\n"
            "- Annually: live recovery drill — restore production from backup "
            "to an isolated environment, verify integrity.\n"
            "- Findings from drills feed back into runbooks within 30 days.\n\n"
            "## 5. Roles\n\n"
            "- **BCP/DR Lead**: {ir_lead}\n"
            "- **Engineering Lead**: TBD\n"
            "- **Communications**: {comms_lead}\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
    "vulnerability_mgmt": {
        "title":         "Vulnerability Management Policy",
        "soc2_controls": ["CC7.1", "CC6.8"],
        "body": (
            "# Vulnerability Management Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Security Team\n"
            "**SOC 2 Controls:** CC7.1, CC6.8\n\n"
            "## 1. Purpose\n\n"
            "Defines how {company_name} identifies, assesses, and remediates "
            "vulnerabilities across its infrastructure, applications, and "
            "dependencies.\n\n"
            "## 2. Detection sources\n\n"
            "- Cloud-native scanners (CISO Copilot, AWS Inspector, Security Hub, "
            "GuardDuty, Defender for Cloud).\n"
            "- Dependency scanners (Dependabot, Snyk, or equivalent) on every PR.\n"
            "- Container image scanning at build time.\n"
            "- Annual third-party penetration test.\n\n"
            "## 3. SLAs\n\n"
            "| Severity  | Remediation SLA                   |\n"
            "| --------- | --------------------------------- |\n"
            "| Critical  | 7 days; emergency change allowed  |\n"
            "| High      | 30 days                           |\n"
            "| Medium    | 90 days                           |\n"
            "| Low       | Next quarterly maintenance window |\n\n"
            "## 4. Exception process\n\n"
            "If a vulnerability cannot be remediated within SLA:\n"
            "1. Security team reviews + assesses risk.\n"
            "2. Compensating controls documented.\n"
            "3. Exception recorded in the risk register with a fixed expiry "
            "(see Risk Management Policy).\n\n"
            "## 5. Disclosure\n\n"
            "Customers are notified of critical vulnerabilities affecting their "
            "data within the timeframe required by contract and applicable law.\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
    "change_management": {
        "title":         "Change Management Policy",
        "soc2_controls": ["CC8.1"],
        "body": (
            "# Change Management Policy\n\n"
            "**Version:** {version}\n"
            "**Effective Date:** {effective_date}\n"
            "**Owner:** {company_name} Engineering\n"
            "**SOC 2 Controls:** CC8.1\n\n"
            "## 1. Purpose\n\n"
            "Defines how {company_name} reviews, approves, and deploys changes to "
            "production systems.\n\n"
            "## 2. Change Types\n\n"
            "- **Standard**: Routine deploys via CI/CD with code review. No CAB required.\n"
            "- **Significant**: New service, schema change, IAM policy change. "
            "Requires architecture review + on-call awareness.\n"
            "- **Emergency**: Hotfix in response to incident. Single-engineer approval; "
            "post-hoc review within 24 hours.\n\n"
            "## 3. Requirements (all change types)\n\n"
            "- Code review by at least one other engineer.\n"
            "- Automated tests pass before merge.\n"
            "- Deployed via the CI/CD pipeline (no manual production access).\n"
            "- All changes audit-logged.\n\n"
            "## 4. Rollback\n\n"
            "Every deployment must have a documented rollback path. If unable to "
            "roll back automatically, the change is classified as 'Significant'.\n\n"
            "---\n"
            "*Approved by: {approver}*\n"
        ),
    },
}


def render(template_key: str, vars: dict) -> dict:
    """Render a template with vars. Returns {title, content_md, soc2_controls}.
    Raises KeyError if template_key isn't known."""
    tpl = TEMPLATES[template_key]
    body = tpl["body"]
    defaults = {
        "version":        "1.0",
        "effective_date": vars.get("effective_date", ""),
        "company_name":   vars.get("company_name", "Your Company"),
        "approver":       vars.get("approver", "Security Team"),
        "ir_lead":        vars.get("ir_lead", "TBD"),
        "comms_lead":     vars.get("comms_lead", "TBD"),
        "legal_contact":  vars.get("legal_contact", "TBD"),
    }
    rendered = body.format(**defaults)
    return {
        "title":         tpl["title"],
        "content_md":    rendered,
        "soc2_controls": tpl["soc2_controls"],
    }


def list_templates() -> list[dict]:
    return [
        {"key": k, "title": v["title"], "soc2_controls": v["soc2_controls"]}
        for k, v in TEMPLATES.items()
    ]
