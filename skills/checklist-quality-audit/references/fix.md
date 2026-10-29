# Fix mode: conventions

The conventions of the fix mode (`/gm:checklist-quality-audit fix`, section 12 of SKILL.md), in one place. The orchestrator, the fixer (`agents/cqa-fixer.md`) and the fix reviewer (`agents/cqa-fix-reviewer.md`) all follow this file. These conventions belong to the skill: they never depend on the workstation (the lead's or another dev's `CLAUDE.md`, the defaults of the tool that runs the skill), and they win over any such default.

## 1. Keys, passes and files

- A key is one checklist category in one repo, `<repo>/<slug>` (`front/securite`): the `key` of an entry of `fix/candidates.json`. A key has one branch, one worktree and, once pushed, one draft merge request.
- The folder of a key is `fix/<repo>/<slug>/` in the run directory:

| File | Written by | Content |
|---|---|---|
| `wt/` | `cqa_fix.py start` | the worktree of the key, on its branch |
| `report-<N>.json` | fixer | its report of pass N (section 8) |
| `notes-<N>.json` | orchestrator | the answers and problems sent to pass N, from pass 2 |
| `review-<N>.json` | reviewer | its verdicts on pass N (section 8) |
| `checks.json`, `check/` | `cqa_fix.py check` | the checks of the changed files (section 9) |
| `mr-title.txt`, `mr.md`, `mr-addition.md` | `cqa_fix.py mr-body` | the title and description of the merge request |

- A pass is one dispatch of the fixer on a key, numbered from 1. Pass 1 takes the selected rows of the key. A later pass takes the rows the lead answered (questions, section 5) or the rows a check, the guard or the review sent back (correction, section 6).
- Right before each fixer pass, `cqa_fix.py pass-start <run_dir> <key> --pass <N>` records the head of the branch as the base of pass N (`keys[<key>].bases["<N>"]` in `fix/state.json`). Every fixer pass N that leaves rows fixed is followed by its own review N, whose `Base` is that recorded head.
- `cqa_fix.py validate <run_dir> <key> review --pass <N>` records the blocking rows of review N (`keys[<key>].reviews["<N>"]`). The key reaches the step `reviewed`, which `mr-body` and `publish` need, only when no row is pending and, for every pass p that has rows fixed in it (`outcome` `fixed`, `pass` p), review p is recorded and blocks none of them. A row fixed again in a later pass belongs to that pass; a dropped row no longer counts. `drop` applies the same rule.
- The range of a key is `<start_sha>..HEAD` of its worktree (`keys[<key>].start_sha` in `fix/state.json`). The guard, the checks, `validate` and `drop` judge only those commits.
- Row outcomes in `fix/state.json`: `pending` (not reported yet), `fixed`, `skipped`, `question`, `dropped` (removed from the branch by `cqa_fix.py drop`), `done_before` (already on a reused branch before this pass, never reported again).

**Dispatch of the fixer**, exactly:

`Run dir: <run_dir>. Fix: <repo>/<slug>. Worktree: <path>. Pass: <N>. Rows: CQ-3, CQ-12. Notes: <path|none>. Follow your instructions.`

`Worktree` is the absolute path of `wt/`; `Rows` lists the rows of the pass; `Notes` is the notes file of the pass, `none` on pass 1.

**Dispatch of the reviewer**, exactly:

`Run dir: <run_dir>. Review: <repo>/<slug>. Worktree: <path>. Base: <sha>. Pass: <N>. Follow your instructions.`

`Base` is the head that `pass-start` recorded just before fixer pass N. The reviewer reads `<Base>..HEAD` and judges only the rows fixed in pass N (`pass` N in the state); when a later pass already ran, its commits are also in `<Base>..HEAD` and belong to its own review. The reviewer of pass N runs after `cqa_fix.py validate` has accepted `report-<N>.json`.

**Notes file** `fix/<repo>/<slug>/notes-<N>.json`, written by the orchestrator before pass N (N from 2):

