# Benchmark catalogs

Each `*.json` file is a normalized control catalog: a JSON list of
`{"id": str, "title": str}`. Consumed by `coverage/scorecard.py`.

| File | Benchmark | Version | Source |
|------|-----------|---------|--------|
| `cis_aws.json` | CIS AWS Foundations Benchmark | v3.0.0 | Turbot/Steampipe aws-compliance mod — `cis_v300/section_*.pp` at commit on main branch, 2026-05-21. Official CIS publication: https://www.cisecurity.org/benchmark/amazon_web_services |
| `fsbp.json` | AWS Foundational Security Best Practices | v1.0.0 (retrieved 2026-05-21) | AWS Security Hub controls reference — https://docs.aws.amazon.com/securityhub/latest/userguide/fsbp-standard.html |
| `pci_dss.json` | PCI DSS | v4.0 / v4.0.1 | Control IDs sourced from Azure Policy regulatory compliance JSON (249 controls) — https://github.com/Azure/azure-policy/blob/master/built-in-policies/policySetDefinitions/Regulatory%20Compliance/PCI_DSS_V4.0.json. Titles extracted from the official PCI DSS v4.0.1 PDF (publicly hosted by Middlebury University, retrieved 2026-05-21). |
| `nist_800_53.json` | NIST SP 800-53 | Rev 5 | Official OSCAL catalog JSON — https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json (retrieved 2026-05-21) |

## NIST catalog scope

Base controls only (no control enhancements). Control enhancements have IDs like `AC-2(1)`
in OSCAL (encoded as `ac-2.1`). These are excluded; chosen 2026-05-21. Keep this choice
consistent across refreshes.

- Base controls: 324
- Controls with enhancements included would be: 1196

## CIS catalog scope

CIS AWS Foundations Benchmark v3.0.0, all 62 controls across sections 1–5
(Identity and Access Management, Storage, Logging, Monitoring, Networking).
Includes both automated and manual controls. Excludes subsection group headers
(e.g., "2.1 Simple Storage Service (S3)") — only leaf requirement controls.

## FSBP catalog scope

AWS Foundational Security Best Practices standard v1.0.0. All 370 controls
as listed in the AWS Security Hub controls reference page, sourced 2026-05-21.

## PCI DSS catalog scope

PCI DSS v4.0 requirements. 249 sub-requirement IDs sourced from the Azure Policy
regulatory compliance JSON (Azure/azure-policy repo). Titles extracted from the
official PCI DSS v4.0.1 PDF (superset of v4.0 requirements; v4.0 IDs unchanged
in v4.0.1). One control (9.5.1.2.1) uses its parent section title as the PDF
extraction returned the same text block for both. Appendix controls (A1.x, A3.x)
are not included in this catalog — the Azure Policy source does not include them.

To refresh: re-source the publication, re-run the transform to the shape
above, replace the JSON, update the version/date in this table.
