---
name: visual-regression
description: Sets up and runs visual (screenshot) regression testing with Playwright — detects/configures the tooling if absent, captures baselines, runs comparisons, and reports pixel diffs with a clear accept/reject call per snapshot. Also supports comparing two live URLs directly (no committed baseline needed) — the case of a rewritten/migrated repo with no relevant local history, checked against the still-live old site; invoke as `/gm:visual-regression <old-url> <new-url>` to go straight to that mode. Stack-agnostic (targets any rendered URL: Drupal, WordPress, Laravel, Vue/Nuxt, Django...). Manual invocation only. Use when the user asks to check for visual regressions, compare UI screenshots before/after a change or a migration, diff two environments/URLs visually, set up screenshot testing, or invokes /gm:visual-regression.
---

# Visual regression testing (Playwright)

Catch unintended UI/CSS changes by comparing screenshots of rendered pages against committed baselines. Tool: Playwright's built-in `toHaveScreenshot()` — no external SaaS, no extra account. Stack-agnostic: it drives a browser against a URL, so it works the same whether the app behind it is Drupal, WordPress, Laravel, Vue/Nuxt or Django.

## Mental model

- **Baselines are code**: the reference PNGs (`*-snapshots/**` next to the specs, or a centralized `tests/visual/__screenshots__/`) are committed to the repo — they're the contract, like a fixture.
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

## Flow 1 — Setup (once)

1. Install `@playwright/test` as a devDependency with the detected package manager; run `npx playwright install --with-deps chromium`.
2. Create/extend `playwright.config.ts` with a `visual` project: fixed `viewport`, `expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.02, animations: 'disabled' } }` (adjust the ratio only if the user asks for stricter/looser), and `webServer`/`baseURL` from the previous step.
3. Create `tests/visual/` with one spec per page/flow, using `await expect(page).toHaveScreenshot('<name>.png')`. Disable animations/transitions and mask genuinely dynamic content (timestamps, ads, carousels) with `mask: [...]` rather than excluding whole pages.
4. Add npm scripts: `test:visual` (`playwright test tests/visual`) and `test:visual:update` (`playwright test tests/visual --update-snapshots`).
5. `.gitignore`: add `playwright-report/`, `test-results/`, `blob-report/`. **Do not** ignore the snapshots directory — it must stay versioned, same logic as `.archi/` in `/gm:archi-c4`.
6. Project `CLAUDE.md` (root, create if absent): insert idempotently (skip if the tags already exist):

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
3. Commit the baselines with a dedicated commit (per the user's commit conventions), separate from the code change when possible.

## Flow 3 — Compare (day-to-day use)

1. Run `npm run test:visual` (or the project's runner-prefixed equivalent).
2. On failures, read the report rather than just the pass/fail count: each failing snapshot has an `-actual.png`/`-expected.png`/`-diff.png` triplet under `test-results/`, and `npx playwright show-report` gives the side-by-side.
3. Report, per failing snapshot: page/component name, diff percentage if available, and a one-line description of what visibly changed (layout shift, color, missing element...).
4. For each failure, ask the user to call it: **intentional change** → update just that snapshot (Flow 2, targeted); **regression** → treat as a bug (point to `/gm:review` or the relevant dev skill), don't update the baseline.
5. Never auto-accept a diff without the user looking at it — a silently updated baseline defeats the whole point of the check.

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

- Don't add a multi-browser (Firefox/WebKit) or multi-viewport matrix by default — flakiness/cost outweigh the signal unless asked.
- Don't wire this into CI (`.gitlab-ci.yml`) or any deploy pipeline without explicit confirmation — pipeline changes are a shared, hard-to-reverse edit.
- Don't update a baseline the user hasn't visually reviewed.
- Don't capture a huge page inventory by default; start focused, extend on request.
- Don't treat a single Mode B run as a hard pass/fail gate — cross-domain/cross-environment noise is real; a human calls each borderline diff.
- Don't assume route paths match 1:1 between reference and target without confirming — ask, or use a redirect map if one exists.
