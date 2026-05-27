# Shasta by Transilience — branding pass design

> Brainstorm + ship, 2026-05-26. Apply a consistent "Shasta by
> Transilience" brand across every web surface (and later iOS) without
> abandoning the Quiet Paper / persimmon aesthetic that already exists.
>
> Status: **SHIPPED 2026-05-26** to `$SHASTA_DOMAIN` (web
> only; iOS deferred). Mid-flight pivot from mobius-mark + wordmark
> lockup to text-only "Shasta by Transilience" lockup after the
> localhost preview showed the hand-rolled lemniscate SVG didn't read
> cleanly. Updated below to reflect what actually shipped.

## Strategic frame

Shasta is the **product**; Transilience is the **company**. The two brands
live deliberately apart:

| | Transilience (corporate) | Shasta (product) |
|---|---|---|
| Surface | Rich Black (`#0A0A0B`) — dark-first, cinematic | Quiet Paper (`#FAF8F3`) — light-first, calm |
| Accent | Violet → crimson gradient + bumblebee yellow | Persimmon (`#D85F3B`) |
| Voice | Operator-grade, "a bit menacing" | Quiet, calm, relaxed |
| Where | `transilience.ai` (marketing) | `$SHASTA_DOMAIN` (product) |
| Why | Sells the company to a CISO | Hosts the analyst's daily work |

