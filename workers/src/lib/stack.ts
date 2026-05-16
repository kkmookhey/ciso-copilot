// Stack profile shape (mirrors iOS SwiftData model in CISOBrief.md §8.3) plus the
// chip-label → CPE vendor/product alias table used by the matcher.

export interface StackProfile {
  cloud: string[];
  identity: string[];
  edr: string[];
  siem: string[];
  saas: string[];
  regulatedData: string[];
  sector?: string;
  employeeBand?: string;
}

// Onboarding chips render with these human labels; the matcher needs CPE-style
// lowercase vendor/product strings to compare against NVD data. Extend as new
// chips are added to the onboarding wizard.
const ALIASES: Record<string, { vendor?: string; product?: string }> = {
  // Cloud
  "AWS":                  { vendor: "amazon" },
  "Azure":                { vendor: "microsoft", product: "azure" },
  "GCP":                  { vendor: "google" },
  "Google Cloud":         { vendor: "google" },
  "Oracle Cloud":         { vendor: "oracle" },

  // Identity
  "Okta":                 { vendor: "okta" },
  "Microsoft Entra":      { vendor: "microsoft" },
  "Azure AD":             { vendor: "microsoft" },
  "Ping Identity":        { vendor: "pingidentity" },
  "Duo":                  { vendor: "cisco", product: "duo" },
  "OneLogin":             { vendor: "onelogin" },

  // EDR
  "CrowdStrike":          { vendor: "crowdstrike" },
  "SentinelOne":          { vendor: "sentinelone" },
  "Microsoft Defender":   { vendor: "microsoft", product: "defender" },
  "Carbon Black":         { vendor: "vmware", product: "carbon_black" },

  // SIEM
  "Splunk":               { vendor: "splunk" },
  "Datadog":              { vendor: "datadog" },
  "Elastic":              { vendor: "elastic" },
  "Sumo Logic":           { vendor: "sumologic" },
  "Microsoft Sentinel":   { vendor: "microsoft", product: "sentinel" },

  // SaaS
  "Microsoft 365":        { vendor: "microsoft", product: "office_365" },
  "Google Workspace":     { vendor: "google" },
  "Salesforce":           { vendor: "salesforce" },
  "Slack":                { vendor: "slack" },
  "Atlassian":            { vendor: "atlassian" },
  "GitHub":               { vendor: "github" },
  "GitLab":               { vendor: "gitlab" },
  "Zoom":                 { vendor: "zoom" },
  "Workday":              { vendor: "workday" },
};

export function tokenizeStack(p: StackProfile): { vendors: Set<string>; products: Set<string> } {
  const vendors = new Set<string>();
  const products = new Set<string>();

  const all = [...p.cloud, ...p.identity, ...p.edr, ...p.siem, ...p.saas];
  for (const label of all) {
    const alias = ALIASES[label];
    if (alias) {
      if (alias.vendor)  vendors.add(alias.vendor);
      if (alias.product) products.add(alias.product);
      continue;
    }
    // Fallback: lowercase + underscore. Catches obvious cases without an alias entry.
    vendors.add(label.toLowerCase().trim().replace(/\s+/g, "_"));
  }

  return { vendors, products };
}

export async function hashStack(p: StackProfile): Promise<string> {
  const canonical = JSON.stringify({
    cloud:         [...p.cloud].sort(),
    identity:      [...p.identity].sort(),
    edr:           [...p.edr].sort(),
    siem:          [...p.siem].sort(),
    saas:          [...p.saas].sort(),
    regulatedData: [...p.regulatedData].sort(),
    sector:        p.sector ?? null,
  });
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}
