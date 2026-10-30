# Fix mode: conventions

The conventions of the fix mode (`/gm:checklist-quality-audit fix`, section 12 of SKILL.md), in one place. The orchestrator, the fixer (`agents/cqa-fixer.md`) and the fix reviewer (`agents/cqa-fix-reviewer.md`) all follow this file. These conventions belong to the skill: they never depend on the workstation (the lead's or another dev's `CLAUDE.md`, the defaults of the tool that runs the skill), and they win over any such default.

## 1. Keys, sections, passes and files

- A key is a repo, `<repo>` (`front`): the `key` of an entry of `fix/candidates.json`. A key has one branch, one worktree and, once pushed, one draft merge request for the whole pass. Its rows are grouped in `sections`, one per checklist category, each with the alias of section 3, SÉCURITÉ first, then the order of the copy. Each row records its `section` in `fix/state.json`.
- The sections of a key run one after the other in the same worktree. `cqa_fix.py section-start <run_dir> <key> --section <alias>` opens the next one; `cqa_fix.py section-end <run_dir> <key> --section <alias>` closes it once each of its rows is settled (fixed and reviewed, skipped, dropped, or a question set aside in autonomous mode). A section is fully settled (fixer passes, checks, guard, review, correction pass, drops) before the next one starts: the fixer of a section sees the fixes of the earlier ones, builds on them and never does them twice. A section that cannot reach a valid review (report or review still invalid after a new attempt, a problem of the workstation) ends with `--failed` and a reason: its commits leave the branch (refused when one is already pushed), its open rows become `dropped` with that reason, and the uncommitted changes of the worktree are saved in `leftover-<N>.patch` before the worktree is cleaned.
- The folder of a key is `fix/<repo>/` in the run directory:

| File | Written by | Content |
|---|---|---|
| `wt/` | `cqa_fix.py start` | the worktree of the key, on its branch |
| `commit-<N>.txt` | fixer | the message of its next commit in pass N, guarded before the commit (section 3) |
| `report-<N>.json` | fixer | its report of pass N (section 8) |
| `notes-<N>.json` | orchestrator | the answers and problems sent to pass N (below) |
| `review-<N>.json` | reviewer, or the orchestrator for a light review | the verdicts on pass N (section 8) |
| `checks.json`, `check/` | `cqa_fix.py check` | the checks of the changed files (section 9) |
| `leftover-<N>.patch` | `cqa_fix.py section-end --failed`, `cqa_fix.py abandon`, `cqa_fix.py open` | the uncommitted changes of a failed section, or of a worktree removed by `abandon` or by the next `open` (then under `fix/_history/<stamp>/<repo>/`) |
| `mr-title.txt`, `mr.md`, `mr-addition.md` | `cqa_fix.py mr-body` | the title and description of the merge request |

