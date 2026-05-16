// Nightly brief generation. For each user: match → rank → LLM-enrich top items → store → APNs.
// CISOBrief.md §9 phase 4 + phase 8.

import type { Env } from "../index";
import { matchCvesForStack, type MatchedItem } from "../lib/match";
import { hashStack, type StackProfile } from "../lib/stack";
import {
  whyItMatters, boardParagraph, teamQuestions,
  type CveContext, type TeamQuestions,
} from "../lib/anthropic";
import { sendApnsPush } from "../lib/apns";

const TOP_N = 5;

interface UserRow {
  device_id: string;
  stack_profile: string;
  device_token: string | null;
}

interface EnrichedItem extends MatchedItem {
  whyItMatters: string;
  boardParagraph: string;
  teamQuestions: TeamQuestions;
}

export async function handleBriefCron(env: Env): Promise<void> {
  const users = await env.DB.prepare(`
    SELECT device_id, stack_profile, device_token FROM users
  `).all<UserRow>();

  console.log(`brief-cron: ${users.results.length} users`);
  for (const u of users.results) {
    try {
      await generateBriefForUser(env, u);
    } catch (err) {
      console.error(`brief-cron: user ${u.device_id} failed:`, err);
    }
  }
}

export async function generateBriefForUser(env: Env, user: UserRow): Promise<void> {
  const profile = JSON.parse(user.stack_profile) as StackProfile;
  const stackHash = await hashStack(profile);
  const matches = await matchCvesForStack(env.DB, profile, TOP_N);

  if (matches.length === 0) {
    await storeBrief(env, user.device_id, []);
    console.log(`brief: ${user.device_id} — no matches`);
    return;
  }

  // Fully parallel: all items × all prompts at once. ~5–10s wall time for 5 items.
  const enriched: EnrichedItem[] = await Promise.all(
    matches.map(async (m) => {
      const ctx: CveContext = {
        cveId:            m.cveId,
        description:      m.description,
        cvss:             m.cvssScore,
        epss:             m.epssScore,
        inKev:            m.inKev,
        kevDateAdded:     m.kevDateAdded,
        matchedStack:     [...m.matchedVendors, ...m.matchedProducts],
        affectedProducts: m.matchedProducts,
        sector:           profile.sector,
        severity:         m.severity,
        lastModified:     m.lastModified || new Date().toISOString().split("T")[0],
      };

      const [why, board, questions] = await Promise.all([
        whyItMatters(env, ctx, stackHash),
        boardParagraph(env, ctx),
        teamQuestions(env, ctx, stackHash),
      ]);

      return { ...m, whyItMatters: why, boardParagraph: board, teamQuestions: questions };
    }),
  );

  await storeBrief(env, user.device_id, enriched);

  const actNow = enriched.filter(e => e.severity === "act_now");
  if (actNow.length > 0 && user.device_token) {
    await sendApnsPush(env, user.device_token, actNow.length, actNow[0].cveId);
  }

  console.log(`brief: ${user.device_id} — ${enriched.length} items, ${actNow.length} act_now`);
}

async function storeBrief(env: Env, deviceId: string, items: EnrichedItem[]): Promise<void> {
  const date = new Date().toISOString().split("T")[0];
  await env.DB.prepare(`
    INSERT INTO briefs (device_id, date, items, generated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(device_id, date) DO UPDATE SET
      items        = excluded.items,
      generated_at = excluded.generated_at
  `).bind(deviceId, date, JSON.stringify(items), new Date().toISOString()).run();
}
