import Foundation

// Single source of truth for chip options across onboarding and profile editing.
// Mirrors the alias map in workers/src/lib/stack.ts — keep them in sync when adding chips.
enum StackOptions {
    static let cloud = [
        "AWS", "Azure", "GCP", "Oracle Cloud", "IBM Cloud", "DigitalOcean", "Alibaba Cloud",
    ]

    static let identity = [
        "Okta", "Microsoft Entra", "Azure AD", "Ping Identity",
        "Duo", "OneLogin", "JumpCloud", "ForgeRock",
    ]

    static let edr = [
        "CrowdStrike", "SentinelOne", "Microsoft Defender", "Carbon Black",
        "Cybereason", "Cortex XDR", "Trellix", "Sophos", "Trend Micro",
    ]

    static let siem = [
        "Splunk", "Datadog", "Elastic", "Sumo Logic", "Microsoft Sentinel",
        "IBM QRadar", "Exabeam", "Securonix", "ArcSight", "LogRhythm",
    ]

    static let saas = [
        "Microsoft 365", "Google Workspace", "Salesforce", "Slack", "Atlassian",
        "GitHub", "GitLab", "Zoom", "Workday", "ServiceNow",
        "Box", "Dropbox", "Notion", "Confluence", "Jira", "Asana",
    ]

    static let regulatedData = [
        "PCI DSS", "HIPAA", "GDPR", "SOC 2", "ISO 27001",
        "FedRAMP", "HITRUST", "PHI", "PII", "Source Code", "Financial Records",
    ]

    static let sector = [
        "Financial Services", "Healthcare", "Technology", "Retail",
        "Manufacturing", "Government", "Education", "Energy", "Media", "Telecom",
    ]

    static let employeeBand = [
        "<100", "100–1,000", "1,000–5,000", "5,000–20,000", "20,000+",
    ]
}