The customer journey: visit transilience.ai (dark, serious, "the company
that builds security operators") → sign up → land on
$SHASTA_DOMAIN (light, calm, paper-mode, "your daily analyst
workspace"). That contrast is a feature, not a bug — analysts already
stare at dark IDEs all day; a calm warm console is a relief.

The branding pass adds the **Transilience endorsement layer** on top of
Shasta's existing Quiet Paper palette. It does **not** repaint the
product.

## Decisions

1. **Keep Quiet Paper palette unchanged.** No edits to existing Tailwind
   tokens (`blue.*` = persimmon family; `slate.*` = warm neutrals;
   `white = #FFFCF6`). Severity tokens (`actNow`, `checkToday`, `watch`,
   `fyi`) untouched.
2. **Text-only "Shasta by Transilience" lockup.** No mobius mark. The
   localhost preview pivot showed the hand-rolled lemniscate didn't read
   cleanly at the sizes we needed, and bringing the upstream raster
   mobius PNG in would have re-introduced the gradient clash with
   persimmon. The brand pairing as pure typography is cleaner: `Shasta
   by Transilience` in the wordmark slot, `Full Stack Security OS` as
   the tagline, chapter heading (e.g. `Sign in.`, `Welcome.`) with a
   persimmon underline + period as the quirky accent.
3. **Wordmark-only for Shasta.** "Shasta" sits inside the
   `Shasta by Transilience` lockup as part of the brand pairing. No
   separate Shasta mark designed; revisit later if a custom mark
   (mountain silhouette, stylized S, etc.) earns its place.
4. **Light-mode only for v1.** No dark Shasta variant. If we need a
   dark surface for the iOS App Store screenshots or marketing later,
   we borrow Transilience corporate's dark palette directly — that's
   the intended bridge.
5. **The existing `ModuleRail` sidebar is the canonical brand surface.**
   Add the brand block to the top of the sidebar; reuse the sidebar's
   existing dark surface (`#3A342B`) and cream foreground (`#FAF8F3`)
   for the wordmark colours. No new top-bar component on authed
   routes.
6. **Pre-auth routes** (SignIn, Callback, PendingApproval) get a
   `<HeroLockup>` component — the brand pairing + tagline + chapter
   heading + per-route content (form, status, polling).

## Assets

None. The lockup is pure typography — no SVG assets, no raster, no
new files in `web/public/`. The existing favicon stays as-is for now;
a brand-aligned favicon (persimmon-tinted, no mobius) is deferred.

## Tailwind tokens

No new tokens. The existing palette (`blue.*` = persimmon, `slate.*` =
warm neutrals, `white = #FFFCF6`) covers every brand surface. The
sidebar uses the existing `#3A342B` background + `#FAF8F3` foreground;
the persimmon underline on the chapter heading uses `border-blue-500`
which is `#D85F3B`.

## Components — what shipped

### `<BrandLockup>` — tiny inline brand text

`web/src/components/BrandLockup.tsx`:

```tsx
export function BrandLockup({ className = "" }: { className?: string }) {
  return <span className={className}>Shasta by Transilience</span>;
}
```

Used inline wherever "Shasta by Transilience" appears in text. Caller
controls font size + tone via className.

### `<HeroLockup>` — pre-auth hero stack

Same file. Composes the three-line brand stack + persimmon-underlined
chapter heading + per-route children:

```
Shasta by Transilience       ← 26px (+20% from preview), font-semibold,
                                slate-900, tight tracking

The Full Stack Security OS   ← 19px (+20% from preview), slate-600

[Chapter.]                   ← 56px, bold, slate-900, persimmon
                                (border-blue-500) 4px bottom border,
                                period at the end ("Sign in.",
                                "Welcome.", "Pending review.")

{children}                   ← per-route content (form, copy, CTAs)
```

Used on `SignIn.tsx`, `PendingApproval.tsx`. `Callback.tsx` uses the
tinier `BrandLockup` directly (callback is a brief in-flight page,
not a hero moment).

### `<ModuleRail>` — brand block at top

Modifies `web/src/chat/ModuleRail.tsx`:
- Adds a brand block above the NavLink loop, inside the sidebar
- Block has 18px horizontal padding (matches nav-item padding) and
  bottom border `1px #4A4238` (matches existing email-row separator)
- Line 1: `Shasta by Transilience` — 14px, font-semibold, `#FFFCF6`
- Line 2: `FULL STACK SECURITY OS` — 10px, uppercase, 0.12em tracking,
  `#A89B89`
- The sidebar's existing dark surface (`#3A342B`) and cream foreground
  (`#FAF8F3`) carry the lockup — no new colours
- Active-state nav indicator (persimmon left border) unchanged

After this change, every authed route sees `Shasta by Transilience` +
the uppercase tagline at the top of the sidebar.

## Page-by-page branding map

| Page | Surface | Branding treatment |
|------|---------|-------------------|
| `/signin` | Cream paper | `<HeroLockup>` centred on page; sign-in buttons under tagline |
| `/auth/callback` | Cream paper | `<HeroLockup>` with a small "Signing you in…" caption + spinner |
| `/pending-approval` | Cream paper | `<HeroLockup>` + "Your account is pending approval" + contact link |
| `/welcome` | Cream paper | `<HeroLockup>` + welcome copy + "Connect a cloud →" CTA |
| `/` (chat) + every authed route | Sidebar + content | `<ModuleRail>` with brand block at top; content area unchanged |

Pre-auth routes use `<HeroLockup>` because there's no sidebar to anchor
the brand. Authed routes use the sidebar's brand block because the
sidebar is always visible.

## iOS (deferred — own sprint)

Captured here for completeness; not in scope for the web pass.

- **App icon**: monochrome cream mobius on persimmon background. Tested
  at 60×60 (home screen) and 1024×1024 (App Store). The mobius needs
  to read at the small size; we'll over-weight the stroke for the app
  icon variant.
- **Splash**: cream background with `<HeroLockup>`-equivalent in
  SwiftUI. Reuse the same tagline copy.
- **In-app header**: navigation bar with mobius + "Shasta" wordmark in
  ink. Sub-screens use SF Pro Display per iOS conventions; brand
  consistency comes from the mobius + persimmon accent, not from
  Futura/Jost (which would feel out of place on iOS).

Pulled out as a separate spec when we ship the iOS branding sprint.

## Acceptance criteria

- [ ] `transilience-mobius.svg` and `transilience-wordmark.svg` exist in
      `web/public/` and render cleanly at 16px through 256px
- [ ] `<BrandLockup>` component implemented with three sizes and three
      tones; unit-tested with vitest snapshots
- [ ] `<HeroLockup>` composes `<BrandLockup>` correctly and renders on
      all four pre-auth routes
- [ ] `<ModuleRail>` has the brand block at the top; visible on every
      authed route
- [ ] `web/tailwind.config.*` has the `brand` token group; no other
      tokens changed
- [ ] Favicon updated to the mobius SVG (browser tab shows persimmon
      mobius)
- [ ] Manual visual check: log in via Google, click through every
      authed route, confirm the brand block sits correctly in the
      sidebar and the content area is unchanged
- [ ] No regression in the existing `pnpm lint` baseline (~42 errors
      already documented as known dirty; don't add more)
- [ ] Deployed to `$SHASTA_DOMAIN` via `pnpm build` + S3 sync
      + CloudFront invalidation
- [ ] README screenshot capture (deferred from Phase 1 docs trio) is now
      unblocked — capture post-deploy

## Out of scope

- Custom Shasta mark (mountain / S / leaf etc.) — design later if it
  earns its place
- Dark-mode variant of Shasta surfaces — borrow from Transilience
  corporate when needed (App Store screenshots, marketing video)
- Animation on the mobius (Transilience corporate brand spec mentions a
  3-5s loop for marketing; Shasta product surface stays static)
- Typography overhaul — Shasta keeps its existing type stack; we don't
  introduce Futura/Jost just for the wordmark since the wordmark is a
  pre-rendered SVG path
- iOS app icon + splash + nav (separate sprint)
- Email + APNs push template branding (deferred — same lockup pattern,
  separate implementation)
- Transilience corporate site changes — out of repo scope
- Photography / hero imagery for the marketing surfaces — Shasta product
  has no hero imagery surface, by design (the warm paper does the work)

## Sequencing for the rest of the session

1. KK approves this spec
2. Hand-roll `transilience-mobius.svg`; lift `transilience-wordmark.svg`
   from upstream
3. Add `brand` Tailwind tokens
4. Build `<BrandLockup>` component + vitest snapshots
5. Build `<HeroLockup>` and wire into all four pre-auth routes
6. Update `<ModuleRail>` with the brand block
7. Update favicon
8. Local visual check
9. `pnpm build` + S3 sync + CloudFront invalidation
10. Open `$SHASTA_DOMAIN` in incognito, walk every route,
    confirm the brand pass landed
11. Capture screenshots of the branded UI → drop into README → commit
12. Tell KK we're ready for the Transilience team share