`{"answers": [{"ref", "answer"}], "problems": [{"ref", "source": "guard"|"check"|"review", "detail"}]}`

`ref` is an integer (12 for CQ-12). `answer` is the lead's answer to the row's question: one of its options or a free text, in French. `detail` says the problem in French: for `guard`, the reason the guard printed, with the commit and the path; for `check`, the failed check, the file and its message; for `review`, the reviewer's `notes`.

## 2. Branches

- Real pass: `feature/checklist-qa-fix-<slug>`. With `--dry-run`: `dry-run/checklist-qa-fix-<slug>`. When the name is taken by a merged or discarded branch: `<prefix><slug>-<n>`, the first free suffix. `<slug>` is the slug of the category (`securite`, `seo-geo`).
- `cqa_fix.py start` creates the branch and its worktree (`git worktree add --no-track`) from `origin/<base>` up to date (`develop`, else the default branch of the project), or takes over the branch of an open merge request of the key, or an unmerged fix branch without one: new commits follow the old ones, never a rebase. The local `develop` of the dev is never updated nor used, and their checkout (files, current branch, stash) is never touched.
- Only `cqa_fix.py publish` pushes, and only a `feature/checklist-qa-fix-*` branch, as a fast-forward: never `--force`, never `push -u`, never towards `develop`, `main`, `master` or `release`. A `dry-run/` branch is never pushed and never reused by a real pass.
- The sub-agents never create, switch, move, rename, reset or delete a branch, and never push.

## 3. Commits

```bash
git -C "<worktree>" -c core.hooksPath=/dev/null -c core.fsmonitor=false add -- <path> [<path> ...]
git -C "<worktree>" -c core.hooksPath=/dev/null -c core.fsmonitor=false commit -m "<subject>" -m "<body>"
```

- Name the files of the change one by one (a deleted file too). Never `git add -A`, `git add .`, `git add -u`, `git commit -a`, never a wildcard.
- `-c core.hooksPath=/dev/null`: the hooks of the repo (husky, GrumPHP, CaptainHook...) never run, since they rely on the dev's environment or on `node_modules`. The merge request declares them « Non lancé », with their command. `-c core.fsmonitor=false` does the same for a file monitor configured in the repo. Every git command of the fix mode in a worktree carries both options.
- **Subject**: `type(CQ-107): imperative summary`, in English, 72 characters at most, with a ref of the key. It must match `^(fix|feat|refactor|chore|perf|docs|style)\(CQ-([0-9]+)\): \S`.

| Type | Use |
|---|---|
| `fix` | corrects what the audit found wrong (most rows) |
| `feat` | adds what the audit found missing: a file, a header, a meta tag, an attribute |
| `perf` | a performance fix (lazy loading, image size, cache headers set in the code) |
| `refactor` | restructures code without changing what it does, when the row asks for it |
| `style` | formatting of the code only, no change of behaviour (not a CSS change: that is `fix` or `feat`) |
| `docs` | documentation only |
| `chore` | configuration or maintenance without effect on the site (`.gitignore`, `.editorconfig`, linter configuration) |

- **Body**: short, in English: what the audit found, then what the commit changes. Plain text: no `$`, backtick, double quote or backslash in the subject or the body (the shell expands them inside the double quotes). For example:

```text
fix(CQ-107): escape the search form label

The audit found the search form label printed without escaping.
Escape it with esc_html() in searchform.php.
```

- **No attribution line** of any kind: no `Co-Authored-By`, no `Claude-Session`, no « Generated with », no mention of an assistant or a tool. This rule of the skill wins over the defaults of the tool that runs it. The commits are authored with the lead's git identity (`user.name` and `user.email` of the repo): never `--author`, never `-c user.*`.
- **A commit carries one row only.** A row may take several commits, each with its ref. A change that also fixes another row of the key goes in the commit of the first row; the report of the second row names that row in `covered_by`, and the reviewer judges the commit for both.
- **History is never rewritten** by a sub-agent: no `--amend`, `rebase`, `reset`, `cherry-pick`, `revert`, `merge` or `tag`. A correction is a new commit on top. Only `cqa_fix.py drop` (the orchestrator, before any push) removes the commits of a row from the branch.

