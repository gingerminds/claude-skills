# Lots and sub-agents (section 6)

Goal: one valid findings file per lot, `findings/<lot>.json` (and `findings/<lot>-followup.json` after the follow-up), with the toolbox results the sub-agents need. Every Bash call starts with the `S=` line of SKILL.md (here unexpanded) and `RUN="<run_dir>"`. Run each command in its own Bash call.

## 1. Rows, lots and probes

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" fiches "$RUN"
```

Caches the fiche of each row in `fiches/` and prints `{"fiches", "truncated"}` (refs of fiches cut by Notion: the sub-agents say so).

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_notion.py" comments "$RUN"
```

Writes `comments.json`: the verdicts already visible in the comments of the integration, so that a run again does not repeat a comment. Prints `{"refs_with_verdicts"}`.

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" lots "$RUN"
```

- Writes `lots.json` and prints the lots (`id`, `kind`, `needs_toolbox`, `needs_server_facts`, `rows`), the rows excluded by reason and the coverage.
- Excluded before any evaluation: `standby` (never touched), `no_fiche`, `techno` (« Non applicable » with its reason), `phase` (J0 / J+7, left as they are), `no_tools` (not evaluable, with the missing tools).
- Order of the lots: `server`, the `code-*` lots, the `browser-*` lots, then their `-outils` siblings (rows that need "Audit web" or "Crawler").
- Show the lead one line: « <n> lots : server (<rows> lignes), code-securite (<rows>), ... ».

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" probes "$RUN"
```

Writes `probes.json`, the HTTP probes of the toolbox (fixed base, key pages, URLs and paths cited by the fiches of the rows that need HTTP), and prints `{"file", "base_url", "count", "from_fiches", "discarded"}`. `discarded`: the PHP files a key page or a fiche cites outside the routes of the application, never probed (a GET runs the script); tell the lead how many. Exit 2 when the target URL is missing: no toolbox web tool can run then.

Exit codes of the four: 2 for a missing file or variable (the message names the command to run first), 3 for Notion, 6 for an invalid `context.json` (fix it, then `context --check`).

## 2. Toolbox in the background

When the toolbox is ready (diagnostic step 6), start it with Bash `run_in_background`, with the credentials inline only when the session holds the Basic Auth credentials:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>' bash "$S/cqa-toolbox.sh" all "$RUN"
```

- It runs `http`, `gitleaks`, `assets`, `lighthouse`, `a11y`, `nu`, `yellowlab`, `testssl`, `redocly`, `schemathesis` and `crawl`, one after the other, then writes `tools/manifest.json`.
- The launcher reads `cqa_plan.py toolbox-env`, `repos-tsv` and `openapi-specs` itself: never call them by hand.
- Exit 0: all done. Exit 1: at least one tool failed; the manifest says which, with its reason. Exit 2: usage or environment (Docker, `context.json`).
- When the background task ends, read `tools/manifest.json` and tell the lead the tools that failed or were skipped, with their reason.

Resume (SKILL.md section 3) with a `tools/` folder but no `tools/manifest.json`: the collection was interrupted. Rebuild the manifest from what finished (a tool folder without `result.json` shows as failed):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" manifest "$RUN"
```

Then relaunch only the tools absent from it or failed, one at a time, each in its own background Bash call, with the same credentials rule:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>' bash "$S/cqa-toolbox.sh" run "$RUN" lighthouse
```

`run` returns the code of the tool (3: target unreachable or 401) and does not update the manifest: once the last one has ended, run `cqa_plan.py manifest "$RUN"` again. The toolbox counts as finished then. Without any `tools/` folder, launch `all` as above.

Toolbox not ready: no collection. Record each tool as skipped, so that the sub-agents and the synthesis know why:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" skip "$RUN" lighthouse "boîte à outils indisponible sur le poste"
```

Do it for `http`, `gitleaks`, `assets`, `lighthouse`, `a11y`, `nu`, `yellowlab`, `testssl`, `redocly`, `schemathesis` and `crawl`.

## 3. Dispatch

