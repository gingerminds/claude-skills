---
name: merge-request
description: Prepares a GitLab merge request — a scoped commit, a branch push, and `glab mr create` with a structured description that references the ticket. Defaults to a Draft MR targeting the integration branch. Use when the user asks to prepare/open/create a MR or PR after a fix, or invokes /gm:merge-request.
---

# Clara — Prepare Merge Request

Turn a finished change into a clean, reviewable GitLab MR. Outward-facing: create the MR as a **Draft** by default and never mark it Ready or merge without explicit user go-ahead.

## Preflight

1. **Branch** — Work on a dedicated branch, never the base. Convention: `fix/<ticket>-<slug>` (or `feat/<ticket>-<slug>`). If currently on the base branch (`develop`/`main`), create the branch first.
2. **Base branch** — Default target is `develop` (fall back to the repo's default branch). Confirm from existing branches/MRs if unsure.
3. **Clean staging** — Stage **only** the files belonging to this change. Inspect `git status` and explicitly exclude unrelated working-tree edits (leave them unstaged — never `git add -A` blindly).
4. **Reuse the review** — If `/gm:review` just ran, lift its verdict, **Vérification ✅** checks, and **Hors-scope** notes straight into the MR description instead of re-deriving them.

## Commit

One focused commit. Message convention (match the repo's history):

```
type(scope: #<ticket>): <imperative summary>

<body: what was wrong, the root cause, and what the fix does — wrapped ~72 cols>
```

`type` ∈ fix / feat / refactor / chore. Verify the staged set before committing:

```bash
git diff --cached --name-only   # must contain only the intended files
```

## Push & create MR

```bash
git push -u origin <branch>
GITLAB_HOST=<host> glab auth status                       # confirm auth + host
GITLAB_HOST=<host> glab mr list --source-branch <branch>  # bail if one already exists — update it, don't duplicate

DESC=$(cat <<'EOF'
<the MR description template below, filled in>
EOF
)

GITLAB_HOST=<host> glab mr create \
  --source-branch <branch> \
  --target-branch <base> \
  --title "<commit summary>" \
  --description "$DESC" \
  --draft --yes
```

Set `GITLAB_HOST` when the remote is self-hosted (e.g. `gitlab.gingerminds.fr`). Build `$DESC` with the heredoc above so the multi-line markdown survives the shell. `--draft` is the canonical Draft flag (don't fake it with a `Draft:` title prefix); `--yes` skips the interactive prompt. If `glab mr list` shows an open MR for the branch, update it instead of creating a second one.

## MR description template

This fills `$DESC` above. Reference the tracker (Mantis/Jira/issue) with a real link. Structure:

```markdown
## Contexte
<ticket link> — <one-paragraph problem statement>

## Cause racine
<root cause; name the commits/routes/services involved>

## Correctif
<what changed and why it's correct; what is intentionally left unchanged>

## Vérification
- ✅ <empirical check that ran>
- ✅ phpcs / php -l / tests as applicable

### UAT manuelle
<exact steps a human follows to confirm>

## Hors-scope
<dead code / latent issues for a separate ticket>
```

## Finish

Return the MR URL. State plainly that it's a **Draft** and that UAT + "mark Ready" are the user's to do. Report what was committed and what was deliberately excluded.

## Non-goals

- Never `git add -A` over unrelated changes; commit only the change at hand.
- Never mark Ready, approve, or merge without explicit instruction.
- Never push to the base branch directly.
