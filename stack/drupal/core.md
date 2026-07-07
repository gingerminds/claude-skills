# Drupal — core (shared)

Baseline shared by every Drupal consumer (dev, review, security). Load this first, then the caller's nature.

## Coding standard

- **`phpcs --standard=Drupal,DrupalPractice`** is the reference standard. Run it via the project runner (see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`) rather than eyeballing.
- **Strict types** (`declare(strict_types=1);`) and typed signatures (params + return) where the file's idiom allows.
- **Meaningful PHPDoc** on functions/methods.
- **English** for all code: identifiers, comments, docblocks. **User-facing strings go through `t()`** (or `$this->t()`), never hardcoded.
- **Match the local idiom** of the file — don't impose DI on a fully procedural `.module`, don't rewrite a working procedural hook just for style.

## Cache API vocabulary

Shared cacheability vocabulary used both when building and when reviewing:

- Cacheability metadata = **contexts** (vary-by), **tags** (invalidate-by), **max-age**.
- Use `CacheableMetadata` to propagate it; don't drop it on the way up a render array.
- Render cache can be broken silently — every dynamic value needs the right context/tag.

## Detection

Drupal is identified by `drupal/core*` in `composer.json` (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`). Contrib/custom modules typically live under `web/modules/custom/*` (sometimes `modules/custom/*` or `docroot/...`).
