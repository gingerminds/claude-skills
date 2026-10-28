# Browser: Claude in Chrome (spec § 7)

The browser checks run in the lead's Chrome, through Claude in Chrome. This page covers the diagnostic check, the login pause, the dispatch of the browser lots and the fallback in this session.

## Rules (every browser use, the orchestrator's included)

- **Template by template.** For each key page, every row that needs it is checked before the next page.
- **New tabs only**, on the project domains: the hosts of `target.preprod_url` and of `key_pages`, plus the host of `target.prod_url` on a prod run. Never read, use or close a tab the skill did not create.
- **Manual login.** The skill opens the preprod and the back-office, then waits for the lead to log in (Basic Auth, test accounts). It never types a password, nor anything else.
- **Read-only.** Allowed: navigation, reading the DOM, Tab and Escape, resizing, zoom. No form is ever submitted, even on the preprod: the rows that need one get `ok_human` or `unknown`, with the test in `human_check`. No click or URL that changes state (save, delete, publish, log out, run cron, clear a cache). No interaction with a cookie or consent banner.
- **Prod.** On a prod run (`phase_filter` with J0 or J+7 and `target.prod_live` true): navigation, reading, Tab and Escape only.
- **Context economy.** Prefer DOM reads and read-only JavaScript to screenshots. A screenshot only when a visual proof decides the verdict; it never goes to Notion.

## Loading the tools

The Chrome tools may be deferred. Load them in one ToolSearch call:

`select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__tabs_create_mcp,mcp__claude-in-chrome__tabs_close_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__read_page,mcp__claude-in-chrome__find,mcp__claude-in-chrome__get_page_text,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__computer,mcp__claude-in-chrome__resize_window,mcp__claude-in-chrome__read_console_messages`

## Diagnostic (tool "Navigateur")

1. Load the tools; call `tabs_context_mcp` with `createIfEmpty: true`.
2. Create a tab with `tabs_create_mcp`, then `navigate` it (with its `tabId`) to `target.preprod_url`.
3. Close that tab with `tabs_close_mcp`.

"Navigateur" is available when the tab was created and navigated. A Basic Auth dialog or a login page is expected. Tools missing, or the extension not connected: « Claude in Chrome n'est pas connecté : ouvrir Chrome avec l'extension Claude, puis relancer le diagnostic. » and "Navigateur" is unavailable.

## Login pause

Only when "Navigateur" is available, after the first batch of lots is dispatched (`references/lots.md`):

1. Create a tab on `target.preprod_url` (on the prod URL for a prod run) and, with `test_accounts.backoffice`, one on the key page « Back-office ».
2. Ask « Connectez-vous dans Chrome, dans les onglets ouverts : Basic Auth de la préprod si elle le demande, puis vos comptes de test. N'utilisez pas cette fenêtre Chrome pendant les lots navigateur. » with « C'est fait » and « Continuer sans connexion ».
3. Without login, the rows that need a session end `unknown`, with the steps in `human_check`.
4. After the last browser lot, close these tabs with `tabs_close_mcp`.

## Dispatch of the browser lots

- Before each browser lot, call `tabs_context_mcp`. If it fails, ask the lead « Claude in Chrome est déconnecté : reconnecter l'extension, puis répondre » with « Reprendre » and « Continuer sans navigateur » (the lot then goes to `gm:cqa-auditor` with the no-"Navigateur" sentence), and do not dispatch before the lead has chosen. « Continuer sans navigateur » holds for the rest of the run, unless the lead says otherwise: every later browser lot goes to `gm:cqa-auditor` the same way, without a new `tabs_context_mcp` check.
- One browser lot at a time, after the `server` lot (its `facts/server.json` holds the server part of the mixed rows).
- "Navigateur" available: `gm:cqa-browser`, with the prompt of `references/lots.md`.
- A return that starts with `CQA_BROWSER_UNAVAILABLE:` (the sub-agent could not load the Chrome tools): fallback below, for this lot and every later browser lot. Do not dispatch `gm:cqa-browser` again in this run.
- "Navigateur" unavailable from the diagnostic: give the browser lots to `gm:cqa-auditor`, with the sentence « Navigateur indisponible : évaluer ce que le code et les outils permettent, unknown pour le reste. » It judges from the code and the tools and gives `unknown` for the parts only a browser shows.
- `gm:cqa-browser` cannot run `validate`: always validate its file yourself.

## Fallback: the lot in this session

Read the file `agents/cqa-browser.md` of the plugin (two folders above this skill) and apply it yourself, as if dispatched with `Run dir: <run_dir>. Lot: <lot_id>. Follow-up: <yes|no>.`:

- Every hard rule of that file binds you as well: no form, no typing, no click that changes state, no consent banner, new tabs only, project domains only, read-only JavaScript, no cookie or storage value, no secret or personal data in a finding.
- Follow its method: inputs in its order, one template at a time, observations noted per row and page, then one finding per row.
- Write `findings/<lot>.json` (or `findings/<lot>-followup.json`) with Write, in the format of `references/verdicts.md`, then run `cqa_plan.py validate` as for any lot.
- Close your tab at the end of the lot and restore the window size.
- Tell the lead in one line that the browser lots run in this session, which takes longer.