## 4. Self-evaluation and the smallest fix

The fixer takes each row in this order.

1. **Recheck** the problem on the worktree: it holds `<base>` up to date, not the audited commit, and the lines of the evidence may have moved. Already fixed: `skipped`, « déjà corrigé sur develop » (name the base branch when it is not `develop`). A row proposed under every repo (`every_repo: true`) that does not concern this repo: `skipped`, « ne concerne pas ce repo ».
2. **Self-evaluate before writing any code.** The fixer does not attempt a row (`skipped`, with a short reason in French) when its fix:
   - would break a guard rail (section 7): third-party code, the core of the stack, a dependency manifest or lock file, a minified file, a secret;
   - needs a decision of the UI/UX team, the client or the contributors: mockup, content, texts and wording, colours and contrasts;
   - needs a change outside the repo: content in the database, a back-office setting, the server configuration, DNS;
   - touches many files with a risk on the behaviour that the audit cannot check without the project's environment;
   - adds or upgrades a dependency (Composer, npm, a plugin, a module, a library). Without the project's environment, a lock file resolved with another PHP or Node version, or without the credentials of private repositories, could break the preprod. The reason gives the command for a dev, for example `composer update <paquet> --with-dependencies`;
   - is uncertain: the fixer is not sure the fix is right, or not sure it is safe.

   In doubt about safety, skip rather than ask: a question (section 5) is for a missing decision, never for a risk.
3. **Fix at the smallest**: what the row asks, nothing more.
   - The style and idiom of the file and of the repo (indentation, naming, how hooks and services are registered) win over the ideal architecture of the stack guidance. No refactoring, reformatting or renaming beyond the fix.
   - Custom code only: extend through the stack's hooks, a child theme, a custom plugin or module, never by editing third-party or core code.
   - A user-facing string goes through the stack's translation API, with the text domain the code already uses.
   - Generated output (built CSS or JavaScript, compiled translations) is never edited by hand when the repo holds its source: change the source. When the change reaches the site only after a build the fixer cannot run, `attention` says so, with the build command found in the project.
   - A tracked file removed by the fix (a `phpinfo.php`, a stray backup): `attention` asks to check that the deployment removes it from the servers too.
4. **Verify by reading** the diff against the « À faire » of the fiche and the `todo` of the finding, and run the « Comment tester » of the fiche on the code where the code shows it. Nothing runs in the project's environment (section 9).
5. **Commit** (section 3), then the next row.

**Semi-auto rows** (`semi_auto: true`, « Semi-auto : MR à valider ») follow the same steps. Their `attention` always says what the reviewer must check closely, from the « À valider » of the finding (`human_check`) and from the fix; the merge request marks them « à valider avec soin ».

**Rows the lead added** (`lead: true`, a « Détection seule » or « Manuel » row the lead judged fixable) get the same discipline and no exception to the guard rails.

Reasons of a skipped row, in French, 300 characters at most, precise enough for a dev to act. For example:

| Case | Reason |
|---|---|
| already fixed | « déjà corrigé sur develop » |
| other repo | « ne concerne pas ce repo » |
| third-party code | « le correctif touche le plugin tiers wp-content/plugins/<nom> : à signaler à l'éditeur ou à surcharger par un hook » |
| dependency | « montée de version à faire par un dev : composer update <paquet> --with-dependencies » |
| design decision | « choix de couleurs à valider par l'équipe UI/UX (contraste du bouton principal) » |
| outside the repo | « réglage de la configuration du serveur (en-têtes HTTP), hors du repo » |
| too wide | « correctif sur 14 gabarits, risque de régression invérifiable sans l'environnement du projet » |
| uncertain | « cause du constat non retrouvée dans le code de ce repo » |

## 5. Questions

