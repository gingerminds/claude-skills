# Drupal — review

Drupal-specific review checklist. Layered on top of the generic `gm:review` dimensions. Assumes `core.md` is loaded. Consumed by `/gm:review` and `/gm:merge-review`.

## Performance & cacheability

- Cache **contexts / tags / max-age** set correctly on dynamic output; `CacheableMetadata` propagated up the render array (not dropped).
- Render cache not broken by an un-varied dynamic value.
- **N+1 entity loads** — flag loops that load entities one by one.
- **Entity queries vs full loads** — loading full entities when only IDs/counts are needed.
- **Batch / queue** for large sets instead of processing everything in one request.

## Standards

- Match the file's **local idiom** (don't impose DI on a procedural `.module`, etc.).
- `phpcs --standard=Drupal,DrupalPractice` (run it via the runner, don't eyeball).
- **House rule:** every function is **typed** (params + return) and carries a **docblock (HEREDOC)** — flag any function missing either.

## Security (app-level)

- Input validation and **access checks** present (no casual bypass of access control).
- **XSS** in render arrays / Twig; SQL injection; **CSRF** protection on state-changing routes.
- No internal service exposure, no secrets in code.

## Language

- User-facing strings go through **`t()`** — flag hardcoded strings.
- Code (identifiers, comments, docblocks) in English.

> Dependency/advisory security (contrib CVEs, `drush pm:security`, Drupal.org SAs) is **not** here — that's `security.md`, consumed by `/gm:security`.
