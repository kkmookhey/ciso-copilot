// Claude Sonnet 4.6 via the Anthropic API, with D1-backed response cache
// keyed per CISOBrief.md §10.4.

import type { Env } from "../index";

const ANTHROPIC_API = "https://api.anthropic.com/v1/messages";
const MODEL         = "claude-sonnet-4-6";

export type PromptType = "why_it_matters" | "board_paragraph" | "team_questions";

export interface CveContext {
  cveId: string;
  description: string;
  cvss: number;
  epss: number;
  inKev: boolean;
  kevDateAdded?: string | null;
  matchedStack: string[];
  affectedProducts: string[];
  sector?: string;
  severity: string;
  lastModified: string;
}

export interface TeamQuestions {
  infrastructure: string[];
  soc: string[];
  vuln_mgmt: string[];
}

// ===== Public surface =====

export async function whyItMatters(env: Env, ctx: CveContext, stackHash: string): Promise<string> {
  return cachedCall(env, ctx, "why_it_matters", stackHash, () => callAnthropic(env, whyItMattersPrompt(ctx)));
}

export async function boardParagraph(env: Env, ctx: CveContext): Promise<string> {
  return cachedCall(env, ctx, "board_paragraph", undefined, () => callAnthropic(env, boardParagraphPrompt(ctx)));
}

export async function teamQuestions(env: Env, ctx: CveContext, stackHash: string): Promise<TeamQuestions> {
  const json = await cachedCall(env, ctx, "team_questions", stackHash, async () => {
    const text = await callAnthropic(env, teamQuestionsPrompt(ctx), 800);
    return extractJson(text);
  });
  return JSON.parse(json) as TeamQuestions;
}

// ===== Caching =====

async function cachedCall(
  env: Env,
  ctx: CveContext,
  promptType: PromptType,
  stackHash: string | undefined,
  produce: () => Promise<string>,
): Promise<string> {
  const key = cacheKey(ctx.cveId, promptType, stackHash);

  const hit = await env.DB.prepare(`
    SELECT response, source_last_modified FROM llm_cache WHERE cache_key = ?
  `).bind(key).first<{ response: string; source_last_modified: string }>();

  if (hit && hit.source_last_modified === ctx.lastModified) return hit.response;

  const response = await produce();

  await env.DB.prepare(`
    INSERT INTO llm_cache (cache_key, prompt_type, response, model_version, generated_at, source_last_modified)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(cache_key) DO UPDATE SET
      response             = excluded.response,
      model_version        = excluded.model_version,
      generated_at         = excluded.generated_at,
      source_last_modified = excluded.source_last_modified
  `).bind(
    key, promptType, response, MODEL, new Date().toISOString(), ctx.lastModified,
  ).run();

  return response;
}

function cacheKey(cveId: string, promptType: PromptType, stackHash?: string): string {
  return stackHash ? `${cveId}#${promptType}#${stackHash}` : `${cveId}#${promptType}`;
}

// ===== Anthropic API call =====

interface AnthropicResponse {
  content: Array<{ type: string; text: string }>;
  model: string;
  stop_reason: string;
}

async function callAnthropic(env: Env, prompt: string, maxTokens = 600): Promise<string> {
  const res = await fetch(ANTHROPIC_API, {
    method: "POST",
    headers: {
      "x-api-key":         env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      "content-type":      "application/json",
    },
    body: JSON.stringify({
      model:      MODEL,
      max_tokens: maxTokens,
      messages:   [{ role: "user", content: prompt }],
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Anthropic ${res.status}: ${err.slice(0, 500)}`);
  }

  const data = await res.json() as AnthropicResponse;
  return data.content[0]?.text ?? "";
}

// ===== Prompts (CISOBrief.md §10) =====

function whyItMattersPrompt(c: CveContext): string {
  return `You are a cybersecurity analyst writing for a CISO who has roughly 30 seconds to read this. Given the CVE and the CISO's tech stack below, write 2–3 sentences explaining why this vulnerability matters to THEM specifically.

Rules:
- Reference the matched stack component by name.
- Reference the evidence type (KEV listing, EPSS score, or CVSS).
- End with one concrete thing the CISO should do today.
- No marketing language. No alarm. Just facts.

CVE: ${c.cveId}
Description: ${c.description}
CVSS: ${c.cvss}
EPSS: ${c.epss}
In KEV: ${c.inKev}
KEV date added: ${c.kevDateAdded ?? "n/a"}

User stack matched: ${c.matchedStack.join(", ") || "n/a"}
User sector: ${c.sector ?? "n/a"}`;
}

function boardParagraphPrompt(c: CveContext): string {
  return `Write a single paragraph (3–5 sentences) the CISO can paste into a board update or email to the executive team. Plain English. Assume the reader is not technical.

Cover: what the issue is, whether we are exposed, what we are doing about it (in general terms), and what the residual risk looks like.

Do not use words like "leverage," "robust," "best-in-class," or any other business jargon. Sound like a person who has done this for 20 years and is slightly bored.

CVE: ${c.cveId}
Description: ${c.description}
Matched stack: ${c.matchedStack.join(", ") || "n/a"}
Severity classification: ${c.severity}`;
}

function teamQuestionsPrompt(c: CveContext): string {
  return `Generate 3 questions each for three internal teams. The questions should be specific enough that the team can answer them with a yes/no plus evidence, not vague enough to spawn a meeting.

Format strictly as JSON:
{
  "infrastructure": ["q1", "q2", "q3"],
  "soc": ["q1", "q2", "q3"],
  "vuln_mgmt": ["q1", "q2", "q3"]
}

CVE: ${c.cveId}
Description: ${c.description}
Affected products: ${c.affectedProducts.join(", ") || "n/a"}
Matched stack: ${c.matchedStack.join(", ") || "n/a"}`;
}

function extractJson(s: string): string {
  const trimmed = s.trim();
  if (trimmed.startsWith("{")) return trimmed;

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fenced) return fenced[1].trim();

  const open  = trimmed.indexOf("{");
  const close = trimmed.lastIndexOf("}");
  if (open >= 0 && close > open) return trimmed.slice(open, close + 1);

  throw new Error(`could not extract JSON from response: ${trimmed.slice(0, 200)}`);
}