- The fixer never asks a question and never waits for an answer: it works alone. When a real decision is missing, the row gets `outcome: question`, with `question` = `{"text", "options", "default"}`: the question in French, 2 to 4 options (200 characters at most each), and in `default` the option the fixer would apply without an answer. It then goes on with the next rows.
- A real decision is a choice between two valid fixes with a visible effect (on the pages, for the visitors, for the editors), or a value only the team knows: the contact of `security.txt`, the provider of the cookie banner, a legal mention.
- Not a question: a risk or a doubt about safety (skip, section 4), a dependency (skip, with the command), what the audit or the fiche already settles.
- A secret the fix needs (a key, a password, a token) never goes in the code: the row becomes a question on where the value comes from (an environment variable, a constant of the server configuration), or is skipped.
- An answered row is never asked again: the next pass applies the answer, or skips the row with the reason (« laissée aux devs par le lead » when that is the answer).
- Present mode: the orchestrator asks the questions of a key as soon as the fixer is done, one at a time, then runs a fixer pass on the answered rows (its own `pass-start`, and its own review when it fixes rows), before the guard and the review. Autonomous mode: each question is put aside with the kind `fix_question` and asked at the very end; the key goes on without the row, and the pass on the answered rows at the end gets its own `pass-start` and review in the same way. A row left without answer, or that the lead leaves to the devs, goes to « Hors-scope » in the merge request.

## 6. Correction pass

- A row with a problem gets one more attempt: a failed check of `cqa_fix.py check` on its files, a problem of the guard (`cqa_fix.py guard`) on its commits, or a `blocking` verdict of the review. The orchestrator writes the problems in the notes file and dispatches a correction pass on those rows. Still a problem afterwards: the orchestrator removes the row from the branch (`cqa_fix.py drop --reason`), and the row goes to « Hors-scope » with the reason.
- The fixer answers a `check` or `review` problem with new commits on top of the branch, with the row's ref and a subject that says what the correction does. It reports the row `fixed` again, with all its commits: the earlier ones as `fix/state.json` lists them now (a drop may have rewritten the SHAs of an earlier report), plus the new ones; `change` and `uat` describe the whole fix.
- A `guard` problem sits in a commit already on the branch: the guard reads every commit of `<start_sha>..HEAD`, and history is never rewritten, so no new commit can clear it. The fixer gives the row up.
- **Giving up**: a row with a commit on the branch can be neither `skipped` nor `question` (`validate` refuses it). The fixer reports it `fixed`, with its commits, `change` and `uat` as they stand, and an `attention` that starts with « Abandon : » followed by the reason in French, 300 characters at most in all, since it becomes the reason of the drop. The same holds in pass 1 for a row the fixer committed and then judged wrong. The orchestrator drops such a row with that reason, without a further correction pass; a reviewer that meets one gives it `blocking` with that reason.
- A drop rewrites the commits that follow the dropped ones, which can take a recorded `Base` out of the branch: drop only once the reviews of the passes in progress are written.
- A correction pass is checked, guarded and reviewed like any pass; its reviewer reads only the correction commits (`<Base>..HEAD`).

## 7. Guard rails

Written in the instructions of the sub-agents, and checked in code: `cqa_fix.py guard` reads every commit of `<start_sha>..HEAD` before any push and prints one problem per commit and path, with the reason in French (quoted below). A commit refused once goes back to the fixer, then its row leaves the branch (section 6).

