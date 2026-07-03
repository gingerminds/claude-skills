---
name: security
description: Audits a project's dependency and infrastructure security — runs composer/npm audit, checks CMS/framework security advisories, reviews the Docker setup, ranks every finding by exploitable criticality, and either applies safe fixes or gives exact step-by-step remediation. Stack-agnostic: pulls stack-specific advisory checks (e.g. Drupal SAs) from the loaded stack/ resource. Use when the user asks for a security audit, a CVE/vulnerability check, a dependency-security pass, or invokes /gm:security.
---

# Security Audit

Run a dependency- and infrastructure-level security audit and return a prioritised, actionable report. Real exploitability over raw advisory counts. Be direct: state what is vulnerable, how bad it is *in this project*, and the exact remediation.

This is a **defensive** audit — find and fix weaknesses, not exploit them.

## Scope first

Detect the stack before running anything — don't run an audit for a tool the project doesn't use. From the project root:

```bash
ls composer.json composer.lock package.json package-lock.json pnpm-lock.yaml yarn.lock 2>/dev/null
ls Dockerfile docker-compose.yml docker-compose.yaml compose.yaml 2>/dev/null
```

Then identify:

1. **PHP** — `composer.json` / `composer.lock` present.
2. **Stack / CMS** — detect via `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`, then load `${CLAUDE_SKILL_DIR}/../../stack/<stack>/MAIN.md` for the **security** nature. This pulls the stack's advisory specifics (e.g. Drupal: `drush pm:security`, Drupal.org SA feeds, core-version check) on top of the generic audit below. No known stack → stay generic.
3. **JS** — which lockfile is present picks the runner (`package-lock.json` → npm, `pnpm-lock.yaml` → pnpm, `yarn.lock` → yarn). Don't run `npm audit` against a pnpm project.
4. **Docker** — any Dockerfile / compose file.

Find the project runner via `${CLAUDE_SKILL_DIR}/../../shared/runner.md` (priority `make` → `docker compose` → `lando`). Run composer/package-manager audits **inside** the runner if one exists (the host PHP version rarely matches), but `*audit` against the committed lockfile is fine from the host too.

State up front which ecosystems you found and will audit; skip the rest explicitly.

## Run the audits

Prefer real signals over guesswork. Run what applies, capture the output, never invent advisory IDs.

**Two things are mandatory and complementary — do BOTH, never just one:**

1. **Read the package/lock files** (`composer.lock`, `package-lock.json`/`pnpm-lock.yaml`/`yarn.lock`) to extract the *exact installed versions* of every package. The lockfile is the ground truth for "what is actually installed". But note: **a lockfile contains NO CVE/advisory data** — only versions and an optional `abandoned` flag. Reading it is necessary but never sufficient.
2. **Run the real audit** (`composer audit`, `npm audit`, plus any stack tool such as `drush pm:security` on Drupal) which fetches the **advisory database over the network** and maps installed versions → CVE/GHSA/SA ids. This is where the actual vulnerability signal comes from.

Reading the lockfile alone gets you hygiene findings (abandoned, pre-release versions) but will MISS every real CVE. Always pair the two: extract versions from the lock, then cross-reference them against advisories.

### Where to run — runner first, then host, then offline fallback

Try in this order; don't stop at the first failure, escalate to the next:

1. **Inside the runner** (`<runner> composer audit`, `<runner> npm audit`, and any stack tool such as `<runner> drush pm:security`) — the bootstrapped, network-capable environment. Best signal. Start it if it isn't up. Resolve `<runner>` via `shared/runner.md`.
2. **On the host** — if the runner is down (Docker not running, runner fails to start), run `composer audit --locked` / `npm audit` directly on the host. The host PHP version may differ, but `--locked` audits the committed lockfile so the result is still valid.
3. **Offline fallback via `WebFetch`** — if the environment has **no network from the shell** (sandboxed `composer audit`/`npm audit` fail with curl errors), DON'T give up and DON'T claim "0 vulnerabilities". Extract installed versions from the lockfile (see below), then pull the advisory feeds with `WebFetch` (a separate network path that often works when the shell is sandboxed) and cross-reference by hand — the loaded stack resource gives the stack-specific feeds (e.g. Drupal.org SA RSS).

