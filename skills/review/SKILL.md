---
name: review
description: Reviews a code change before merge — correctness bugs, security, performance/cacheability, coding standards, and test coverage — and produces a structured, severity-ranked verdict with concrete actions. Tuned for Drupal/PHP + GitLab workflows. Use when the user asks to review a diff, a branch, a fix, or a change before preparing a merge request, or invokes /clara:review.
---

# Clara — Code Review

Review a focused code change and return a verdict the author can act on. Quality and correctness, not style nitpicks. Be direct: state what is wrong, why, and the exact fix.

## Scope first

Determine what to review, in this order:

1. An explicit diff, file, or branch the user named.
2. Otherwise the current branch vs its base (`git diff <base>...HEAD`, base usually `develop` or `main`).
3. Otherwise the working-tree diff (`git diff` / `git diff --cached`).

Read the changed lines **and** enough surrounding code to judge them (the function, its callers, the form/route/service it touches). Never review a hunk in isolation when the bug could live in the context.

## Review dimensions

Assess each, and only report what applies:

- **Correctness** — Does it do what the ticket/intent says? Walk the actual control flow. For regressions, confirm the change addresses the root cause, not a symptom. When feasible, **verify empirically** (build the form, call the service, run the path) rather than asserting.
- **Security** — Input validation, access checks, SQL/XSS/CSRF, secrets, privilege boundaries. Flag anything that widens attack surface.
- **Performance & cacheability** (Drupal) — Cache contexts/tags/max-age, render cache, N+1 entity loads, entity queries vs full loads, batch/queue for large sets.
- **Standards** — Match the **local idiom** of the file (don't impose DI on a fully procedural `.module`, etc.) and the `Drupal,DrupalPractice` standard. Run the linters (see below) rather than eyeballing.
- **Tests** — Is new logic covered? If the project has no test infra, say so and recommend (don't fabricate a lone untestable test); document manual UAT steps instead.

## Run what you can

Prefer real signals over opinion. Prefix with the project's runner (`lando`, `ddev`, or `docker compose exec <svc>`) to run linters and quick probes:

```bash
lando php -l <file>
lando phpcs --standard=Drupal,DrupalPractice <path>
```

Report a check as passed only after it actually ran. If a tool is absent, say "(indispo)" — don't claim it passed.

## Severity

Tag every finding with one level; the verdict follows mechanically:

- **Blocker** — must fix before merge (data loss, security hole, broken feature).
- **Major** — should fix (correctness or perf bug, missing access check).
- **Minor** — worth fixing (edge case, weak cacheability, fragile assumption).
- **Nit** — optional (style, naming).

Verdict: any Blocker/Major → **needs changes**; only Minor/Nit → **ship with nits**; none → **ship**.

## Output format

Lead with the one-line verdict (ship / ship with nits / needs changes). Then:

| Dimension | Verdict |
|---|---|
| Correctness | ✅ / ⚠️ / ❌ + one line |
| Security | … |
| Cacheability | … |
| Standards | … |
| Tests | … |

Then findings, most severe first:

```
- [Blocker|Major|Minor|Nit] <symptom> (<file>:<line>)
  Why: <reason>
  Action: <exact edit or command — copy-pasteable, never "investigate X">
```

Finish with **Out-of-scope** notes (dead code, latent issues you spotted but that belong in a separate ticket) and, when relevant, **manual UAT steps**. When the verdict is ship / ship with nits, point the author to `/clara:merge-request` to open the MR.

## Non-goals

- Don't rewrite the architecture from a diff. Recommend, scope, and let the author decide.
- Don't flag pre-existing behavior the change didn't introduce — note it as out-of-scope instead.
- Don't approve what you couldn't verify; say what's unverified.