- **Where to write**: only in the worktrees of the run directory (`fix/<repo>/<slug>/wt`) and in the files of the key's folder that belong to each agent. Never in the dev's folder, their local `develop`, or the stash the worktrees share with the dev's repo (hence never `git stash`). The fixer and the reviewer may read, but never write nor run anything in, the dev's repo (`repos[].path` of `context.json`): only its `vendor/`, `node_modules/` and the core of the stack, which the worktree lacks, to know the APIs the code calls.
- **No server, no project command**: no command on the preprod, the prod or any server (`ssh`, `scp`, `rsync`, a database client); nothing in the project's environment (`docker compose`, `make`, `composer`, `npm`, `yarn`, `pnpm`, `npx`, `drush`, `wp`, `php artisan`, a test suite, a linter of the project, a build); no network request. Notion is written only by the orchestrator, after the lead agreed, for the link of the merge requests.
- **Paths no commit may touch**:
  - the `third_party_paths` of the repo in `context.json` (« code tiers (third_party_paths) »);
  - `vendor/` and `node_modules/`, at any depth (« dossier de dépendances »);
  - the core and the contributed code of the stack (« cœur ou module contribué de la stack »). WordPress: `wp-admin` and `wp-includes` at any depth, `web/wp`, and the core files `wp-activate.php`, `wp-blog-header.php`, `wp-comments-post.php`, `wp-config-sample.php`, `wp-cron.php`, `wp-links-opml.php`, `wp-load.php`, `wp-login.php`, `wp-mail.php`, `wp-settings.php`, `wp-signup.php`, `wp-trackback.php` and `xmlrpc.php` at any depth (`wp-config.php` stays allowed). Drupal: `core`, `web/core`, `docroot/core`, `html/core`, every `contrib` folder at any depth, `libraries`, `web/libraries`, `docroot/libraries`, `html/libraries`. Another stack: the generic rules only;
  - git submodules and `.gitmodules` (« sous-module git »);
  - minified files, `*.min.*` (« fichier minifié »);
  - the dependency manifests and lock files (« dépendances : aucun ajout ni montée en v2.0 »): `composer.json`, `composer.lock`, `package.json`, `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`.
- **No secret** in the code or a message (« secret détecté (<label>) »): the added lines and the messages are scanned for the values the session knows and for common patterns. A secret value a fix needs gives a question or a skipped row, never a value in the code (section 5).
- **Dependencies**: no new dependency, no version change (major or not), no package script.
- **Git**: a subject `type(CQ-xx): ...` (« message de commit hors convention ») with a ref of the key (« Réf hors de cette MR »); no merge commit (« commit de fusion »); no attribution line (« ligne d'attribution dans le message »); the history kept, `start_sha` staying an ancestor of `HEAD` (« historique réécrit : le départ de la passe n'est plus dans la branche »); no tag; push of `feature/checklist-qa-fix-*` branches only, never `--force`, never towards `develop`, `main`, `master` or `release`.
- **GitLab**: a draft merge request assigned to the lead who runs the skill, without reviewer or label (the lead shares out the review), and the updates of its description. Nothing else: never Ready, approval, merge or closing.

## 8. Report and review files

**Report** `fix/<repo>/<slug>/report-<N>.json`, format `skills/checklist-quality-audit/schemas/fix-report.schema.json`, checked by `cqa_fix.py validate <run_dir> <key> report --pass <N>` (exit 6 with every error in French; a valid report updates the rows of `fix/state.json`):

- `key` and `pass` as in the dispatch; one entry per row, `ref` as an integer (107 for CQ-107), each ref once.
- Every row of `Rows:` and every row still `pending` in the state is in the report. Rows `done_before` or `dropped` are not.
- `fixed`: `change` (what changed, in French, 600 characters at most), `uat` (the manual acceptance test, in French, 600 characters at most: where to go once deployed, what to do, what to see), `commits` (one or more SHAs of `<start_sha>..HEAD` whose subject carries the ref of the row, or the `covered_by` ref).
- `skipped`: `reason` (in French, 300 characters at most). No commit of the row on the branch.
- `question`: `question` = `{"text", "options", "default"}` (section 5). No commit of the row on the branch.
- `covered_by` (`fixed` only): the ref of another row of the key whose commit also fixes this one.
- `attention` (optional, in French, 400 characters at most): what the reviewer of the merge request should look at closely; it reaches « Points d'attention » as « CQ-107 : <texte> ». Always set for a Semi-auto row (section 4) and for a row given up (« Abandon : », section 6).

