---
name: visual-regression
description: Sets up and runs visual (screenshot) regression testing with Playwright — detects/configures the tooling if absent, captures baselines, runs comparisons, and reports pixel diffs with a clear accept/reject call per snapshot. Also supports comparing two live URLs directly (no committed baseline needed) — the case of a rewritten/migrated repo with no relevant local history, checked against the still-live old site; invoke as `/gm:visual-regression <old-url> <new-url>` to go straight to that mode. Stack-agnostic (targets any rendered URL: Drupal, WordPress, Laravel, Vue/Nuxt, Django...). Manual invocation only. Use when the user asks to check for visual regressions, compare UI screenshots before/after a change or a migration, diff two environments/URLs visually, set up screenshot testing, or invokes /gm:visual-regression.
---

# Visual regression testing (Playwright)

Catch unintended UI/CSS changes by comparing screenshots of rendered pages against committed baselines. Tool: Playwright's built-in `toHaveScreenshot()` — no external SaaS, no extra account. Stack-agnostic: it drives a browser against a URL, so it works the same whether the app behind it is Drupal, WordPress, Laravel, Vue/Nuxt or Django.

## Mental model

- **Baselines are code, but not necessarily git-committed forever**: the reference PNGs (`*-snapshots/**` next to the specs, or a centralized `tests/visual/__screenshots__/`) are the contract, like a fixture — but committing every one straight to git isn't the only option and doesn't scale forever. See [Baseline storage & versioning](#baseline-storage--versioning) for when to keep committing vs. version them as releases instead.
- **Diff artifacts are ephemeral**: `playwright-report/`, `test-results/`, `blob-report/` are regenerated every run — gitignored, never committed.
- **Environment determinism matters**: font rendering and anti-aliasing differ across OS/GPU. Baselines generated on a dev laptop will flake against a CI run on Linux. Prefer generating and comparing in the **same environment** (ideally the official `mcr.microsoft.com/playwright` Docker image, or CI) — flag this explicitly if you detect baselines are about to be captured on a bare host.
- **Chromium only by default** — cross-browser visual matrices multiply flakiness and maintenance cost for little signal; only add Firefox/WebKit projects if the user asks.

## Two comparison modes

- **Mode A — baseline vs current code** (default; the rest of this doc unless stated otherwise): committed PNG baselines are the ground truth, `toHaveScreenshot()` tracks drift over time within the same repo. Requires a meaningful history to diff against.
- **Mode B — URL vs URL** ([Flow 4](#flow-4--url-vs-url-comparison-migration-audit)): compares two **live** URLs page-by-page in the same run, no committed baseline needed. Pick this when there's nothing relevant to diff against locally — typically a repo that just went through a full rewrite/major upgrade (new framework, new theme, replatforming), where the only trustworthy "before" is the still-reachable old site (old prod, a frozen staging, a snapshot env), not this repo's git history.

**Invocation shortcut** — `/gm:visual-regression <url_ancien_site> <url_nouveau_site>` (two URLs as arguments, in that order: reference first, target second) goes **straight to Mode B / Flow 4** with those as `REF_URL`/`TARGET_URL` — don't re-ask which mode or re-ask for the URLs. Still confirm the route list/pairing (see below) and any auth needed before running anything. With no arguments, or a single URL, ask which mode applies if it's not obvious from context; default to Mode A for an established repo with ongoing changes, suggest Mode B when the user mentions a migration/rewrite/new repo.

## Detect the project state

1. **Playwright already set up for visual testing** — a config with a `toHaveScreenshot` project/`expect` block, or existing `*-snapshots/` directories → reuse it, skip straight to [Compare](#flow-3--compare-day-to-day-use).
2. **Playwright present, but only for functional E2E** (no screenshot assertions) → add a dedicated `visual` project to the existing `playwright.config.*` rather than a parallel install.
3. **No Playwright at all** → run [Setup](#flow-1--setup-once). For **Mode B only**, full setup isn't required — Flow 4 needs just `@playwright/test` (or even plain `playwright`) plus `pixelmatch`/`pngjs`, no config/baseline scaffolding.
4. Check for an existing JS toolchain: root `package.json` (and its package manager: `package-lock.json`/`pnpm-lock.yaml`/`yarn.lock`), or a dedicated test subfolder convention already in place (`tests/e2e/`, `cypress/`, Laravel's `tests/Browser/`). Respect whatever convention already exists instead of inventing a new one; a backend-only repo with no JS tooling yet gets a minimal root `package.json` dedicated to Playwright.

## How to reach the running app

Apply `../../shared/runner.md` (make → docker compose → lando) to find how the project boots. Then:

- If one command reliably boots the full stack and blocks until ready, wire it as Playwright's `webServer` (`command`, `url`, `reuseExistingServer: !process.env.CI`) so `playwright test` starts it automatically.
- Otherwise, assume the app is already running locally and just ask the user for the base URL (`http://localhost:8080`, a Lando/DDEV URL, etc.) — set it as `use.baseURL`.
- The target doesn't have to be local at all: a deployed URL (a review app, staging, even prod) works just as well as `baseURL` — skip the runner entirely and use it as given. This is the common case for Mode B, and a valid choice in Mode A too (e.g. seeding baselines from staging on a fresh repo with no local env yet).
- Never guess a port/URL silently; a wrong baseURL produces a wall of false failures that looks like a real regression.

## Which pages/components to cover

Ask the user for the routes to cover if not obvious (recommend starting small: homepage + 2-3 pages that carry the most layout/design risk for the current change, not the whole sitemap — a large suite that's never reviewed just becomes noise). If Storybook is detected (`.storybook/`), mention it as an option for component-level visual coverage, but default to page-level unless the user wants component isolation.

**Mode B specifically**: the routes must exist on *both* sides with equivalent content (`/produit/123` on the old site must be the same product as `/produit/123` on the new one). A migration often renumbers/renames URLs — get the user to confirm the route pairing (or a redirect map if one exists) rather than assuming identical paths; a path mismatch reads as a 100%-different page and drowns the real diffs.

## Which breakpoints to cover

Cover breakpoints by default — a single fixed viewport misses the most common class of visual regression (a layout that breaks only on mobile/tablet).

- Detect the project's real breakpoints first: CSS custom media/`@media` queries, a framework config (Tailwind's `theme.screens`, Bootstrap's `$grid-breakpoints`, a `_variables.scss`), or an existing responsive-test setup. Reuse those numbers instead of inventing new ones.
- If none are found, fall back to a 3-point default: mobile (375×667), tablet (768×1024), desktop (1440×900) — confirm with the user before committing to it if the site's layout clearly varies a lot by breakpoint.
- Wire each breakpoint as its own Playwright project (its own `viewport`, e.g. `visual-mobile`/`visual-tablet`/`visual-desktop`) rather than looping viewports inside a single spec — keeps a failing breakpoint attributable in the report instead of buried in one aggregate result.
- **Mode B**: capture `REF_URL` and `TARGET_URL` at the same breakpoint set and the same device-scale-factor, so diffs stay comparable across the two domains.

## Baseline storage & versioning

Committed PNGs never get garbage-collected — every accepted diff replaces the file in the working tree, but git keeps every prior blob in history forever, so a long-lived repo's storage grows unbounded with binary noise. Baselines also go stale: a screenshot validated 6 months ago against a page that's since evolved organically isn't a meaningful "ground truth" anymore, and gating today's diff against it has no value.

- **Default — git-committed baselines** (as described in Flow 1/2): fine for a small suite (a handful of pages × breakpoints, total baseline weight staying in the low MBs). Keep doing this unless a trigger below applies.
- **Switch to release-based baselines** for a large suite (many pages × several breakpoints) or a repo where GitLab storage is already a concern: don't commit the PNGs to the working tree at all. Zip the current `*-snapshots/**` set and attach it as an asset to a tagged GitLab Release (e.g. `visual-baseline-2026-09-23`, bumped on each accepted set); keep only a small manifest in git (the release tag to use). Flow 3 downloads and unzips the latest release's asset before comparing; accepting a diff (Flow 2) creates a new release + asset instead of a new git blob, and the superseded release/asset can be deleted — bounded storage instead of an ever-growing history.
- **Either way, treat a baseline set as having a shelf life**: if its date (commit date, or release tag date) is older than roughly a quarter, flag it to the user before trusting it as ground truth — offer to recapture (Flow 2) rather than silently comparing against a possibly-outdated reference. A failing diff against a 6-month-old baseline says as much about staleness as about a real regression.
- Ask the user which strategy fits during setup (Flow 1) if the repo/team has no existing convention; don't switch strategies mid-project without confirming — it changes what "accepting a diff" actually does.

## Flow 1 — Setup (once)

1. Install `@playwright/test` as a devDependency with the detected package manager; run `npx playwright install --with-deps chromium`.
2. Create/extend `playwright.config.ts` with one `visual` Playwright project **per breakpoint** decided above (each with its own `viewport`), sharing `expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.02, threshold: 0.2, animations: 'disabled' } }` and the `webServer`/`baseURL` from the previous step. These are two different knobs, don't conflate them: `threshold` is the per-pixel color-difference sensitivity — raise it first (e.g. `0.3`) if failures are pure font-rendering/anti-aliasing noise; `maxDiffPixelRatio` is the overall tolerated diff surface — only raise this (or scope the snapshot to exclude the text body) for pages where routine copy edits are expected and shouldn't gate the check. Adjust either only if the user asks or the actual failure pattern justifies it, and say which knob and why.
3. Create `tests/visual/` with one spec per page/flow, using `await expect(page).toHaveScreenshot('<name>.png')`. Disable animations/transitions and mask genuinely dynamic content (timestamps, ads, carousels) with `mask: [...]` rather than excluding whole pages.
4. Add npm scripts: `test:visual` (`playwright test tests/visual`) and `test:visual:update` (`playwright test tests/visual --update-snapshots`).
5. `.gitignore`: add `playwright-report/`, `test-results/`, `blob-report/`. With the git-committed strategy, **do not** ignore the snapshots directory — it must stay versioned, same logic as `.archi/` in `/gm:archi-c4`. With the release-based strategy, ignore the snapshots directory instead — it's restored from the Release asset, not tracked in git.
6. Project `CLAUDE.md` (root, create if absent): insert idempotently (skip if the tags already exist), adjusting the wording below if the release-based strategy was chosen:

   ```markdown
   <!-- gm:visual-regression -->
   ## Non-régression visuelle
   - Comparaison : `npm run test:visual` (rapport dans `playwright-report/`, à ouvrir avec `npx playwright show-report`).
   - Après un changement UI volontaire : valider visuellement le diff puis `npm run test:visual:update` sur les tests concernés, et committer les nouvelles baselines.
   - Les baselines (`tests/visual/**/*-snapshots/`) sont versionnées ; `playwright-report/` et `test-results/` ne le sont pas.
   <!-- /gm:visual-regression -->
   ```

## Flow 2 — Capture baselines

1. Run `test:visual:update` for the new/targeted specs only (`--update-snapshots -g "<name>"` — never blanket-update the whole suite without checking what changed).
2. Open a couple of the generated PNGs and sanity-check them (not a blank/error/loading-state page) before committing — a bad baseline silently becomes "correct" forever.
3. Persist the baselines per the chosen [storage strategy](#baseline-storage--versioning): a dedicated git commit for git-committed baselines, or a new tagged Release + asset upload for release-based ones — either way, separate from the code change when possible.

## Flow 3 — Compare (day-to-day use)

1. With the [release-based storage strategy](#baseline-storage--versioning): download and unzip the latest baseline Release's asset into the snapshots directory first — skip this step entirely for the git-committed strategy, where the baselines are already in the working tree.
2. Run `npm run test:visual` (or the project's runner-prefixed equivalent).
3. On failures, read the report rather than just the pass/fail count: each failing snapshot has an `-actual.png`/`-expected.png`/`-diff.png` triplet under `test-results/`, and `npx playwright show-report` gives the side-by-side.
4. Report, per failing snapshot: page/component name, diff percentage if available, and a one-line description of what visibly changed (layout shift, color, missing element...).
5. For each failure, ask the user to call it: **intentional change** → update just that snapshot (Flow 2, targeted); **regression** → treat as a bug (point to `/gm:review` or the relevant dev skill), don't update the baseline. On a long-form/CMS content page, a routine copy edit alone can push `maxDiffPixelRatio` past the limit with no real visual regression — check whether the diff highlights only the edited text block (expected, safe to accept) or cascades into layout shift elsewhere on the page (real regression from reflow) before calling it; see the `threshold` vs. `maxDiffPixelRatio` note in Flow 1.
6. Never auto-accept a diff without the user looking at it — a silently updated baseline defeats the whole point of the check.

## Flow 4 — URL vs URL comparison (migration audit)

One-shot audit, not an ongoing baseline: validates that a rewritten/migrated site still matches the reference visually, without needing this repo's history.

1. Get both URLs: already given as invocation arguments (`/gm:visual-regression <ref> <target>`) → use them as-is, first = **reference** (old/before), second = **target** (new/after). Otherwise ask: reference (old prod, a frozen staging, whatever is still reachable) and target (local via the runner, a review app, or prod). Get credentials via env vars if either is behind HTTP basic auth or a login wall — never hardcode or commit them.
2. Install just `@playwright/test` (or `playwright` if no test runner is wanted) plus `pixelmatch` and `pngjs` as devDependencies — no `playwright.config.ts` scaffolding needed for a one-shot script.
3. Write a small script (not a persistent spec suite) that, **for each paired route, in the same run**: opens a fresh, isolated `browser.newContext()` per URL (no shared cookies/session bleed between the two), navigates to `REF_URL + route`, screenshots (`fullPage: true`); does the same for `TARGET_URL + route`; then diffs the two PNGs with `pixelmatch`, writing a `<route-slug>.diff.png` plus the diff-pixel ratio into a report folder, e.g.:

   ```ts
   const [refPng, newPng] = [PNG.sync.read(refBuffer), PNG.sync.read(newBuffer)];
   const diff = new PNG({ width: refPng.width, height: refPng.height });
   const diffPixels = pixelmatch(refPng.data, newPng.data, diff.data, refPng.width, refPng.height, { threshold: 0.1 });
   const diffRatio = diffPixels / (refPng.width * refPng.height);
   ```

   Mask/crop genuinely-expected differences (a domain-specific cookie banner, a date, an A/B-test badge, **a rotating/autoplay carousel or slider in the hero area**) before diffing rather than letting them dominate the ratio — same discipline as Mode A's `mask: [...]`. A live carousel captured via two independent browser contexts can land on different slides/transition frames between the ref and target run even when the underlying slide content is identical — locate the carousel's pixel bounds and blank/mask that region before computing the ratio, rather than reporting it as a regression. Mismatched image dimensions (common with different scrollbar/viewport behavior across domains) count as a full diff for that route unless normalized first (same viewport size, same device-scale-factor on both contexts).
4. Report a table — route, diff ratio, one-line description of what visibly changed — flag anything above the same tolerance used in Mode A (`~2%` by default) as "à vérifier". Cross-domain diffing is inherently noisier than same-repo diffing (different CDN fonts, cache states, live/rotating content, consent banners) — call this out and don't over-claim a real regression from a single run; re-run a borderline route before flagging it, and always eyeball the flagged route's before/after screenshots yourself (not just the diff percentage) before reporting it as a real change — a masked or re-checked false positive (e.g. a rotating carousel) should be downgraded and explained, not left as a flagged regression.
5. This audit's output is a report, not a baseline — don't auto-commit anything from it. Once the migration is validated, offer to **seed Mode A baselines from the target** (Flow 2, `baseURL` = the now-validated target) so the new repo gets ongoing regression tracking going forward.

## Non-goals

- Don't add a multi-browser (Firefox/WebKit) matrix by default — flakiness/cost outweigh the signal unless asked. Breakpoints are different: cover them by default (see [Which breakpoints to cover](#which-breakpoints-to-cover)), but stick to the project's defined breakpoints or the 3-point fallback — don't invent a dozen arbitrary widths.
- Don't wire this into CI (`.gitlab-ci.yml`) or any deploy pipeline without explicit confirmation — pipeline changes are a shared, hard-to-reverse edit.
- Don't update a baseline the user hasn't visually reviewed.
- Don't capture a huge page inventory by default; start focused, extend on request.
- Don't treat a single Mode B run as a hard pass/fail gate — cross-domain/cross-environment noise is real; a human calls each borderline diff.
- Don't assume route paths match 1:1 between reference and target without confirming — ask, or use a redirect map if one exists.
