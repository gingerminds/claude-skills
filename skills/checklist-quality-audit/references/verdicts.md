# Verdicts and findings

Shared reference of the PRE-MEL audit. The sub-agents `gm:cqa-auditor` and `gm:cqa-browser` follow it to write their findings; the orchestrator uses it to read them and to explain the plan to the lead.

The contract is the JSON Schema `skills/checklist-quality-audit/schemas/finding.schema.json` (path from the plugin root). `cqa_plan.py validate` checks every findings file against it, and `cqa_plan.py plan` applies the guardrails below. When this page and the schema disagree, the schema wins.

## Findings file

- First round: `findings/<lot>.json` in the run directory. Follow-up round: `findings/<lot>-followup.json`.
- Shape: `{"lot": "<lot id>", "findings": [<finding>, ...]}`, valid JSON in UTF-8.
- Always one finding for every row of the lot: every ref of the lot entry in `lots.json`, no other ref, never the same ref twice. A ref outside the lot or given twice is rejected, and the row counts as not evaluated.
- The follow-up file is a complete lot document too, with one finding for every row of the lot. Copy unchanged the first-round finding of every row that asked for no probe.
- Every finding has the nine keys below, none missing, and no other key than the optional `needs_scenarios`, `tool_dates` and `in_repo`. `validate` refuses a finding that holds any other key, a `source`, a `guardrail` or a `document` evidence (the code writes them, never a lot), and a file whose `lot` is not the lot it is validated for.

| Key | Type | Rule |
|---|---|---|
| `ref` | integer | Ref of the row: 79 for CQ-79. |
| `verdict` | `ko`, `partial`, `unknown`, `ok_human`, `ok`, `na` | See "Verdicts". |
| `confidence` | `high`, `medium`, `low` | See "Confidence". |
| `summary` | text, 1 to 600 characters | What was found. |
| `evidence` | list | See "Evidence". May be empty, except for `ok` and `na`, which need at least one piece of evidence. |
| `todo` | text of 400 characters at most, or `null` | What to do to make the row compliant. `null` when nothing is left to do. |
| `human_check` | text of 400 characters at most, or `null` | What a human must validate, and where. `null` when nothing. |
| `divergence` | text of 400 characters at most, or `null` | Difference between the code and the preprod. See "Code and preprod". |
| `needs_probes` | list of `{"url", "method"}` | HTTP answers you still need. `[]` when none. See "Missing HTTP answers". |
| `needs_scenarios` | optional list of `{"id", "url", "profile", "steps"}` | Interaction scenarios you still need on a touch screen of 320 px. Absent or `[]` when none. See "Missing scenario results: `needs_scenarios`". |

## Texts

`summary`, `todo`, `human_check` and `divergence` end up in a Notion comment that the devs read: blocks labelled "Constat", "À faire", "À valider", "Écart code / préprod". The whole comment holds 1,500 characters and shows the first 5 pieces of evidence.

- Always write these texts, and the `detail` of evidence, in French.
- Never write an em dash. Use a colon, a comma or parentheses.
- Never write a secret: no password, token, API key, cookie value, `.env` value, Basic Auth credential, private key, personal data. Give the kind of secret and its place only ("une clé d'API en clair dans config/services.php, ligne 12").
- Never write `null` as a text, an empty string or "N/A": use JSON `null`.
- State facts first: what, where, how many. One or two sentences in `summary`, under 300 characters when possible. Do not repeat the row title.
- Make `todo` an action a dev can start right away: the change and the place. "Vérifier la conformité" is not a todo.
- Make `human_check` a test a human can run in five minutes: the page, the steps, what to look for.

## Verdicts

From worst to best: `ko`, `partial`, `unknown`, `ok_human`, `ok`, `na`.

Judge against the fiche: its "Comment tester" section is the test to run, its "À faire" section is the requirement, its "Par techno" section adds what is specific to each techno. The `tooling_hint` of the row in `rows.json` adds a row-specific hint.