```json
{
  "key": "front/securite",
  "pass": 1,
  "rows": [
    {
      "ref": 107,
      "outcome": "fixed",
      "change": "Échappe le libellé du formulaire de recherche avec esc_html() dans searchform.php.",
      "uat": "En préprod, ouvrir la page d'accueil, chercher <b>test</b> : le libellé affiche le texte tel quel, sans gras.",
      "commits": ["1a2b3c4d5e6f"],
      "reason": null,
      "question": null,
      "covered_by": null,
      "attention": "À valider avec soin : le libellé vient d'une option du thème, vérifier qu'aucun HTML voulu n'y est saisi."
    },
    {
      "ref": 109,
      "outcome": "fixed",
      "change": "Corrigé par le commit de CQ-107 : le même échappement couvre le champ de recherche.",
      "uat": "En préprod, chercher <b>test</b> depuis l'en-tête : le champ affiche le texte tel quel.",
      "commits": ["1a2b3c4d5e6f"],
      "reason": null,
      "question": null,
      "covered_by": 107,
      "attention": null
    },
    {
      "ref": 112,
      "outcome": "question",
      "change": null,
      "uat": null,
      "commits": [],
      "reason": null,
      "question": {
        "text": "Quel contact mettre dans security.txt ?",
        "options": ["L'adresse de l'équipe technique", "L'adresse du client"],
        "default": "L'adresse de l'équipe technique"
      },
      "covered_by": null,
      "attention": null
    },
    {
      "ref": 113,
      "outcome": "skipped",
      "change": null,
      "uat": null,
      "commits": [],
      "reason": "montée de version à faire par un dev : composer update guzzlehttp/guzzle --with-dependencies",
      "question": null,
      "covered_by": null,
      "attention": null
    }
  ]
}
```

**Review** `fix/<repo>/<slug>/review-<N>.json`, format `skills/checklist-quality-audit/schemas/fix-review.schema.json`, checked by `cqa_fix.py validate <run_dir> <key> review --pass <N>` (exit 6 with every error in French):

- `key` and `pass` as in the dispatch; `rows` with one verdict per row the state shows `fixed` in pass N, and no verdict on another row; `attention`, a list (`[]` when there is nothing).
- `ok`: the fix may go in the merge request as is; `notes` optional. `blocking`: it may not; `notes` required, in French, 600 characters at most: what is wrong and what to change. The fixer reads it alone, in one attempt.
- `attention`: the non-blocking remarks for « Points d'attention » of the merge request, in French, 400 characters at most each, starting with the ref when they concern one row.

```json
{
  "key": "front/securite",
  "pass": 1,
  "rows": [
    {"ref": 107, "verdict": "ok", "notes": null},
    {"ref": 109, "verdict": "blocking", "notes": "Le champ de recherche de l'en-tête (header.php:42) affiche encore get_search_query() sans esc_attr() : l'échappement de CQ-107 ne le couvre pas. Ajouter esc_attr() dans header.php."}
  ],
  "attention": ["CQ-107 : le libellé vient d'une option du thème, vérifier en préprod qu'aucun HTML voulu n'y était saisi."]
}
```

## 9. Checks before the merge request

- `cqa_fix.py check` (the orchestrator) runs in the worktree, without the project's environment: the syntax of each changed file (PHP, JavaScript, JSON, YAML) and stylelint with the project's configuration, in the toolbox image mounted read-only on the worktree; else with the host's binaries; else declared not run. A failure sends the rows of the file back to the fixer (section 6). The results are in `checks.json`: `checks` (`{"name", "status", "files", "detail"}`, `status` being `passed`, `failed` or `not_run`) and `not_run` (the project's own checks, with their command).
- Not run, left to the reviewer of the merge request with their command: the lint and the tests of the project that need its dependencies or its environment (phpcs, eslint, PHPUnit), and the git hooks of the repo. The push triggers the GitLab pipeline of the project; the merge request points to it.
- Nothing is presented as verified without having run.