- A pass is one dispatch of the fixer on one section of a key, numbered from 1 over the whole key. The first pass of a section takes its selected rows. A later pass of the section takes the rows the lead answered (questions, section 5), the rows a check or the review sent back (correction, section 6), or the rows a drop took along (`retry`, section 6).
- Right before each fixer pass, `cqa_fix.py pass-start <run_dir> <key> --pass <N> --section <alias>` records the head of the branch as the base of pass N (`keys[<key>].bases["<N>"]` in `fix/state.json`) and the section the pass belongs to (`pass_sections["<N>"]`). A report holds the rows of that section only: `validate` refuses a row of another section (« Réf d'une autre section (<alias>) »), and every row of the section still `pending` must be in it. Every fixer pass N that leaves rows fixed is followed by its own review N, whose `Base` is that recorded head. Run again for a pass whose report is not validated yet (a resumed pass), `pass-start` keeps the base it recorded first, so that the commits of the interrupted run stay in `bases["<N>"]..HEAD`; it moves the base to `HEAD` only when that base is no longer in the branch. `drop` moves the recorded bases along with its rewrite. A key is never `reviewed` in the middle of a pass: `pass-start` on a `reviewed` key brings it back to `fixed`, and the key stays there until the report of the pass is validated.
- `cqa_fix.py validate <run_dir> <key> report --pass <N>` ties the report to its pass. Pass N must be started (`pass-start`) and still open: no report of a later pass validated, no review N recorded. Re-validating the report of the current pass before its review is allowed. A commit a fixed row reports for the first time (not yet among its commits in the state) must lie in `bases["<N>"]..HEAD`, otherwise « commit hors de la passe N : <sha> »: every reported commit is read by the review of its own pass. A row already `fixed` in an earlier pass p is reported again (a correction, or as it stands) only once review p is recorded, otherwise « ligne CQ-x : la revue de la passe p n'est pas enregistrée »: a row fixed again belongs to the new pass, whose review does not read its earlier commits. Every problem of a row comes out of one run. Likewise, a review needs the validated report of its pass: `validate ... review --pass <N>` refuses a pass not started or without its validated report.
- `cqa_fix.py validate <run_dir> <key> review --pass <N>` records the blocking rows of review N (`keys[<key>].reviews["<N>"]`) and the text corrections of its `ok` verdicts (section 8). The key reaches the step `reviewed`, which `mr-body` and `publish` need, only when no pass is in progress, no row is pending and, for every pass p that has rows fixed in it (`outcome` `fixed`, `pass` p), review p is recorded and blocks none of them. A pass is in progress from its `pass-start` until its report is validated (a base recorded for a pass later than every validated report): it keeps the key from `reviewed`, whatever `validate ... review` or `drop` record meanwhile. A row fixed again in a later pass belongs to that pass; a dropped row no longer counts. `validate ... report` and `drop` apply the same rule, so a pass that fixes no row needs no review: its key is `reviewed` at once when its earlier fixed rows are all reviewed (or when it has none), while a pass with new fixed rows waits for its review. The step moves back in three cases only: `start` brings a `done` key back to `started` (a key taken up again after its worktree was removed), `pass-start` brings a `reviewed` key back to `fixed`, and `validate ... report` sets the key to `fixed`, whatever its step, before the rule above may raise it at once. Otherwise that rule only raises the step (`validate ... review`, `drop`), and `check` moves a key below `checked` up to `checked`.
- The range of a key is `<start_sha>..HEAD` of its worktree (`keys[<key>].start_sha` in `fix/state.json`). The guard, the checks, `validate` and `drop` judge only those commits.
- Row outcomes in `fix/state.json`: `pending` (not reported yet, or back with `retry` true for one more attempt after a drop took it along), `fixed`, `skipped`, `question`, `dropped` (removed from the branch by `cqa_fix.py drop` or a failed section), `done_before` (already on a reused branch before this pass, never reported again).
- A key left to the lead (`keys[<key>].left` in `fix/state.json`: `{"reason", "at"}`, set by `cqa_fix.py leave`, null otherwise): a merge commit or a rewritten history in its branch, or a failed section that could not be rebuilt. Its branch and worktree stay as they are; `reorder`, `reword`, `mr-body`, `publish` and `finish` refuse it (exit 2) until `cqa_fix.py leave --clear`, and the summary names it. The record belongs to the pass: a new `open` starts without it.

**Dispatch of the fixer**, exactly:

`Run dir: <run_dir>. Fix: <repo>. Section: <alias>. Worktree: <path>. Pass: <N>. Rows: CQ-3, CQ-12. Notes: <path|none>. Stack core: <path|none>. Follow your instructions.`

`Fix` is the key; `Section` is the alias of the section of the pass, as `pass-start` recorded it; `Worktree` is the absolute path of `wt/`; `Rows` lists the rows of the pass, all of that section; `Notes` is the notes file of the pass, `none` when there is none (the first pass of a section, a retry); `Stack core` is the `path` of `stack_core["<repo>"]` in `fix/state.json`, `none` without one. That folder holds the core of WordPress or Drupal at the project's version when the repo lacks it (found, downloaded into the shared cache or given by the lead at the diagnostic). It is read-only for both sub-agents: they read it to check the core functions and their signatures, never write there and never run anything from it.

**Dispatch of the reviewer**, exactly:

`Run dir: <run_dir>. Review: <repo>. Worktree: <path>. Base: <sha>. Pass: <N>. Follow your instructions.`

`Base` is the head that `pass-start` recorded just before fixer pass N. The reviewer reads `<Base>..HEAD` and judges only the rows fixed in pass N (`pass` N in the state); when a later pass already ran, its commits are also in `<Base>..HEAD` and belong to its own review. The reviewer of pass N runs after `cqa_fix.py validate` has accepted `report-<N>.json`. It reads the stack core in `stack_core` of `fix/state.json`.

**Notes file** `fix/<repo>/notes-<N>.json`, written by the orchestrator before a pass that answers questions or corrects rows:

`{"answers": [{"ref", "answer"}], "problems": [{"ref", "source": "check"|"review", "detail"}]}`

`ref` is an integer (12 for CQ-12). `answer` is the lead's answer to the row's question: one of its options or a free text, in French. `detail` says the problem in French: for `check`, the failed check, the file and its message; for `review`, the reviewer's `notes`. A problem of the guard never goes in a notes file (section 6).

## 2. Branches

- One branch per pass and repo: `feature/checklist-qa-fix`. With `--dry-run`: `dry-run/checklist-qa-fix`. When the name is taken by a merged or discarded branch: `<name>-<n>`, the first free suffix (`feature/checklist-qa-fix-2`).
- `cqa_fix.py start` creates the branch and its worktree (`git worktree add --no-track`) from `origin/<base>` up to date (`develop`, else the default branch of the project), or takes over the pass branch of an open merge request of the repo, or an unmerged pass branch without one: new commits follow the old ones, never a rebase. The branch of a merged or closed merge request is never taken over. The local `develop` of the dev is never updated nor used, and their checkout (files, current branch, stash) is never touched.
- The branches of the v2 fix mode, one per category (`feature/checklist-qa-fix-<slug>`, `<slug>` being the slug of the category, `securite` or `seo-geo`), and their merge requests are still fixes in progress: their rows count as already in a merge request. They are never taken over as the branch of a pass.
- One merge request per pass and repo, its rows grouped by section. Before the publication, the orchestrator groups the unpushed commits by section (`cqa_fix.py reorder`; a conflict keeps the order and the summary says so), then rewrites the messages a `form` problem of the guard names (`cqa_fix.py reword`, section 6). Pushed commits are never reordered nor rewritten.
- Only `cqa_fix.py publish` pushes, and only a pass branch (`feature/checklist-qa-fix`, `feature/checklist-qa-fix-<n>`), as a fast-forward: never `--force`, never `push -u`, never towards `develop`, `main`, `master` or `release`. A `dry-run/` branch is never pushed and never reused by a real pass.
- The sub-agents never create, switch, move, rename, reset or delete a branch, and never push.

## 3. Commits

```bash
git -C "<worktree>" -c core.hooksPath=/dev/null -c core.fsmonitor=false add -- <path> [<path> ...]
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cqa_fix.py" guard "<run dir>" "<repo>" --staged --message-file "<message file>"
git -C "<worktree>" -c core.hooksPath=/dev/null -c core.fsmonitor=false commit -F "<message file>"
```

- Name the files of the change one by one (a deleted file too). Never `git add -A`, `git add .`, `git add -u`, `git commit -a`, never a wildcard.
- **Guard before the commit**: the fixer writes the message in `fix/<repo>/commit-<N>.txt` of the run directory (`<message file>`, an absolute path), then `cqa_fix.py guard <run_dir> <key> --staged --message-file <file>` checks the index of the worktree and that message with the rules of section 7, as the guard of the branch will check the commit. The commit takes the same file (`-F`): the message committed is the one guarded. The fixer never commits while the guard reports a problem: it corrects the message (a problem with `path` null, whatever its `kind`) or the change, then guards again. The one exception is a heuristic secret alert in a file (`suspect`, `waivable` true): the fixer may commit when every problem left is such an alert, each on a line it checked and that holds no written value (a value read at run time). It notes each one in the row's `attention`, « Alerte de secret à confirmer par le lead : <chemin>, ligne <n> » (the path and the line number, never the content of the line), and only the lead judges it, from the guard of the branch (section 6).
- `-c core.hooksPath=/dev/null`: the hooks of the repo (husky, GrumPHP, CaptainHook...) never run, since they rely on the dev's environment or on `node_modules`. The merge request declares them « Non lancé », with their command. `-c core.fsmonitor=false` does the same for a file monitor configured in the repo. Every git command of the fix mode in a worktree carries both options.
- **Subject**: `type(<alias> CQ-107): imperative summary`, in English, 72 characters at most, with a ref of the key and the alias of the row's section: SÉCURITÉ `securite`, QUALITÉ `qualite`, ACCESSIBILITÉ `accessibilite`, SEO / GEO `seo`, ÉCO-CONCEPTION / WEBPERFORMANCE `eco`, RGPD & LÉGAL `rgpd`, PROCESS INTERNE `process`, another category the first word of its slug. It must match `^(fix|feat|refactor|chore|perf|docs|style)\(([a-z0-9]+) CQ-([0-9]+)\): \S`. The v2 form `type(CQ-107): ...` is refused for a new commit.

| Type | Use |
|---|---|
| `fix` | corrects what the audit found wrong (most rows) |
| `feat` | adds what the audit found missing: a file, a header, a meta tag, an attribute |
| `perf` | a performance fix (lazy loading, image size, cache headers set in the code) |
| `refactor` | restructures code without changing what it does, when the row asks for it |
| `style` | formatting of the code only, no change of behaviour (not a CSS change: that is `fix` or `feat`) |
| `docs` | documentation only |
| `chore` | configuration or maintenance without effect on the site (`.gitignore`, `.editorconfig`, linter configuration) |

- **Body**: short, in English: what the audit found, then what the commit changes. Plain text, written in the message file with the Write tool (the shell never reads it). For example:

```text
fix(securite CQ-107): escape the search form label

The audit found the search form label printed without escaping.
Escape it with esc_html() in searchform.php.
```

- **No attribution line** of any kind: no `Co-Authored-By`, no `Claude-Session`, no « Generated with », no mention of an assistant or a tool. This rule of the skill wins over the defaults of the tool that runs it. The commits are authored with the lead's git identity (`user.name` and `user.email` of the repo): never `--author`, never `-c user.*`.
- **A commit carries one row only.** A row may take several commits, each with its ref. A change that also fixes another row of the section goes in the commit of the first row; the report of the second row names that row in `covered_by`, and the reviewer judges the commit for both.
- **History is never rewritten** by a sub-agent: no `--amend`, `rebase`, `reset`, `cherry-pick`, `revert`, `merge` or `tag`. A correction is a new commit on top. Only the orchestrator, before any push, removes or rewrites commits of the branch (`cqa_fix.py drop`, `reorder`, `reword`, `section-end --failed`).

## 4. Self-evaluation and the smallest fix

The fixer takes each row in this order.

1. **Recheck** the problem on the worktree: it holds `<base>` up to date plus the commits of the earlier sections, not the audited commit, and the lines of the evidence may have moved. Already fixed: `skipped`, « déjà corrigé sur develop » (name the base branch when it is not `develop`). A row proposed under every repo (`every_repo: true`) that does not concern this repo: `skipped`, « ne concerne pas ce repo ».
2. **Self-evaluate before writing any code.** The fixer does not attempt a row (`skipped`, with a short reason in French) when its fix:
   - would break a guard rail (section 7): third-party code, the core of the stack, a dependency manifest or lock file, a minified file, a secret;
   - needs a decision of the UI/UX team, the client or the contributors: mockup, content, texts and wording, colours and contrasts;
   - needs a change outside the repo, and only when nothing useful can be done in the repo: content in the database, a back-office setting, the server configuration, DNS. When part of the fix lies in the repo, the fixer does that part and `remaining` says the rest (step 3);
   - touches many files with a risk on the behaviour that the audit cannot check without the project's environment;
   - adds or upgrades a dependency (Composer, npm, a plugin, a module, a library). Without the project's environment, a lock file resolved with another PHP or Node version, or without the credentials of private repositories, could break the preprod. The reason gives the command for a dev, for example `composer update <paquet> --with-dependencies`;
   - is uncertain: the fixer is not sure the fix is right, or not sure it is safe.

   In doubt about safety, skip rather than ask: a question (section 5) is for a missing decision, never for a risk. The audit already leaves to the devs the rows whose whole fix lies outside the repos (`in_repo` false in its finding): they never reach the fixer, unless the lead added them (`lead` true).
3. **Fix at the smallest**: what the row asks, nothing more.
   - The style and idiom of the file and of the repo (indentation, naming, how hooks and services are registered) win over the ideal architecture of the stack guidance. No refactoring, reformatting or renaming beyond the fix.
   - Custom code only: extend through the stack's hooks, a child theme, a custom plugin or module, never by editing third-party or core code.
   - A user-facing string goes through the stack's translation API, with the text domain the code already uses.
   - Generated output (built CSS or JavaScript, compiled translations) is never edited by hand when the repo holds its source: change the source. When the change reaches the site only after a build the fixer cannot run, `attention` says so, with the build command found in the project.
   - A tracked file removed by the fix (a `phpinfo.php`, a stray backup): `attention` asks to check that the deployment removes it from the servers too.
   - A part of the row the fixer cannot do in the repo (the back office, another repo, a dependency, a decision of the team on that part): the fix does the rest, and `remaining` says what is left (section 8). A missing decision about what the fixer changes is a question (section 5), not `remaining`.
4. **Verify by reading** the diff against the « À faire » of the fiche and the `todo` of the finding, and run the « Comment tester » of the fiche on the code where the code shows it. Nothing runs in the project's environment (section 9), and no code is run to try it.
5. **Commit** (section 3), then the next row.

**Semi-auto rows** (`semi_auto: true`, « Semi-auto : MR à valider ») follow the same steps. When such a row is `fixed`, its `attention` says what the reviewer must check closely, from the « À valider » of the finding (`human_check`) and from the fix; the merge request marks it « à valider avec soin ». A Semi-auto row skipped needs no `attention`.

**Rows the lead added** (`lead: true`, a « Détection seule » or « Manuel » row the lead judged fixable) get the same discipline and no exception to the guard rails.

Reasons of a skipped row, in French, 300 characters at most, precise enough for a dev to act. For example:

| Case | Reason |
|---|---|
| already fixed | « déjà corrigé sur develop » |
| fixed by the commit of another row in an earlier pass, of this section or of an earlier one | « corrigé par le commit de CQ-<ref> d'une passe précédente » (listed under « Correctifs » of the merge request as covered by that row, not under « Hors-scope ») |
| other repo | « ne concerne pas ce repo » |
| third-party code | « le correctif touche le plugin tiers wp-content/plugins/<nom> : à signaler à l'éditeur ou à surcharger par un hook » |
| dependency | « montée de version à faire par un dev : composer update <paquet> --with-dependencies » |
| design decision | « choix de couleurs à valider par l'équipe UI/UX (contraste du bouton principal) » |
| outside the repo | « réglage de la configuration du serveur (en-têtes HTTP), hors du repo » |
| too wide | « correctif sur 14 gabarits, risque de régression invérifiable sans l'environnement du projet » |
| uncertain | « cause du constat non retrouvée dans le code de ce repo » |
| refused by the guard | « refusé par la garde : <raison> » (the reason the guard printed, never a value) |

## 5. Questions

- The fixer never asks a question and never waits for an answer: it works alone. When a real decision is missing, the row gets `outcome: question`, with `question` = `{"text", "options", "default"}`: the question in French, 2 to 4 options (200 characters at most each), and in `default` the option the fixer would apply without an answer. It then goes on with the next rows.
- A real decision is a choice between two valid fixes with a visible effect (on the pages, for the visitors, for the editors), or a value only the team knows: the contact of `security.txt`, the provider of the cookie banner, a legal mention.
- Not a question: a risk or a doubt about safety (skip, section 4), a dependency (skip, with the command), what the audit or the fiche already settles.
- **Options.** Never an option that leaves the row as it is (« Ne rien faire », « Ne pas corriger », « Laisser à un dev », « Pas de changement »): the orchestrator adds « Laisser aux devs » itself, and `validate` refuses such an option. Each option is checked in the code as if it were the fix, the `default` first, before it is offered: the files and lines it would change, what it points to (a page, a route, a URL and its parameters, a template, a function) exists and works, and it breaks nothing the code shows. An option that does not hold is left out; with fewer than two valid fixes left, there is no decision to ask: the fixer applies the one left when the audit and the fiche settle it, else skips the row.
- The `text` names the file and the element the decision is about (a template, a button, a route).
- A secret the fix needs (a key, a password, a token) never goes in the code: the row becomes a question on where the value comes from (an environment variable, a constant of the server configuration), or is skipped.
- An answered row is never asked again: the next pass applies the answer. A row the lead leaves to the devs gets no pass: the orchestrator drops it (`cqa_fix.py drop`, reason « laissée aux devs par le lead »; a row without commits is only marked `dropped`). A fixer that still meets such an answer skips the row with that reason.
- **Grouped questions.** Before asking, the orchestrator groups the questions of the pass that bear on the same file and the same element, whatever their section: one question to the lead, whose answer holds for each row concerned.
- Present mode: the orchestrator asks the questions of a section as soon as the fixer is done, one at a time, then runs a fixer pass on the answered rows (its own `pass-start`, and its own review when it fixes rows), before the guard and the review. Autonomous mode: each question is put aside with the kind `fix_question` and asked at the very end; the section goes on without the row, and the pass on the answered rows at the end belongs to the row's section, with its own `pass-start` and review in the same way. A row that the lead leaves to the devs, or leaves without answer at the end, goes to « Hors-scope » in the merge request: the orchestrator drops it with `cqa_fix.py drop`, reason « laissée aux devs par le lead » or « sans réponse du lead ».

## 6. Correction pass

- A row with a problem gets one more attempt: a failed check of `cqa_fix.py check` on its files, or a `blocking` verdict of the review. The orchestrator writes the problems in the notes file and dispatches a correction pass on those rows. Still a problem afterwards: the orchestrator removes the row from the branch (`cqa_fix.py drop --reason`), and the row goes to « Hors-scope » with the reason.
- The fixer answers a `check` or `review` problem with new commits on top of the branch, guarded before each commit (section 3), with the row's section and ref and a subject that says what the correction does. It reports the row `fixed` again, with all its commits: the earlier ones as `fix/state.json` lists them now (a drop may have rewritten the SHAs of an earlier report), plus the new ones; `change`, `uat` and `remaining` describe the whole fix.
- A text of the report alone (`change`, `uat`, `attention`, `remaining`) never needs a correction pass: the reviewer gives `ok` with `corrections` (section 8).
- **A problem of the guard never goes to the fixer.** `cqa_fix.py guard` reads the commits of `<start_sha>..HEAD`, and no new commit can change a commit already made; the fixer guards its index before each commit (section 3), so such a problem is rare. The orchestrator settles it by where it lies and by its `kind`, before the publication:
  - **The message**: a `form` problem (« message de commit hors convention », « Réf hors de cette MR », « section du commit différente de celle de la ligne », « sujet de plus de 72 caractères », « ligne d'attribution dans le message »), or a secret found in a message (`path` null and a `label`, whatever its `kind`, never `waivable`): the orchestrator rewrites the message with `cqa_fix.py reword` (unpushed commits only; the SHAs of the state, the bases and the reviews are remapped), keeping or setting the ref of the row the report attributes the commit to, then runs the guard again. The row stays.
  - **The change**: a `content` problem with a `path` (a forbidden path, a secret in a file, a dependency, a submodule): the orchestrator drops the row at once with `cqa_fix.py drop`, the guard reason as reason, without a correction pass.
  - **A heuristic alert in a file**: a `suspect` with `waivable` true (`credential_assignment`, `credential_define`), which the fixer noted in the row's `attention` when it committed it (section 3): only the lead judges it, from its path, line and label, never the value. A false positive is waived with `cqa_fix.py waive-secret` (section 7) and the row stays; a real secret is a `content` problem. The waiver is the lead's: no sub-agent ever runs it. A `suspect` in a file with `waivable` false is a `content` problem.
  - **No row**: a `content` problem with no `path` and no `label` (a merge commit, a rewritten history: its `ref` is null, or at most the ref a merge commit's subject carries) names no change a drop could remove, and it belongs to the whole branch: neither a drop nor a failed section (`cqa_fix.py section-end --failed`) can rebuild such a branch, both refuse it (exit 5). The fixer never merges nor rewrites history, so such a problem means the worktree was changed outside the pass: the orchestrator leaves the key (`cqa_fix.py leave --reason`: `fix/state.json` records it, and `reorder`, `reword`, `mr-body`, `publish` and `finish` refuse the key until `cqa_fix.py leave --clear`), and the lead removes the merge or restores the history by hand, or leaves the branch as it is. A secret found in a message has a `label`, even when the message carries no ref: it is a problem of the message, reworded as above.
- **Giving up**: a row with a commit on the branch can be neither `skipped` nor `question` (`validate` refuses it). The fixer reports it `fixed`, with its commits, `change` and `uat` as they stand, and an `attention` that starts with « Abandon : » followed by the reason in French, 300 characters at most in all, since it becomes the reason of the drop. The same holds in the first pass of a section for a row the fixer committed and then judged wrong, and for a `check` or `review` problem it cannot correct safely. The orchestrator drops such a row with that reason, without a further correction pass; a reviewer that meets one gives it `blocking` with that reason.
- **Rows taken along by a drop** (`also_dropped`: their fix depended on a removed commit) come back `pending` with `retry` true and the reason in `reason`, for one more fixer pass in their section, done as a first pass. After it, a failed check, a blocking review or a `content` problem of the guard makes them leave for good, without a correction pass; a problem of the message is reworded and a waivable alert goes to the lead, as for any row. A row taken along a second time leaves for good.
- A drop rewrites the commits that follow the dropped ones, which can take a recorded `Base` out of the branch: drop only once the reviews of the passes in progress are written.
- A correction pass on rows fixed in pass p runs only once review p is recorded: the checks, the guard and the review of a pass come first, then one correction pass with every problem found (`validate ... report` refuses a row of an earlier pass whose review is not recorded, section 1).
- A correction pass is checked, guarded and reviewed like any pass: its reviewer reads `<Base>..HEAD` and judges only the rows of its pass; the commits of a later pass in that range are not its concern.

## 7. Guard rails

Written in the instructions of the sub-agents, and checked in code: `cqa_fix.py guard` reads every commit of `<start_sha>..HEAD` before any push and prints one problem per commit and path, with the reason in French (quoted below) and its `kind`: `form` for the message, `suspect` for a heuristic secret alert (`credential_assignment`, `credential_define`, with its `line` and `waivable` true in a file), `content` for the paths, the other secrets, a merge commit or a rewritten history. A secret is reported per commit, path, line and label (`line` being the line where it starts); the lead judges every credential on that whole line, and one waiver covers the line for that label, as long as the file keeps the content the lead judged (the waiver is keyed on the blob of the path: a `drop` that leaves the file unchanged keeps it, any change voids it). Only the lead waives a `suspect`, with `cqa_fix.py waive-secret <run_dir> <key> --sha <sha> --path <path> --line <n> --label <label>`; no sub-agent ever runs it. `cqa_fix.py guard <run_dir> <key> --staged --message-file <file>` checks the index of the worktree and the planned message the same way, before the commit (section 3). Section 6 says how each problem found on the branch is settled.

- **Where to write**: only in the worktree of the run directory (`fix/<repo>/wt`) and in the files of the key's folder that belong to each agent. Never in the dev's folder, their local `develop`, or the stash the worktrees share with the dev's repo (hence never `git stash`). The fixer and the reviewer may read, but never write nor run anything in, the dev's repo (`repos[].path` of `context.json`): only its `vendor/`, `node_modules/` and the core of the stack, which the worktree lacks, to know the APIs the code calls. The stack core of `stack_core` (section 1) is read-only in the same way.
- **Scope of the sub-agents**: no command in the background, no search from `/` or outside the paths above, no code run, not even a trial (the fixer's syntax checks run no code), no read of another project.
- **No server, no project command**: no command on the preprod, the prod or any server (`ssh`, `scp`, `rsync`, a database client); nothing in the project's environment (`docker compose`, `make`, `composer`, `npm`, `yarn`, `pnpm`, `npx`, `drush`, `wp`, `php artisan`, a test suite, a linter of the project, a build); no network request. Notion is written only by the orchestrator, after the lead agreed.
- **Paths no commit may touch**:
  - the `third_party_paths` of the repo in `context.json` (« code tiers (third_party_paths) »);
  - `vendor/` and `node_modules/`, at any depth (« dossier de dépendances »);
  - the core and the contributed code of the stack (« cœur ou module contribué de la stack »). WordPress: `wp-admin` and `wp-includes` at any depth, `web/wp`, and the core files `wp-activate.php`, `wp-blog-header.php`, `wp-comments-post.php`, `wp-config-sample.php`, `wp-cron.php`, `wp-links-opml.php`, `wp-load.php`, `wp-login.php`, `wp-mail.php`, `wp-settings.php`, `wp-signup.php`, `wp-trackback.php` and `xmlrpc.php` at any depth (`wp-config.php` stays allowed). Drupal: `core`, `web/core`, `docroot/core`, `html/core`, every `contrib` folder at any depth, `libraries`, `web/libraries`, `docroot/libraries`, `html/libraries`. Another stack: the generic rules only;
  - git submodules and `.gitmodules` (« sous-module git »);
  - minified files, `*.min.*` (« fichier minifié »);
  - the dependency manifests and lock files (« dépendances : aucun ajout ni montée en v2.0 »): `composer.json`, `composer.lock`, `package.json`, `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`.
- **No secret** in the code or a message (« secret détecté (<label>) »): every file a commit changes is scanned whole, as committed, with the strict rules of the audit (the values the session knows and the common patterns), and a secret is reported when one of its lines is added by the commit; the messages are scanned whole. gitleaks of the toolbox reads the same content once per guard call (`cqa-toolbox.sh secrets <in_file> <out_dir>`: offline, input read-only, no credentials, values redacted from the report, the input and the report removed right after); its findings are « secret détecté (gitleaks:<RuleID>) », `content`, never waived, and an inline `gitleaks:allow` comment is ignored. Without Docker or the `gitleaks` image, the home rules run alone and the guard's `note` says « gitleaks indisponible : contrôle maison seul ». A secret value a fix needs gives a question or a skipped row, never a value in the code (section 5).
- **Dependencies**: no new dependency, no version change (major or not), no package script.
- **Git**: a subject `type(<alias> CQ-xx): ...` (« message de commit hors convention ») with a ref of the key (« Réf hors de cette MR ») and the alias of its section (« section du commit différente de celle de la ligne »), 72 characters at most (« sujet de plus de 72 caractères »); no merge commit (« commit de fusion »); no attribution line (« ligne d'attribution dans le message »); the history kept, `start_sha` staying an ancestor of `HEAD` (« historique réécrit : le départ de la passe n'est plus dans la branche »); no tag; push of the pass branches only, never `--force`, never towards `develop`, `main`, `master` or `release`.
- **GitLab**: a draft merge request assigned to the lead who runs the skill, without reviewer or label (the lead shares out the review), and the updates of its description. Nothing else: never Ready, approval, merge or closing. The merge requests go through `glab`, the tool of the skill as of `gm:merge-request`, whatever a workstation rule says about another merge request tool.

## 8. Report and review files

**Report** `fix/<repo>/report-<N>.json`, format `skills/checklist-quality-audit/schemas/fix-report.schema.json`, checked by `cqa_fix.py validate <run_dir> <key> report --pass <N>` (exit 6 with every error in French; a valid report updates the rows of `fix/state.json`):

- `key` (the repo) and `pass` as in the dispatch; one entry per row, `ref` as an integer (107 for CQ-107), each ref once, all of the section of the pass.
- Every row of `Rows:` and every row of that section still `pending` in the state is in the report. Rows `done_before` or `dropped` are not.
- `fixed`: `change` (what changed, in French, 600 characters at most), `uat` (the manual acceptance test, in French, 600 characters at most: where to go once deployed, what to do, what to see), `commits` (one or more SHAs of `<start_sha>..HEAD` whose subject carries the ref of the row, or the `covered_by` ref).
- `remaining` (`fixed` only, optional, in French, 300 characters at most): what is left to do when the commits fix the row in part, precise enough for a dev to act; `null` for a full fix. The merge request, the summary and Notion mark the row « partiel » with it, never as fully fixed; `change` says what was done.
- `skipped`: `reason` (in French, 300 characters at most). No commit of the row on the branch.
- `question`: `question` = `{"text", "options", "default"}` (section 5: no option that leaves the row as it is, each option checked in the code). No commit of the row on the branch.
- `covered_by` (`fixed` only): the ref of another row of the section whose commit also fixes this one.
- `attention` (optional, in French, 400 characters at most): what the reviewer of the merge request should look at closely; it reaches « Points d'attention » as « CQ-107 : <texte> ». Always set for a Semi-auto row reported `fixed` (section 4), for a row committed with a secret alert left to the lead (« Alerte de secret à confirmer par le lead : <chemin>, ligne <n> », section 3) and for a row given up (« Abandon : », 300 characters at most in all, section 6).

```json
{
  "key": "front",
  "pass": 1,
  "rows": [
    {
      "ref": 107,
      "outcome": "fixed",
      "change": "Échappe le libellé du formulaire de recherche avec esc_html() dans searchform.php.",
      "uat": "En préprod, ouvrir la page d'accueil, chercher <b>test</b> : le libellé affiche le texte tel quel, sans gras.",
      "remaining": "Le libellé du pied de page vient d'un widget réglé en back-office : y retirer le HTML saisi.",
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
      "remaining": null,
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
      "remaining": null,
      "commits": [],
      "reason": null,
      "question": {
        "text": "Quel contact mettre dans security.txt (fichier .well-known/security.txt) ?",
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
      "remaining": null,
      "commits": [],
      "reason": "montée de version à faire par un dev : composer update guzzlehttp/guzzle --with-dependencies",
      "question": null,
      "covered_by": null,
      "attention": null
    }
  ]
}
```

**Review** `fix/<repo>/review-<N>.json`, format `skills/checklist-quality-audit/schemas/fix-review.schema.json`, checked by `cqa_fix.py validate <run_dir> <key> review --pass <N>` (exit 6 with every error in French):

- `key` (the repo) and `pass` as in the dispatch; `rows` with one verdict per row the state shows `fixed` in pass N, and no verdict on another row; `attention`, a list (`[]` when there is nothing).
- `ok`: the fix may go in the merge request; `notes` optional. `ok` with `corrections`: the code is right, but a text of the report is wrong, missing or incomplete. `corrections` = `{"change", "uat", "attention", "remaining"}`, in French: each field given is the full new text, within the limits of the report (600, 600, 400 and 300 characters); a field left out or `null` keeps the report's text. `validate` records them in `rows["<ref>"].corrections` of the state, and the merge request, the summary and Notion use them instead of the report's texts: no correction pass for a text alone. A new report of the row drops them.
- `blocking`: the code must change; `notes` required, in French, 600 characters at most: what is wrong and what to change. The fixer reads it alone, in one attempt. `corrections` stays `null`.
- `attention`: the non-blocking remarks for « Points d'attention » of the merge request, in French, 400 characters at most each, starting with the ref when they concern one row.
- **Light review**: when `cqa_fix.py review-scope <run_dir> <key> --pass <N>` prints `light` true (the diff of the pass touches documentation only, `*.md`, `*.rst` or `*.adoc`, with 40 added plus removed lines at most), the orchestrator writes `review-<N>.json` itself, by the rules of the reviewer, instead of dispatching it.

```json
{
  "key": "front",
  "pass": 1,
  "rows": [
    {"ref": 107, "verdict": "ok", "notes": null, "corrections": {"uat": "En préprod, ouvrir la page d'accueil, chercher <b>test</b> dans le formulaire de recherche : le libellé et le résultat affichent le texte tel quel, sans gras."}},
    {"ref": 109, "verdict": "blocking", "notes": "Le champ de recherche de l'en-tête (header.php:42) affiche encore get_search_query() sans esc_attr() : l'échappement de CQ-107 ne le couvre pas. Ajouter esc_attr() dans header.php.", "corrections": null}
  ],
  "attention": ["CQ-107 : le libellé vient d'une option du thème, vérifier en préprod qu'aucun HTML voulu n'y était saisi."]
}
```

## 9. Checks before the merge request

- `cqa_fix.py check` (the orchestrator) runs in the worktree, without the project's environment: the syntax of each changed file (PHP, JavaScript, JSON, YAML) and stylelint with the project's configuration, in the toolbox image mounted read-only on the worktree; else with the host's binaries; else declared not run. A changed `.scss` that stylelint does not check with the project's configuration is parsed by postcss-scss (« Syntaxe SCSS (postcss-scss) »). The `*.conf` and `*.conf.template` of an `nginx` folder and any `nginx.conf` go through `nginx -t` (« Syntaxe nginx (nginx -t) ») and the `*.vcl` through `varnishd -C` (« Syntaxe Varnish (varnishd -C) »), in the official `nginx` and `varnish` images of the toolbox, offline, with dummy values for the `${VAR}` of the templates and the host names of the files mapped to the loopback; the same check runs on the folder as it was at the start of the pass, and an error already there is `not_run` « erreur déjà présente avant le correctif », only a new one fails. An error that comes from the image rather than from the change (a module, a certificate, an included file or a host of the project's environment missing, a snippet checked out of its context) is `not_run` « contrôle impossible hors de l'environnement du projet », even on a new file. Without those images, these checks are `not_run` with the reason. It never downloads the image (`docker run --pull never`): the orchestrator offers the download before the pass. A failure sends the rows of the file back to the fixer (section 6). The results are in `checks.json`: `checks` (`{"name", "status", "files", "detail"}`, `status` being `passed`, `failed` or `not_run`) and `not_run` (the project's own checks, with their command).
- The toolbox image checks the syntax with its own PHP and Node versions, not the project's: a construct newer than the image's version fails there although the project accepts it, and one the project's older version rejects can pass. Read a syntax failure with that in mind; the pipeline of the merge request runs the project's own versions.
- Not run, left to the reviewer of the merge request with their command: the lint and the tests of the project that need its dependencies or its environment (phpcs, eslint, PHPUnit), and the git hooks of the repo. The push triggers the GitLab pipeline of the project; the merge request points to it.
- Nothing is presented as verified without having run.
