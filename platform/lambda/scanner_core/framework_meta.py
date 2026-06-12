"""Per-framework display metadata — canonical source (Task 1.1.1).

This is THE single edit point for framework display metadata. When adding or
updating a framework, edit only this file. Consumer Lambdas (ai_summary,
compliance_summary) carry byte-identical local copies for runtime because they
are bundled via ``lambda.Code.fromAsset`` and cannot import scanner_core at
Lambda invocation time.

Mirrors a tiny subset of the framework_registry's ``frameworks`` block —
(name, family, source_url, version) per framework key — so read-side Lambdas
can return family information for UI grouping without bundling the entire
scanner_core registry.

Future: promote to a shared Lambda Layer so the local copies can be removed.
"""
from __future__ import annotations

FRAMEWORK_META: dict[str, dict[str, str]] = {
    # === AI family ===
    "nist_ai_rmf": {
        "name":       "NIST AI RMF",
        "family":     "ai",
        "source_url": "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
        "version":    "1.0",
    },
    "iso_42001": {
        "name":       "ISO/IEC 42001",
        "family":     "ai",
        "source_url": "https://www.iso.org/standard/81230.html",
        "version":    "2023",
    },
    "soc2_ai": {
        "name":       "SOC 2 + AI",
        "family":     "ai",
        "source_url": "https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2",
        "version":    "2024-tbd",
    },
    "eu_ai_act": {
        "name":       "EU AI Act",
        "family":     "ai",
        "source_url": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
        "version":    "2024/1689",
    },
    "nist_ai_600_1": {
        "name":       "NIST AI 600-1",
        "family":     "ai",
        "source_url": "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf",
        "version":    "1.0",
    },
    "owasp_llm_top10": {
        "name":       "OWASP LLM Top 10",
        "family":     "ai",
        "source_url": "https://genai.owasp.org/llm-top-10/",
        "version":    "2025",
    },
    "owasp_agentic": {
        "name":       "OWASP Agentic",
        "family":     "ai",
        "source_url": "https://genai.owasp.org/",
        "version":    "draft-2025",
    },
    "mitre_atlas": {
        "name":       "MITRE ATLAS",
        "family":     "ai",
        "source_url": "https://atlas.mitre.org/matrices/ATLAS",
        "version":    "4",
    },

    # === Security family ===
    "soc2": {
        "name":       "SOC 2",
        "family":     "security",
        "source_url": "https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2",
        "version":    "TSC 2017 (2022 update)",
    },
    "iso27001": {
        "name":       "ISO/IEC 27001",
        "family":     "security",
        "source_url": "https://www.iso.org/standard/27001",
        "version":    "2022",
    },
    "fedramp": {
        "name":       "FedRAMP",
        "family":     "security",
        "source_url": "https://www.fedramp.gov/",
        "version":    "Rev 5",
    },
    "cis_aws": {
        "name":       "CIS AWS Foundations",
        "family":     "security",
        "source_url": "https://www.cisecurity.org/benchmark/amazon_web_services",
        "version":    "Latest",
    },
    "cis_azure": {
        "name":       "CIS Azure Foundations",
        "family":     "security",
        "source_url": "https://www.cisecurity.org/benchmark/azure",
        "version":    "Latest",
    },
    "cis_gcp": {
        "name":       "CIS GCP Foundations",
        "family":     "security",
        "source_url": "https://www.cisecurity.org/benchmark/google_cloud_platform",
        "version":    "Latest",
    },
    "nist_800_53": {
        "name":       "NIST SP 800-53",
        "family":     "security",
        "source_url": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf",
        "version":    "Rev 5",
    },
    "fsbp": {
        "name":       "AWS FSBP",
        "family":     "security",
        "source_url": "https://docs.aws.amazon.com/securityhub/latest/userguide/fsbp-controls.html",
        "version":    "Latest",
    },
    "mcsb": {
        "name":       "MS Cloud Security Benchmark",
        "family":     "security",
        "source_url": "https://learn.microsoft.com/en-us/security/benchmark/azure/",
        "version":    "v1",
    },

    # === Industry family ===
    "pci_dss": {
        "name":       "PCI DSS",
        "family":     "industry",
        "source_url": "https://www.pcisecuritystandards.org/",
        "version":    "4.0",
    },
    "hipaa": {
        "name":       "HIPAA Security Rule",
        "family":     "industry",
        "source_url": "https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html",
        "version":    "45 CFR 164",
    },
}


def ai_family_meta() -> dict[str, dict[str, str]]:
    """Return only the AI-family entries, for /ai/summary's response."""
    return {k: v for k, v in FRAMEWORK_META.items() if v["family"] == "ai"}