### `ok`: compliant, verified without human judgement

Use it when a check that needs no judgement covers the whole row: a header, a file, a configuration value, a command output, a tool result for the exact rule of the row.

- Always give at least one piece of evidence and `confidence: "high"`.
- Always leave `todo` and `human_check` at `null`.
- Never use `ok` on a row whose tools include "Intervention humaine": use `ok_human`.
- Never use `ok` when part of "Comment tester" was not checked: use `ok_human` with the rest in `human_check`, or `unknown`.

```json
{
  "ref": 41,
  "verdict": "ok",
  "confidence": "high",
  "summary": "La préprod renvoie Strict-Transport-Security (max-age=31536000; includeSubDomains) sur les 6 pages sondées, et http:// redirige en 301 vers https://.",
  "evidence": [
    {"type": "tool", "tool": "http", "ref": "summary.json#headers/strict-transport-security"},
    {"type": "http", "url": "http://preprod.example.org/", "detail": "301 vers https://preprod.example.org/"}
  ],
  "todo": null,
  "human_check": null,
  "divergence": null,
  "needs_probes": []
}
```

### `ok_human`: compliant for the audit, a human must validate

Use it when everything the audit can check is compliant, but the row needs judgement (relevance of a text alternative, quality of a content, visual rendering), a form to submit, or "Intervention humaine".

- Always fill `human_check` with what to validate and where.
- Always leave `todo` at `null`: if something is left to do, the verdict is `partial` or `ko`.

```json
{
  "ref": 58,
  "verdict": "ok_human",
  "confidence": "high",
  "summary": "Les 42 images des 6 pages clés ont un attribut alt ; il est vide pour les 9 images décoratives.",
  "evidence": [
    {"type": "tool", "tool": "a11y", "ref": "summary.json#image-alt"},
    {"type": "file", "repo": "nuxt", "path": "components/ArticleCard.vue", "line": 18}
  ],
  "todo": null,
  "human_check": "Vérifier que les textes alternatifs décrivent l'information des images sur les pages Accueil et Article.",
  "divergence": null,
  "needs_probes": []
}
```

### `partial`: partly compliant

Use it when a real part of what the fiche asks is in place and another part is missing: some of the expected headers, most templates but not all, the rule applied without its configuration.

- Always fill `todo` with what is left.

```json
{
  "ref": 63,
  "verdict": "partial",
  "confidence": "high",
  "summary": "La préprod envoie X-Content-Type-Options et Referrer-Policy, mais ni Content-Security-Policy ni Permissions-Policy.",
  "evidence": [
    {"type": "tool", "tool": "http", "ref": "summary.json#headers"},
    {"type": "file", "repo": "nuxt", "path": "nuxt.config.ts", "line": 34}
  ],
  "todo": "Ajouter Content-Security-Policy et Permissions-Policy aux routeRules de nuxt.config.ts, puis déployer en préprod.",
  "human_check": null,
  "divergence": null,
  "needs_probes": []
}
```

### `ko`: not compliant

Use it when what the fiche asks is missing or broken where it matters.

- Always fill `todo` with the fix and its place.

```json
{
  "ref": 79,
  "verdict": "ko",
  "confidence": "high",
  "summary": "3 éléments ont un tabindex positif : l'ordre de tabulation saute le menu.",
  "evidence": [
    {"type": "file", "repo": "nuxt", "path": "components/AppHeader.vue", "line": 42},
    {"type": "page", "url": "https://preprod.example.org/actualites/x", "detail": "parcours au clavier"},
    {"type": "tool", "tool": "a11y", "ref": "summary.json#home/3"}
  ],
  "todo": "Supprimer les tabindex positifs et suivre l'ordre du DOM.",
  "human_check": null,
  "divergence": null,
  "needs_probes": []
}
```

### `na`: not applicable to this project

Use it when the subject of the row does not exist in the project: no payment, no form, no API, no user account.

