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

## The rule that gets forgotten: "before" comes first

Capture **before you edit the first file**. As soon as the change classifies as presentation, list the pages (and email) the change affects, and take the "before" shots on the current code. Writing the code first and remembering afterwards is the usual failure.

If the edits already exist, recover a "before" without losing work:

```bash
git stash push -- <files of this change>   # only this change's files — never a blind `git stash` over unrelated edits
# rebuild assets + clear caches (see below), capture "before"
git stash pop
```

## Make sure the screenshot shows the code you think it shows

Between "before" and "after", **rebuild and flush**, or both shots show the same thing:

- front assets: the project's build (`make build`, `npm run build`, `gulp build`…, through the runner — see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`);
- CMS caches: e.g. Drupal `drush -l <site> cr`, WordPress object/page cache;
- browser cache: headless Chrome starts with a fresh profile, so it's fine; with a real browser, hard-reload.

## Capture

Local environment only (the MR lands on the integration branch, not preprod). Headless Chrome is the simplest path:

```bash
OUT=<scratchpad>/screens
google-chrome --headless=new --hide-scrollbars --ignore-certificate-errors \
  --window-size=1440,1000 --screenshot="$OUT/before-<page>-desktop.png" "<local URL>"
# add a mobile shot (--window-size=390,844) when the layout or a breakpoint is affected
```

- Frame what changed: pick the window height (or scroll target URL with an anchor) so the changed element is visible; a footer change needs a tall window or a full-page capture.
- Use Claude in Chrome or Playwright instead when the page needs a login, an interaction (open a menu, hover) or an element-level crop.
- **Emails**: trigger the email locally, open it in the local mail catcher (Mailhog/Mailpit) and capture that view.
- Name files `before-<page>-<viewport>.png` / `after-<page>-<viewport>.png`, in the session scratchpad. **Never commit them** to the repo.

**Check the shots yourself**: `Read` each image and confirm it shows the target element and that before and after actually differ the way the ticket expects. A wrong or identical pair is worse than none.

## Embed in the merge request (GitLab)

Upload each image through the project uploads API, then paste the returned markdown. `glab api -F` does not send multipart, so use `curl`:

```bash
HOST=<gitlab host>                                   # e.g. gitlab.gingerminds.fr
PROJECT=$(glab repo view --output json | jq -r .id)  # or the url-encoded "group%2Fproject"
TOKEN=$(glab config get token --host "$HOST")
curl -sS --header "PRIVATE-TOKEN: $TOKEN" \
  --form "file=@$OUT/before-<page>-desktop.png" \
  "https://$HOST/api/v4/projects/$PROJECT/uploads" | jq -r .markdown
```

Then add the section to the MR description (create, or `glab mr update --description`):

```markdown
## Rendu avant / après
| Avant | Après |
|---|---|
| ![avant](/uploads/…/before-footer-desktop.png) | ![après](/uploads/…/after-footer-desktop.png) |
```

If the upload fails, debug it (host, token, project id) — don't hand the upload back to the user while a working path exists. Only if it is truly blocked, say so and give the local file paths.

## Non-goals

- No pixel-diff tooling or visual regression suite — two honest screenshots per affected view are enough.
- No screenshots of preprod/prod for an MR targeting the integration branch.
