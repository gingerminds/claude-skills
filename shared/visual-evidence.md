# Visual evidence — before/after screenshots for front changes

A reviewer can't judge a visual change from a diff. Whenever a change alters what a visitor (or an email recipient) **sees**, capture the rendering **before** and **after** and embed both in the merge request. Consumed by the stack dev skills (`/gm:drupal`, `/gm:vue`, `/gm:wordpress`…), `/gm:merge-request` and `/gm:review`.

## When it applies

The change touches the rendering, i.e. any of:

- templates (`*.html.twig`, `*.vue` templates, `*.blade.php`, theme PHP templates, JSX markup);
- styles (`*.scss`, `*.css`), front JS that changes the DOM or layout;
- images, SVG, icons, fonts, theme libraries;
- email templates or email assets;
- config that changes what a page displays (block placement, view mode, field display).

Pure backend changes (a service, a query, a cron) don't need it — say nothing, move on.

## Where the files live — and for how long

Screenshots are **never versioned**. They go to `<root>/.gm/handoff/<id>/screens/`, where `<root>` is `git rev-parse --show-toplevel` and `<id>` the ticket id (`sans-id` without one) — the same `.gm/` folder `/gm:ticket-refine` uses. Before writing the first file, exclude `.gm/` from git without touching the project's `.gitignore`:

```bash
root="$(git rev-parse --show-toplevel)"
SCREENS="$root/.gm/handoff/<id>/screens"
mkdir -p "$SCREENS"
exclude="$(git rev-parse --git-path info/exclude)"
mkdir -p "$(dirname "$exclude")"
grep -qxF '.gm/' "$exclude" || printf '\n.gm/\n' >> "$exclude"
git check-ignore -q "$SCREENS" || echo "WARNING: $SCREENS is not ignored by git"   # must print nothing
```

Never stage anything under `.gm/`, and never `git add -f` a screenshot.

**Lifetime**: the durable copy is the one uploaded to the MR. Once every upload succeeded and the MR description shows them, delete `$SCREENS` (`rm -rf "$SCREENS"`) — the purge is the job of whoever uploads, at upload time. If no MR is opened (work abandoned, user wants to review first), leave the folder and tell the user its path; it is ignored by git, so it can't leak into a commit.

## The rule that gets forgotten: "before" comes first

Capture **before you edit the first file**. As soon as the change classifies as presentation, list the pages (and email) the change affects, and take the "before" shots on the current code. Writing the code first and remembering afterwards is the usual failure.

Why not a `git worktree` on the base? The local site (Lando, docker compose…) serves the **main working tree**: a worktree isn't served, so a capture would still show the current code. Use a worktree only when the environment can actually serve it (a second stack pointed at it); otherwise recover a "before" in place, as below.

### Recovering a "before" after the fact

First check the state — the procedure differs, and a wrong guess captures the new code as "before":

```bash
git status --porcelain -- <files of this change>   # empty → the change is already committed
```

**Uncommitted change** — stash only this change's files, *including new untracked ones* (`-u`: a new logo or template must disappear too), and pop **only the stash you created**:

```bash
before=$(git rev-parse -q --verify refs/stash || true)
git stash push -u -m "gm-visual-before" -- <files of this change>
after=$(git rev-parse -q --verify refs/stash || true)
# rebuild assets + clear caches (see below), capture "before"
[ -n "$after" ] && [ "$after" != "$before" ] && git stash pop --index
```

Without the guard, a `stash push` that created nothing (files already committed) is followed by a `pop` that re-applies `stash@{0}` from another ticket. If the guard skipped the pop, stop and re-check the state — don't capture.

**Already committed** — the working tree must be clean (`git status --porcelain` empty; if it isn't, don't stash unrelated work — ask the user). Then check out the base, detached, and come back:

```bash
branch=$(git branch --show-current)
git switch --detach "$(git merge-base HEAD origin/<target branch>)"
# rebuild assets + clear caches, capture "before"
git switch "$branch"
# rebuild assets + clear caches again before the "after" shots
```

## Make sure the screenshot shows the code you think it shows

Between "before" and "after", **rebuild and flush**, or both shots show the same thing:

- front assets: the project's build (`make build`, `npm run build`, `gulp build`…, through the runner — see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`);
- CMS caches: e.g. Drupal `drush -l <site> cr`, WordPress object/page cache;
- browser cache: headless Chrome starts with a fresh profile, so it's fine; with a real browser, hard-reload.

## Capture

Local environment only (the MR lands on the integration branch, not preprod). Headless Chrome is the simplest path:

```bash
google-chrome --headless=new --hide-scrollbars --ignore-certificate-errors \
  --window-size=1440,1000 --screenshot="$SCREENS/before-<page>-desktop.png" "<local URL>"
# add a mobile shot (--window-size=390,844) when the layout or a breakpoint is affected
```

- Frame what changed: pick the window height (or a URL anchor) so the changed element is visible; a footer change needs a tall window or a full-page capture.
- Use Claude in Chrome or Playwright instead when the page needs a login, an interaction (open a menu, hover) or an element-level crop.
- **Emails**: trigger the email locally, open it in the local mail catcher (Mailhog/Mailpit) and capture that view.
- Name files `before-<page>-<viewport>.png` / `after-<page>-<viewport>.png`.

**Check the shots yourself**: `Read` each image and confirm it shows the target element and that before and after actually differ the way the ticket expects. A wrong or identical pair is worse than none.

## Embed in the merge request (GitLab)

Upload each image through the project uploads API, then paste the returned markdown. `glab api -F` does not send multipart, so use `curl` — with `--fail-with-body` and `jq -er`, so a failed upload stops loudly instead of yielding an empty or `null` link:

```bash
HOST=<gitlab host>                                   # e.g. gitlab.gingerminds.fr
PROJECT=$(glab repo view --output json | jq -er .id) # or the url-encoded "group%2Fproject"
TOKEN=$(glab config get token --host "$HOST")
for f in "$SCREENS"/*.png; do
  curl -sS --fail-with-body --header "PRIVATE-TOKEN: $TOKEN" \
    --form "file=@$f" \
    "https://$HOST/api/v4/projects/$PROJECT/uploads" | jq -er .markdown || { echo "upload failed: $f"; break; }
done
```

Then add the section to the MR description (create, or `glab mr update --description`):

```markdown
## Rendu avant / après
| Avant | Après |
|---|---|
| ![avant](/uploads/…/before-footer-desktop.png) | ![après](/uploads/…/after-footer-desktop.png) |
```

Once the description renders the images, purge `$SCREENS` (see *Lifetime*). If an upload fails, debug it (host, token, project id) — don't hand the upload back to the user while a working path exists. Only if it is truly blocked, say so, keep the folder and give its path.

## Non-goals

- No pixel-diff tooling or visual regression suite — two honest screenshots per affected view are enough.
- No screenshots of preprod/prod for an MR targeting the integration branch.
- Never commit screenshots, not even "temporarily".
