---
name: review
description: Reviews a code change before merge — correctness bugs, security, performance/cacheability, coding standards, and test coverage — and produces a structured, severity-ranked verdict with concrete actions. Tuned for Drupal/PHP + GitLab workflows. Use when the user asks to review a diff, a branch, a fix, or a change before preparing a merge request, or invokes /clara:review.
---

# Clara — Code Review

Review a focused code change and return a verdict the author can act on. Quality and correctness, not style nitpicks. Be direct: state what is wrong, why, and the exact fix.

## Scope first

Determine what to review, in this order:

1. **A GitLab MR (URL or number)** the user gave. Pull it with `glab` — no manual copy needed:
   ```bash
   GITLAB_HOST=<host> glab mr view <id> -R <group/project>   # title, description, branches, the Mantis link
   GITLAB_HOST=<host> glab mr diff <id> -R <group/project>   # the diff to review
   ```
   The `-R` and host come from the URL (`https://<host>/<group/project>/-/merge_requests/<id>`).
2. An explicit diff, file, or branch the user named.
3. Otherwise the current branch vs its base (`git diff <base>...HEAD`, base usually `develop` or `main`).
4. Otherwise the working-tree diff (`git diff` / `git diff --cached`).

Read the changed lines **and** enough surrounding code to judge them (the function, its callers, the form/route/service it touches). Never review a hunk in isolation when the bug could live in the context.

## Load the ticket

Correctness is judged against intent, so get the ticket. Extract the Mantis id from the MR description or the branch name (`fix/<id>-slug`), then:

```bash
scripts/mantis-issue.sh <id>    # prints summary, description, steps-to-reproduce, notes
```

It needs `MANTIS_URL` + `MANTIS_TOKEN` in the env. If they're unset or it exits non-zero (exit 2 = no creds, 3 = API error), **ask the user to paste the ticket** rather than reviewing intent blind.

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

**Noise control** — report the signal, not every observation:
- **Nit** findings are *folded by default*: collapse them into a single trailing line (`Nits: <a>; <b>; <c>`), or omit entirely if the change is clean. Only expand them when the user asks for a full pass.
- **Minor** is one line each (symptom + fix inline) — no `Why`/`Action` block.
- Reserve the full `Why` / `Action` block for **Blocker/Major** only.
- Don't pad: no finding without a concrete consequence. If you can't name what breaks, it's a Nit at most.

## Output format

Lead with the one-line verdict (ship / ship with nits / needs changes). Keep it tight — only show what carries information.

**Dimension table** — list only the dimensions that are ⚠️/❌; collapse all the clean ones into a single line below the table (`✅ Security, Cacheability, Tests`). If *everything* passes, skip the table and just give the verdict.

| Dimension | Verdict |
|---|---|
| <only failing/at-risk dimensions> | ⚠️ / ❌ + one line |

Then the findings, most severe first. Full block for **Blocker/Major**; one line for **Minor**; **Nits** folded into a single trailing line (see Noise control):

```
- [Blocker|Major] <symptom> (<file>:<line>)
  Why: <reason>
  Action: <exact edit or command — copy-pasteable, never "investigate X">
- [Minor] <symptom> (<file>:<line>) — <fix inline>
Nits: <a>; <b>; <c>
```

Finish only when there's something to say: **Out-of-scope** notes (latent issues for a separate ticket) and **manual UAT steps** when relevant — omit either section if empty rather than writing "none". When the verdict is ship / ship with nits, point the author to `/clara:merge-request` to open the MR.

## Non-goals

- Don't rewrite the architecture from a diff. Recommend, scope, and let the author decide.
- Don't flag pre-existing behavior the change didn't introduce — note it as out-of-scope instead.
- Don't approve what you couldn't verify; say what's unverified.