- Always with `confidence: "high"`, at least one piece of evidence (the file or the result that shows the subject is absent) and a `summary` that says what you searched and did not find. An `na` without evidence becomes `unknown`.
- Never use `na` for "not checked": that is `unknown`.
- Rows outside the project technos never reach you: the scripts already set them aside.

```json
{
  "ref": 97,
  "verdict": "na",
  "confidence": "high",
  "summary": "Le projet n'a pas de paiement en ligne : aucune route de commande, aucun paquet de paiement dans composer.json ni dans package.json, aucun tunnel d'achat sur la préprod.",
  "evidence": [
    {"type": "file", "repo": "laravel", "path": "composer.json"},
    {"type": "file", "repo": "nuxt", "path": "package.json"}
  ],
  "todo": null,
  "human_check": null,
  "divergence": null,
  "needs_probes": []
}
```

### `unknown`: cannot be evaluated

Use it when the row, or the part of it that decides the verdict, cannot be checked: a tool unavailable or failed, a page behind a login without a test account, a command refused by the SSH guard, an HTTP answer still missing.

- Always say why in `summary`.
- Always fill `human_check` with what to check and how: the exact command, the URL, the steps.
- Never use `unknown` to soften a `ko` or a `partial` you can prove.

```json
{
  "ref": 112,
  "verdict": "unknown",
  "confidence": "high",
  "summary": "Le garde-fou SSH a refusé la lecture de la crontab de l'utilisateur de sauvegarde : la planification des sauvegardes n'a pas pu être vérifiée.",
  "evidence": [],
  "todo": null,
  "human_check": "Commande à faire confirmer par le lead : crontab -l -u backup (lecture seule, affiche la planification sans rien modifier).",
  "divergence": null,
  "needs_probes": []
}
```

## Confidence

- `high`: direct observation that covers the row (file read, response seen, command output, tool result on the exact rule).
- `medium`: indirect or partial observation (a sample of pages, code read without seeing the preprod).
- `low`: weak signals. Prefer `unknown` with what to check.

## Evidence

List the strongest evidence first: the comment shows the first 5.

| `type` | Keys | Rules |
|---|---|---|
| `file` | `repo`, `path`, `line` (optional: integer from 1, or `null`) | `repo` is a `repos[].name` of `context.json`. `path` is relative to the repo root and names an existing file (never a folder nor the root `.`); never absolute, never `..`. The comment links it to GitLab at the audited commit. |
| `page` | `url`, `detail` (optional) | A page seen in the browser, on a host of the target. `detail` says what was observed, in French. |
| `tool` | `tool`, `ref` | `tool` is a folder of `tools/` that holds a `summary.json` and whose `result.json` is `ok` or `not_applicable` (a tool that failed or was skipped is no evidence). `ref` is relative to `tools/<tool>/`, for example `summary.json#home/3`. |
| `http` | `url`, `detail` | A URL that a probe of the toolbox requested (`tools/http/`, `tools/http-followup/`), on a host of the target. `detail` gives the status, the header or a short excerpt, in French and without any secret. |

- The code checks every piece of evidence at `validate`: a `page` or `http` URL is on a host of the target (`target.preprod_url`, `target.prod_url` on a prod run, the `sites` of `context.json`), an `http` URL is a probe of the toolbox that ran (`tools/http/`, `tools/http-followup/`), and a `tool` is a tool whose `result.json` is `ok` or `not_applicable`. A finding with other evidence is refused. A request you made yourself is not `http` evidence: write its status, header or size in `summary`. `na`, like `ok`, needs at least one piece of evidence.
- Never cite a file, a repo or a tool that the run does not hold: `cqa_plan.py validate` checks them and a finding with such evidence is rejected. The check is made once, at `validate`, and frozen in `findings/<lot>.checked.json`.
- Never put credentials or a token in a URL.

## Several repos: the worst verdict