Sub-agent types: `gm:cqa-auditor` (code, tools, server) and `gm:cqa-browser` (browser). Prompt, exactly, then extra sentences on the same line:

```
Run dir: <run_dir>. Lot: <lot_id>. Follow-up: no. Follow your instructions. Plugin root: <dossier du plugin>.
```

- `Plugin root:` always: the absolute folder two levels above this skill (the parent of `scripts/`).
- `server` lot without "SSH serveur": add « SSH serveur indisponible. »; without "BDD": « BDD indisponible. ».
- Browser lot given to `gm:cqa-auditor` (no "Navigateur"): add « Navigateur indisponible : évaluer ce que le code et les outils permettent, unknown pour le reste. »
- Rerun (SKILL.md, section 10): replace `Follow-up: no.` by `Follow-up: no. Rerun: <NN>. Rows: CQ-3, CQ-12.` (the kept rows of that lot only). The agent writes `findings/<lot_id>-rerun-<NN>.json` with those rows only; validate it with `validate "$RUN" <lot_id> --rerun <NN>`.
- Relaunch after validation errors: add « Corriger ces erreurs de validation : <erreurs, séparées par des points-virgules>. »
- Never add a secret, a credential or a token to a prompt.

A sub-agent returns 10 lines at most: relay to the lead one short French line per lot (counts per verdict, refused commands, missing logins). Do not read the findings files yourself; `validate` checks them.

## 4. Order and limits

1. `server` first when there is one (always `gm:cqa-auditor`), with the `code-*` lots whose `needs_toolbox` is false: 4 sub-agents at most at once. Dispatch this first batch, then do the login pause (`references/browser.md`) while it runs.
2. The `browser-*` lots, one at a time, once the `server` lot is done (its `facts/server.json` serves the mixed rows): `gm:cqa-browser`, or the fallback of `references/browser.md`. A browser lot may run beside code lots, within the limit of 4.
3. The `-outils` lots once the toolbox has finished (its background task notified you), whatever its exit code. Toolbox not ready: dispatch them with the others; they read the skipped results and give `unknown` for those parts. `browser-*-outils` lots follow the browser rules.
4. Before each wave, recheck the target and SSH, and before each browser lot call `tabs_context_mcp` (SKILL.md, section 6).
5. When resuming, skip every lot whose first-round file already passes `validate`.

## 5. Validation

After each lot:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" validate "$RUN" <lot_id>
```

- It prints `{"ok", "errors"}`, exit 6 when there are errors: missing file, invalid JSON, ref outside the lot or twice, missing key, evidence that names a repo, a path or a tool the run does not hold.
- Errors: relaunch the same sub-agent once, with the errors in the prompt (section 3), then validate again. Still errors: go on; the rows without a valid finding count as `unknown` in the plan, and say so to the lead.
- `gm:cqa-browser` cannot run `validate`: you always do it.

## 6. Follow-up of the probes

Once every lot of the first round is validated:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" probes "$RUN" --followup
```

- Writes `probes-followup.json` and prints `{"file", "base_url", "count", "lots", "discarded"}`.
- `discarded`: URLs dropped because they leave the origin of the target or run a PHP file outside the routes of the application; tell the lead how many.
- `count` 0: no follow-up.

Otherwise, when the toolbox is ready (foreground, with the credentials inline only when the session holds the Basic Auth credentials):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
GM_AUDIT_AUTH_USER='<identifiant>' GM_AUDIT_AUTH_PASS='<mot de passe>' bash "$S/cqa-toolbox.sh" run "$RUN" http --followup
```

- Exit 0: the answers are in `tools/http-followup/`. Relaunch once each lot of `lots`, with `Follow-up: yes`, with the same agent type as its first round and the same limits.
- Then validate each follow-up file:

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" validate "$RUN" <lot_id> --followup
```

- Errors: one relaunch with the errors, as in section 5.
- Toolbox exit other than 0 (3: target unreachable or 401), or toolbox not ready: no relaunch. The plan caps the findings still waiting for probes at `unknown` (`ko` and `partial` stay); tell the lead.
- There is never a second follow-up.
