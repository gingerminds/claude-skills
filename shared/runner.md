# Project runner — priority order

Many commands (linters, `composer`, `drush`, `phpcs`, `npm`…) must run **inside the project environment**, not on the host (the host PHP/Node version rarely matches). Detect the runner and **prefix** your commands with the first one available:

1. **`make`** — if a `Makefile` exposes the target: `make lint`, `make test`, `make phpcs`… This is the agency's standard entry point; prefer it.
2. **`docker compose`** — otherwise: `docker compose exec <service> <cmd>` (e.g. `docker compose exec php composer audit`).
3. **`lando`** — **last resort only**: `lando <cmd>`.
   > ⚠️ **Temporary.** Projects still on Lando are slated to migrate to Docker. Never suggest Lando first, and don't add any new dependency on Lando.

## Rules

- **Strict order** make → docker → lando. Only fall through to the next when the previous is absent (no make target → no compose → then lando).
- Some read-only commands against a committed file run fine on the host too (e.g. `composer audit --locked` reads `composer.lock`) — the host is an acceptable fallback when no runner is available.
- Never claim a check passed if it didn't actually run for lack of a runner: say "(unavailable)" and state what's missing.