A row can concern several repos (Laravel back-office and Nuxt front, for example). Evaluate it on every repo it concerns, then keep the worst verdict in the order `ko`, `partial`, `unknown`, `ok_human`, `ok`, `na`. Laravel `ok` and Nuxt `partial` give `partial`; a repo the row does not concern counts as `na`, which never changes the result of the others. When the repos differ, name each repo and its result in `summary`.

## Code and preprod: `divergence`

- The code is the working tree of each repo, at `repos[].audited_sha` of `context.json` (branch `develop`). The preprod runs `repos[].deployed_sha`; `aligned: false` means that it runs other code.
- When the code and the preprod disagree on the row, describe it in `divergence`: what the code does, what the preprod shows, the likely cause (fix not deployed yet, configuration made on the server only). Example: "Le correctif est dans develop (commit 3f2a1c9) mais pas en préprod, qui renvoie encore X-Powered-By."
- Give the verdict of the code, which is what goes live. For a row that only the server or the preprod can show (server configuration, TLS), give the verdict of the preprod.
- Leave `divergence` at `null` when both agree, or when only one side could be checked (say so in `summary`).
- A filled `divergence` turns `ok` and `ok_human` into `partial`. `ko` and `partial` stay.

## Missing HTTP answers: `needs_probes`

- When the verdict depends on an HTTP answer that `tools/http/` does not hold and that you cannot get yourself, ask for it in `needs_probes` instead of guessing.
- Each entry is `{"url": "<absolute URL>", "method": "GET"}` or `"HEAD"`. Never another method.
- Only ask for URLs on the origin (scheme, host and port) of the audit target: `target.preprod_url`, or `target.prod_url` on a prod run only, that is a `phase_filter` with J0 or J+7 while `target.prod_live` is true (the rule of `probes.is_prod_run`). Any other URL is dropped.
- Never put credentials or a token in a probe URL. Ask only what decides the verdict, 5 URLs per finding at most.
- The orchestrator runs every probe once through the toolbox, with the preprod credentials when needed. The answers land in `tools/http-followup/`. Then it relaunches the lot once with `Follow-up: yes`.
- A finding that lists probes is never `ok`, `ok_human` or `na`: give `unknown`, or `ko` or `partial` when your evidence already establishes it.
- First round: list the probes with that verdict.
- Follow-up round: remove every probe that got an answer, and judge again with it. Keep only the probes that got none. A finding that still lists probes after the follow-up is capped at `unknown` (`ko` and `partial` stay).

```json
{"needs_probes": [{"url": "https://preprod.example.org/.well-known/security.txt", "method": "GET"}, {"url": "https://preprod.example.org/sitemap.xml", "method": "HEAD"}]}
```

## Missing scenario results: `needs_scenarios`

Use it when the verdict of a row depends on what a menu, a panel or the focus does on a small touch screen (320 px wide, landscape) and `tools/mobile` does not show it: a component without ARIA (a clickable `div` menu, which the sweep does not find as a trigger), a particular panel, the focus order inside a menu. It is optional: absent, or `[]`, when nothing is needed.