The goal is to get **real material** one way or another. A "couldn't run anything" report is a last resort, not a first answer — exhaust runner → host → WebFetch before concluding.

### PHP / Composer

First, extract the exact installed versions from the lockfile (works with zero network, host or runner):

```bash
# All packages + versions + abandoned/pre-release flags
php -r '$l=json_decode(file_get_contents("composer.lock"),true);
foreach(($l["packages"]??[]) as $p){
  $ab=!empty($p["abandoned"])?" [ABANDONED]":"";
  $pre=preg_match("#(alpha|beta|-rc|dev-)#i",$p["version"])?" [pre-release]":"";
  echo str_pad($p["name"],45)." ".$p["version"].$ab.$pre."\n";
}'
```

The loaded stack resource may add stack-specific extraction (e.g. Drupal core + contrib filtering). Then run the real audit:

```bash
composer audit --locked --format=plain     # audits composer.lock against the PHP advisories DB
composer audit --locked --format=json       # machine-readable, for parsing severity/CVE/affected versions
```

`--locked` audits what is actually committed (`composer.lock`), not just the resolved tree. Each advisory carries a CVE/GHSA id, affected version range, and a link — keep these for the report.

### CMS / framework advisories (stack-specific)

Some ecosystems don't publish all advisories to packagist/npm and need their own tooling and feeds — **apply the loaded stack resource here**. For example, Drupal contrib SAs are **not** on packagist.org (they live on Drupal.org), so a packagist-only audit gives a false "all clear"; `stack/drupal/security.md` covers `drush pm:security`, the core-version EOL check, and the Drupal.org SA RSS feeds for the offline cross-reference. If no stack resource was loaded, note that only the generic `composer audit` / `npm audit` coverage applies.

### JavaScript

Use the runner that matches the lockfile:

```bash
npm audit --omit=dev            # or: npm audit --json
pnpm audit --prod               # or: pnpm audit --json
yarn npm audit --environment production   # yarn berry
```

Separate **prod** from **dev** dependencies — a high-severity CVE in a build-only devDependency is a far lower real risk than the same in shipped runtime code. Note which side each finding is on. For a compiled asset/theme pipeline (gulp/webpack/postcss/sass compiled to static CSS/JS), the *entire* npm tree is effectively build-time — flag those findings as Low real risk and say why, but still run the audit.

If `npm audit` can't reach the network, read `package.json` + `package-lock.json` for the installed versions and cross-reference notable packages against the **GitHub Advisory Database** (`WebFetch https://github.com/advisories?query=<package>`) rather than skipping the JS side entirely.

### Docker configuration

If a Dockerfile / compose file exists, audit the *configuration* (this overlaps `/gm:docker-devops` — reuse its hardening checklist rather than re-deriving it). Check for:

- **Base image**: floating tags (`:latest`, unpinned), or a base that is itself EOL / vulnerable.
- **Privilege**: container running as `root` (no `USER`), `privileged: true`, added capabilities, `--privileged`.
- **Docker socket**: `/var/run/docker.sock` bind-mounted into a container (container-escape surface).
- **Secrets**: credentials in `ENV`/`ARG`, hardcoded in compose, or copied into image layers; `.env` committed.
- **Exposed surface**: ports bound to `0.0.0.0` that should be internal; admin/DB ports published needlessly.
- **Filesystem**: no `read_only`, writable mounts that needn't be, host paths mounted broadly.
- **Image scan** (if a scanner is available): `docker scout cves <image>` or `trivy image <image>` / `trivy config .` — report OS/library CVEs in the built image. If no scanner is installed, say so; don't fabricate results.

## Prioritise by criticality

Rank every finding — advisory counts are noise, *exploitable* findings are signal. Tag each with one level, judged **in context**, not by the raw CVSS alone:

- **Critical** — remotely exploitable on shipped/runtime code, RCE, auth bypass, exposed secret, container escape. Fix now.
- **High** — serious vuln on a reachable runtime path, or a privilege/secret issue gated by some condition.
- **Medium** — real but constrained: needs auth, hard-to-reach path, or limited to a prod-but-low-exposure surface.
- **Low** — dev-only dependency, theoretical, or requires already-elevated access.
- **Info** — hygiene (unsupported module, unpinned base image, EOL minor) with no named CVE yet.

Context that moves a finding up or down: prod vs dev dependency; runtime-reachable vs build-only; internet-facing vs internal; whether a fixed version actually exists yet; whether the vulnerable code path is even used. **Say why** you placed each finding where you did when it differs from the advisory's nominal severity.

## Remediate

For each finding, give one of two things — never a vague "update your dependencies":

**A. Propose the fix** (when it's safe and mechanical):

```bash
composer require <vendor>/<package>:^X.Y      # bump to the fixed version (scope to the affected package)
npm audit fix                                  # non-breaking, when it resolves the advisory
```

The loaded stack resource gives stack-specific remediation (e.g. Drupal's `composer update drupal/core-recommended --with-dependencies`, the `update.php`/config-sync steps). Offer to apply these to the working tree when the user wants. After applying, **re-run the audit** to confirm the finding clears and nothing else broke (build/tests if quick).

**B. Give the marche à suivre** (when the fix is breaking, risky, or has no clean version yet):

- A major version bump with BC breaks → outline the migration steps, what to test, what likely breaks.
- No patched release exists → the mitigation (config workaround, WAF rule, disable the feature, pin + watch the advisory), plus what to monitor.
- A framework/CMS update needing a migration step (e.g. Drupal `update.php` / config sync) → the ordered steps, with the DB-backup-first reminder.
- An abandoned/unsupported package → replace, fork-and-patch, or remove — with the trade-offs.

Be explicit about **breaking-change risk**: don't propose a blind `composer update` that drags the whole tree forward. Scope every bump to the affected package(s) and their needed deps.

## Output format

Lead with a one-line headline: counts by severity and the single most urgent action. Then:

**Summary table** — one row per ecosystem audited:

| Ecosystem | Audited | Critical | High | Medium | Low | Info |
|---|---|---|---|---|---|---|
| Composer (PHP/CMS) | ✅ | … | … | … | … | … |
| npm (prod) | ✅ | … | … | … | … | … |
| Docker config | ✅ | … | … | … | … | … |

Then findings, **most critical first**. Full block for Critical/High, one line for Medium/Low, fold Info into a trailing line:

```
- [Critical] <package/component> — <vuln> (CVE/GHSA/SA-id)
  Affected: <version range> · Installed: <current> · Fixed in: <version | none yet>
  Risk here: <why this severity in THIS project — reachable? prod? exposed?>
  Fix: <exact command, or numbered marche à suivre if breaking>
- [Medium] <component> — <vuln> (<id>) → <one-line remediation>
Info: <unsupported module X>; <unpinned base image Y>; <EOL core minor Z>
```

Close with **what couldn't be verified** — and be specific about which rung of the runner → host → WebFetch chain you reached and what's still pending (e.g. "RSS cross-check done, but a live `composer audit` for the full contrib history is still owed"). When there are fixes to ship, point the user at `/gm:review` → `/gm:merge-request` to land them.

## Non-goals

- **Not an offensive / pentest tool.** No exploitation, no payloads, no live-target probing — codebase and dependency audit only.
- Don't report raw advisory dumps — rank, dedupe, and contextualise. A wall of `npm audit` output is the input, not the deliverable.
- Don't claim a check passed unless it actually ran; mark unavailable tools "(indispo)".
- Don't blind-bump the whole dependency tree to silence one advisory. Scope every change.
- Don't flag app-level code bugs here (injection, XSS in custom code) — that's `/gm:review`'s job; note them as out-of-scope if you spot them.
