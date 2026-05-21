# Check-title catalog — design

> Incremental change #2. Replaces the display-time generic-title heuristic
> with a curated `check_id → title` catalog served by the findings API.
> Created 2026-05-20.

## Problem

Shasta emits a per-*result* `title` on every finding — sometimes generic
("Unable to check PostgreSQL secure transport"), sometimes resource-specific
(`"PostgreSQL 'db1' require_secure_transport=off"`). The same `check_id`
carries different titles at different call sites; Shasta has no central
check registry with one canonical title per check.

Today `web/src/routes/TopRisks.tsx` (`genericizeTitle`) strips single-quoted
substrings from the *first* finding's title to derive a card title. This is
lossy: it depends on Shasta always quoting resource names, picks an arbitrary
finding, and produces nothing clean for titles that don't follow the pattern.

## Solution

A curated catalog mapping each of Shasta's **292** `check_id`s to one clean,
generic, human-authored title. The findings API attaches a `check_title`
field to every finding it returns; clients render that instead of deriving
their own.

## Components

### Catalog — single source of truth
`scripts/check_titles.py` — `CHECK_TITLES: dict[str, str]`, all 292 entries.
Authored by a slug→title transform for a first draft, then hand-curated
against Shasta's check code for a consistent house style.

### Distribution
Each Lambda bundles strictly one directory (no layers — project convention,
cf. the duplicated `anthropic_call.py`). `scripts/sync_check_titles.py`
copies the catalog module into `platform/lambda/findings_list/check_titles.py`
and `platform/lambda/findings_rollup/check_titles.py`. The copies are
committed and never hand-edited; re-run sync after any catalog edit. The
operation is idempotent.

### Read-time application
`findings_list` and `findings_rollup` set, per finding/group:

```
check_title = CHECK_TITLES.get(check_id) or _fallback_title(title)
```

`_fallback_title()` is the single-quote-strip heuristic ported to Python —
defense-in-depth for any `check_id` not yet catalogued. No DB migration, no
rescan; `check_title` is a pure function of `check_id`, so it stays
presentation, not stored data. Catalog edits take effect on next deploy.

`findings_summary` is untouched — it returns counts, not titles.

### Client changes
- Web: `Finding` type gains `check_title`; `TopRisks.tsx` consumes
  `f.check_title`; the client-side `genericizeTitle` is deleted.
- iOS: `Finding` model gains optional `check_title`, used where the title
  renders.

## Testing
- Catalog completeness: a test reads Shasta's `check_id` literals live and
  asserts `CHECK_TITLES` covers every one — no empty or placeholder values.
  The catalog cannot silently fall behind Shasta.
- Fallback: `_fallback_title()` returns sane output for an unknown id.
- Sync integrity: the two Lambda copies are byte-identical to the master.

## Out of scope
- `findings_summary` (counts only).
- Write-time storage of the title in a `findings` column.
- Framework mappings (incremental change #4).
