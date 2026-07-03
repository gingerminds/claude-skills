# Drupal — dev

Backend development knowledge for Drupal 10/11. Assumes `core.md` is already loaded (standard, `t()`, Cache API vocabulary). Consumed by `/gm:drupal-11`.

## Core knowledge areas

Assume familiarity with:

- Drupal 10/11 internals and the Symfony components it builds on
- Plugin system, Dependency Injection Container
- Entity API, Typed Data API
- Config API, Cache API, Render API
- Event subscribers
- Services & service decoration
- Batch API, Queue API, Migrate API
- Access API, Multilingual API
- Routing & controllers
- Forms API (backend logic, not theming)

## Architecture mindset

- Business logic lives in **services**, never in controllers.
- Controllers **orchestrate**, not compute.
- Prefer event subscribers or services over procedural hooks when cleaner.
- Prefer dependency injection over static access.
- Design for testability; avoid tight coupling to Drupal — extract pure domain logic when feasible.

## Entity & data discipline

- Avoid loading full entities when only IDs are needed; use entity queries when possible.
- Be memory-aware for large datasets; consider batch or queue.
- Avoid N+1 loading patterns.
- Validate assumptions about entity fields and bundles; consider multilingual implications.

## Performance & cacheability

- Always reason about cacheability metadata; use `CacheableMetadata` when needed.
- Respect cache contexts, tags, and max-age; don't break render cache accidentally.
- Prefer lazy builders when relevant; think in terms of HTTP cache and reverse proxy.

## Configuration discipline

- Never hardcode configuration; use Config API properly.
- Separate runtime state from configuration; respect config schema.

## Security (dev-side)

- Validate and sanitize inputs; respect access checks — never bypass access control casually.
- Be mindful of XSS in render arrays; respect CSRF protection.
- Avoid exposing internal services.

## Testing

- Prefer **Kernel tests** for integration, **Unit tests** for domain logic.
- Mock Drupal dependencies when possible; avoid untestable static usage.
- Ensure new logic is covered.

## Service decoration (decorator strategy)

When altering behavior of an existing service:

- **Prefer service decoration** over class overrides or patching core/contrib — it adds behavior while delegating to the original.
- Decorators implement the same interface, inject the inner service explicitly (commonly `$inner`), stay small and single-purpose.
- Don't change public semantics silently unless the goal is explicit and documented; reason about decoration order when several exist; keep container compilation valid and cache-clear safe.
- When proposing one: show the YAML `decorates` definition, the constructor/delegation pattern, and the tests (unit for logic, kernel for wiring).

## Plugin-based extensibility

When functionality is expected to evolve or vary:

- **Prefer plugins** over switch/case, hardcoded registries, or bundle conditionals — for multiple implementations, runtime discovery, config-driven selection, or third-party extension points.
- Define a clear plugin interface; keep definitions minimal but descriptive; use the Plugin Manager for discovery + caching (it orchestrates, plugins execute).
- Inject dependencies via `ContainerFactoryPluginInterface`; provide a default and fail fast on resolution failure.
- When proposing one: describe the extension point (what varies vs stays stable), interface/base class only if it cuts duplication, how plugins are discovered/selected, and the tests.

## Event-driven alternatives to hooks

- **Prefer events/subscribers** when they improve cohesion, testability, and separation. Use hooks when the hook is the canonical extension point, no event exists, or you need small procedural glue.
- HTTP request/response → `EventSubscriber` (Symfony request events). Entity lifecycle → thin entity hooks, logic in services.
- Rule of thumb: hooks = procedural integration; events = decoupled listeners. Either way, **business logic belongs in services**.
- When proposing one: identify the right event/subsystem, give a subscriber skeleton with DI, note side effects/ordering and cache/security implications.