- A scenario is data, never code. It is checked against a closed list and caps, and a finding that breaks a rule is invalid (the lot is relaunched once with the errors, `references/lots.md`, section 5).
- Ask for a scenario only when you already know the selectors: read from the DOM in Chrome (browser lot), or from the templates of the repos (auditor). Never to look for them and never a guess: a selector that matches nothing answers « élément introuvable », which proves nothing. A selector is a CSS selector resolved by `document.querySelector`: `xpath=`, `text=` and `>>` are not CSS and answer « sélecteur invalide ».
- The page is loaded by a GET before any guard acts: never a URL that changes a state (`logout`, `/user/logout`, delete, trash, publish, cron, cache clearing, a token, a nonce, `action=trash`), never a PHP file outside the public entry points of the application (the rule of the agents' hard rules). The tool refuses such a URL with its reason, but its list is closed: the rule is yours first.
- Caps: 3 scenarios per finding, 15 steps per scenario, selectors and values of 200 characters at most, without control characters. The run plays 20 scenarios at most: the others are dropped with their reason, and the findings that still wait for them stay capped at `unknown`.
- A `tap` or a `select` aims only at a control that merely reads: a menu, the trigger of an accordion or of a panel (`aria-expanded`), a tab, a list of choices without effect. Never at a control that acts (add to cart, delete, like, vote, subscribe, log in or out, validate, save, any button whose label or behaviour does something), even a `button` outside a form: the tool lets the GET requests of the page through, so only your choice of the target keeps the preprod unchanged.
- In a lot whose `needs_session` is true, ask for no scenario: the toolbox has no session and would only see the login page.
- A scenario has these four keys and no other:
  - `id`: `^[a-z0-9-]{1,40}$`, chosen by you, unique in the lot (a second finding of the lot that takes it invalidates both), never with a lot prefix: the collection writes `<lot>:<id>`.
  - `url`: absolute, on the origin of the target, 2000 characters at most. It need not be a key page.
  - `profile`: `portrait` (320 x 640) or `paysage` (640 x 320), a touch screen at scale 2.
  - `steps`: 1 to 15 steps, played in order on the page loaded afresh. A step that fails stops the scenario.
- A step has a `type` among these six and only the keys listed:
  - `tab`: `count` (1 to 30, 1 by default) and `shift` (`false` by default); presses Tab (or Shift+Tab) and notes the focus at each press.
  - `press`: `key`, one of `Escape`, `Tab`, `Shift+Tab`, `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`, `Home`, `End` (never Enter nor Space).
  - `tap`: `selector`; taps the element (a control that reads, see above).
  - `select`: `selector` and `value`; picks an option of a list, the form is never sent (a list without effect, see above).
  - `state`: `selector`; reads the attributes, role, size and focus of an element.
  - `capture`: no key; takes a picture of the page.
- Nothing types text, sends a form, follows a link or runs JavaScript of yours. The tool refuses a submit control, a download link (`a[download]` or `area[download]`, « refusé : lien de téléchargement ») and a consent banner, aborts every navigation after the load, of the page or of an iframe (an iframe loaded afterwards stays empty), and every request that is not a GET, a HEAD or an OPTIONS, but it lets the GET requests of the page through: a `tap` on a `button` whose script calls `fetch('/cart/add/12')` or loads `?add-to-cart=12` runs the action on the preprod.
- A finding that lists scenarios is never `ok`, `ok_human` or `na`: give `unknown`, or `ko` or `partial` when your evidence already establishes it.
- The orchestrator plays the scenarios once, in an emulation of Chromium (no Safari iOS, a tap and no other gesture, no virtual keyboard, a site seen without its consent banner: say it in `summary` or `human_check` when it matters). The answers land in `tools/mobile-followup/`, one line per step, found by `<lot>:<id>` (your lot id, a colon, the `id` you chose). Then it relaunches the lot once with `Follow-up: yes` (that one relaunch also serves the probes of the lot).
- Follow-up round: judge again with the steps of each scenario (the `id` field of `raw/<lot>-<id>-<profile>.json` must be `<lot>:<id>`; a scenario with no line under your `<lot>:<id>` was not played) and cite them as `{"type": "tool", "tool": "mobile-followup", "ref": "summary.json#<lot>:<id>"}`. Remove a scenario whose steps ran (one that stopped on a `failed` step answers up to that step: judge with what ran; so does one cut by the budget, whose remaining steps say « non exécutée (budget de durée atteint) »). Keep a scenario that proved nothing: not played (a refusal, a page not opened, a budget reached), or whose steps are `refused`, `selector-invalid` or `not-found`. A finding that still lists scenarios after the follow-up is capped at `unknown` (`ko` and `partial` stay), like one that still lists probes.
- Never ask for a scenario in a rerun or a recette: a rerun has no follow-up.

```json
{"needs_scenarios": [{"id": "menu-burger", "url": "https://preprod.example.org/", "profile": "portrait", "steps": [{"type": "tap", "selector": "#menu-toggle"}, {"type": "state", "selector": "#menu"}, {"type": "tab", "count": 3}, {"type": "press", "key": "Escape"}, {"type": "capture"}]}]}
```

## Guardrails of the merge script

`cqa_plan.py plan` applies these rules, in this order, each on the verdict left by the previous one. Write the right verdict yourself: a lowered verdict is a finding the lead has to correct.

1. `ok` without evidence, or with a confidence other than `high`, becomes `ok_human`.
2. `ok` on a row whose tools include "Intervention humaine" becomes `ok_human`.
3. A filled `divergence` turns `ok` and `ok_human` into `partial`.
4. `needs_probes` or `needs_scenarios` still filled after the follow-up turns `ok_human`, `ok` and `na` into `unknown`; `ko` and `partial` stay.
5. `ok` with a filled `todo` or `human_check` becomes `ok_human`.
6. `na` with a confidence other than `high` becomes `unknown`.
7. `na` without evidence becomes `unknown`.
8. An `ok` built from a context document (its `source`) becomes `ok_human`: a report may be older than the code.

A row of the lot without a valid finding (missing, invalid, rejected for its evidence, ref given twice) counts as `unknown`.

## From verdict to Notion

Only the orchestrator writes in Notion, after the lead confirms. Sub-agents never do.

| Verdict | Status written | Assignee | Comment |
|---|---|---|---|
| `ok` | Ok / Recetté | unchanged | short evidence |
| `ok_human` | Mis en place | lead added | what the human must validate |
| `partial` | Mise en place partielle | unchanged (assignment step) | finding and what is left |
| `ko` | À traiter | unchanged (assignment step) | finding and what to do |
| `na` | Non applicable | unchanged | justification |
| `unknown` | unchanged | unchanged | reason and what to check |
| row "En standby" | never changed | never changed | none |

A status set by a human changes only with the lead's agreement.

### Rows judged from a context document

A row can take its verdict from a context document, a third-party audit report (SKILL.md, section 6, step 1, and `references/documents.md`). The sub-agents never write such a finding: they never write `source` nor a `document` evidence (the schema says so, and `validate` refuses them in a lot file). `cqa_plan.py lots` writes these findings in `findings/context-docs.json` from `context-docs/verdicts.json`, and the plan and the Notion write take them like any other finding.

- Shape: `source: "context_doc"`, `confidence: "high"`, one piece of evidence of type `document` per report (`doc` is the id of an imported document of `context-docs/`, which the code checks, `ref` the criteria such as « critères 7.3 », else the places of its points), a `summary` that names the report and its date (« Rapport « <label> » (<date>) : ... ») and paraphrases its points, a `todo` made of the suggested fixes for `ko` and `partial`, and `human_check` and `divergence` at `null`.
- Verdict: `ko` when a `ko` point of the report is linked to the row and no criterion of the row is `ok`, `partial` when one is (a `ko` of the report is never lost); `ok_human` when every criterion of the row is covered and `ok` or `na` in the report (a conforming report is never an `ok`: it may be older than the code, so `human_check` says « Conforme d'après le rapport d1 du <date>, à confirmer sur la version actuelle », and the date is « de date inconnue » when the report has none), `na` when all are `na`; no verdict otherwise (a criterion the report leaves undetermined, or a report of findings without a `ko` for the row): the skill audits the row, the points of the report being hints. With several reports the worst verdict wins.
- `in_repo` is `false` only when every linked point is for the content, a third party or the infrastructure: the `todo` then starts with « À traiter par <propriétaires> : » and the fix mode leaves the row to the devs with that reason.
- Notion: the table above applies, and so do the guardrails (an `ok` on a row whose tools include « Intervention humaine » becomes `ok_human`). The comment lists the line « Rapport d1 : critères 7.3 » among its evidence. In Notion and in the merge requests only the name of the document, the reference and the paraphrased fix are written, never a raw excerpt of the document and never an exploitation detail.
- A rerun or a recette ignores these verdicts: the agents judge the code as it is, and their finding replaces the one of the document.

## For the orchestrator: plan, confirmation, writing

Sub-agents can skip this section. Every Bash call starts with the `S=` line of SKILL.md (here unexpanded) and `RUN="<run_dir>"`.

### Plan

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN"
```

- It merges the findings (after the guardrails above), the rows and the exclusions into `notion/plan.json`, and prints its `summary`: `{"by_status_after", "blocking_non_conform", "unknown", "human_overrides": [{"ref", "before", "after"}], "deferred", "missing_in_copy"}`, plus `discarded` (`[{"ref", "reason"}]`, in French) and `discarded_count`: the rows whose finding it set aside, never silently. A row without a valid finding (refused evidence, a row the file does not hold, a lot that wrote nothing) is written « Non évaluable », except a row on which the journal of the copy already holds a verdict written by the skill (a status, or a comment other than « Non évaluable »), which is left as it is (its reason says so); a finding refused in a later file leaves the earlier one in place and is listed too. The same list is printed on stderr. It is not part of `notion/plan.json`. The evidence of a finding is not checked again against the workstation: it was frozen at `validate` (`findings/<lot>.checked.json`), and only a finding without a record (a run older than the record) is.
- A comment is planned only when the status changes, or when neither the visible comments of the integration (`comments.json`) nor the earlier runs of this copy already carry the verdict: a run again does not repeat comments.
- The header of a comment gives the date of the run; for a row whose latest finding comes from a rerun, the date of the rerun (recorded by `rerun-close` in `reruns.json`), so that the lead sees when it was judged again. The footer keeps the audited commit.
- Exit 6: `context.json` incomplete for the comments (`lead.option`, `audited_sha`, `run_id`): fix it. Exit 2: a file of the run is missing (the message names the command).

Titles of the refs to show, from `rows.json`:

```bash
RUN="<run_dir>"
python3 - "$RUN/rows.json" 12 40 <<'PY'
import json, sys
rows = json.load(open(sys.argv[1], encoding="utf-8"))["rows"]
titles = {row["ref"]: row["title"] for row in rows if row.get("ref") is not None}
for ref in map(int, sys.argv[2:]):
    print(f"CQ-{ref} : {titles.get(ref, '?')}")
PY
```

What the lead sees, in French:

1. « Statuts après écriture : À traiter <n>, Mise en place partielle <n>, Mis en place <n>, Ok / Recetté <n>, Non applicable <n>, En standby <n>. »
2. « Lignes bloquantes non conformes : » then one line per ref, `CQ-<ref> : <titre>`.
3. « Lignes non évaluées : <n> » (the reasons are in the synthesis).
4. « Statuts posés par un humain que l'audit changerait : » then `CQ-<ref> : <titre> (« <before> » vers « <after> »)`, or « aucun ». A « À traiter » that a person put back after the skill wrote another status on the row (journal of this run or of an earlier run of the copy) is listed here; a « À traiter » the skill never wrote over is not.
5. « Reportées à J0 / J+7 : <n>. Lignes du modèle absentes de la copie : <n>. »

### Confirmation

The consent is given at launch (`notion_write` in `context.json`, SKILL.md section 4): when it is set, the write question is not asked and the matching choice below applies (`all`, `keep_human`, `none`). `cqa_notion.py apply` holds that consent itself, so no option has to be repeated: it refuses `none` and a run with `dry_run` true (exit 2), and with `keep_human` it leaves alone the pages whose status a human set, whether or not the plan was rebuilt. With `all`, a plan that would replace statuses set by a human is confirmed first in interactive mode (SKILL.md section 7, step 1); in autonomous mode `all` is applied as `keep_human`, the kept statuses go to the recap and to « Remarques », and `apply` counts the operations it left out in `kept_human`. In autonomous mode the summary above is not shown before the write but as a recap after it. Only when `notion_write` is `null`, one question, « Écrire le plan dans la copie Notion ? »:

- « Tout écrire »: statuses, assignments of the lead and comments, the human statuses listed included (not in autonomous mode, where it is applied as the next choice).
- « Sans toucher aux statuts posés par un humain »: offered only when `human_overrides` is not empty. Those pages are left as they are, comments included. Keep the list shown above: the rebuilt plan no longer holds it, and SKILL.md section 8, step 3, adds it to `remarques.md`, which the synthesis injects as « Remarques ». Rebuild the plan first, so that the synthesis describes what is written, then apply (`apply` leaves those pages alone by itself):

```bash
S="${CLAUDE_SKILL_DIR}/../../scripts"
RUN="<run_dir>"
python3 "$S/cqa_plan.py" plan "$RUN" --skip-human-overrides
```

- « Ne rien écrire »: the plan stays in `notion/plan.json`; synthesis, « Remarques » and the final scan are made locally, nothing goes to Notion.

### Exit codes of `cqa_notion.py apply`

`apply` prints `{"done", "skipped", "blocked": [{"key", "labels"}], "errors": [{"key", "message"}], "replace_failed", "status_changed": [{"key", "ref", "current", "reason"}]}`, plus `kept_human` (the operations left out to keep the statuses set by a human) when there are some. `done` and `skipped` are numbers: operations written now, and operations already written (listed in `notion/applied.json`, a comment already on the page, a status the row already has). It prints one French line per operation on stderr. `apply` reads the status of a row again before writing it: when a person changed it since the copy was read, the status operation is left unwritten and listed in `status_changed` (exit code unchanged, the comments and assignments of the row are written); it never holds the run as unwritten, and a later write of the same plan reads the status again. Say each one to the lead: « CQ-<ref> : statut changé dans Notion depuis la lecture de la copie (« <current> »), non réécrit. » The `key` of an operation is in `notion/plan.json`, with its `ref` and `kind`.

| Exit | Meaning | What to tell the lead |
|---|---|---|
| exit 0 | every operation written or already there | « <done> opérations écrites, <skipped> déjà présentes. » |
| exit 7 | at least one operation blocked, without any request; the others are written | for each entry of `blocked`, from its labels (`blocked[].labels`): « CQ-<ref> (<kind>) non écrit : <raison> », reasons below |
| exit 3 | stopped on a network or Notion API error, or an operation refused by Notion; wins over 7 | « Écriture interrompue (<message>). Relancer : la reprise saute ce qui est déjà écrit. » Run the same command once more, only once the cause is fixed: `notion/applied.json` makes it resume where it stopped. Still exit 3: stop, without synthesis; the run stays resumable at section 7. An operation refused by Notion skips the later operations of the same page. |
| exit 8 | the plan targets the reference template (its data source, or its page for a comment) | « Écriture refusée : la cible est le modèle de référence. » Nothing is written: stop; a new run with the URL of the copy. |
| exit 2 | plan or `context.json` unreadable or invalid, environment variable missing, `dry_run` true or `notion_write` `none` (nothing is written) | show the message, fix, run again |

Labels of `blocked[].labels`:

- `page_outside_copy`: « page hors de la copie » (not a row of `rows.json`, or not the page of the copy).
- `unknown_option`: « option de « Assignée à » absente de la copie » (removed since the plan; Notion would create it, so the skill does not write it).
- A secret label (`known_value`, `private_key`, `aws_key`, `gitlab_token`, `github_token`, `notion_token`, `jwt`, `credential_assignment`, `credential_define`, `provider_key`, `url_credentials`, `authorization_header`, `cookie_header`, `mysql_password_option`, `kube_client_key`): « le texte contient ce qui ressemble à un secret (<label>) ». Never show the text. The lead writes that comment by hand if needed, without the secret.

A blocked operation stays blocked when run again. `cqa_notion.py comment-page` follows the same codes and the same journal.
