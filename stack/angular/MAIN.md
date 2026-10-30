# Stack: Angular — specifics

Entry point for Angular specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. If a section grows too large it can be promoted to a sibling file (Form 2) without touching the callers.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:angular` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md` (anchored on the calling skill's base dir).

---

## core

Shared baseline (all natures):

- **Standalone components** as the default for new code (Angular 15+ standalone APIs, `bootstrapApplication`) — only follow an `NgModule` structure when the codebase is already organized that way; match the surrounding style rather than migrating mid-ticket.
- TypeScript strict mode; typed inputs/outputs via `@Input()`/`@Output()` or the newer signal-based `input()`/`output()` APIs (Angular 17+) — pick whichever the codebase already uses.
- `OnPush` change detection by default on new components unless there's a concrete reason not to.
- Lint via the project runner (see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`): `angular-eslint`, `tsc` (via `ng build`/`ng lint`).
- Detection: `@angular/core` in `package.json` (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`).

---

## dev

Consumed by `/gm:angular`.

### Core knowledge areas

- Components & templates: property/event binding, structural directives (`*ngIf`/`@if`, `*ngFor`/`@for`, the newer built-in control-flow syntax), attribute directives, pipes (pure by default).
- Dependency injection: `providedIn: 'root'` services, component/route-level providers, injection tokens (`InjectionToken`) for config, `inject()` function vs constructor injection.
- RxJS: Observables, common operators (`map`, `switchMap`, `mergeMap`, `combineLatest`, `debounceTime`), the `async` pipe, subscription lifecycle.
- Signals (Angular 17+): `signal()`, `computed()`, `effect()`, interop with RxJS (`toSignal`/`toObservable`) — the direction the framework is moving for reactivity and change detection.
- Angular Router: route config, guards (`CanActivate`/functional guards), resolvers, lazy-loaded routes (`loadComponent`/`loadChildren`), route params/query params via `ActivatedRoute` or the `input()` route binding.
- Forms: Reactive Forms (`FormGroup`/`FormControl`, strictly typed since Angular 14) preferred for anything non-trivial; template-driven forms for simple cases; custom validators.
- Change detection: `OnPush` strategy, signals reducing the need for manual `markForCheck`, zoneless change detection (experimental) as the direction to be aware of.
- HTTP: `HttpClient`, interceptors (functional interceptors in modern Angular) for auth/error handling, typed responses.
- Testing: `TestBed`, Jasmine/Jest, Angular Testing Library, marble testing for RxJS-heavy logic.

### Architecture mindset

- Business logic and side effects live in **services**, injected via DI — components stay thin (bind, render, delegate).
- Favor a **smart/container vs. presentational** split for anything beyond a trivial component: a container resolves data/state, presentational components just render inputs and emit outputs.
- Prefer **signals or the `async` pipe** to subscribing manually in a component (`.subscribe()` in `ngOnInit` without unsubscribe management is the single most common leak in Angular codebases).
- Use DI (injection tokens, interfaces) to keep services swappable/testable rather than importing concrete implementations directly across layers.

### Component design

- One responsibility per component; extract a child component rather than growing template complexity.
- `OnPush` + immutable data patterns (new references on change, not in-place mutation) so change detection actually catches updates.
- Typed `@Input()`/`@Output()` (or `input()`/`output()`); avoid `any` in template bindings.
- Use the built-in control-flow syntax (`@if`/`@for`/`@switch`) in new templates on Angular 17+; keep `*ngIf`/`*ngFor` only where the codebase hasn't migrated yet.

### RxJS discipline

- Always have an unsubscription strategy: `async` pipe (preferred, no manual subscribe), `takeUntilDestroyed()` (Angular 16+, works with `DestroyRef`), or a manual `Subject` + `takeUntil` in older codebases — never a bare `.subscribe()` with no teardown on a long-lived component.
- Avoid nested `.subscribe()` calls (a subscription starting another subscription) — flatten with `switchMap`/`mergeMap`/`concatMap` chosen deliberately for the concurrency behavior needed (cancel-previous vs queue vs run-all).
- Keep templates free of subscription side effects — bind via `async` pipe or a signal, don't call `.subscribe()` from the template.

### Services & DI

- `providedIn: 'root'` for app-wide singletons; component-level `providers` for scoped instances (e.g. one per feature/dialog).
- Injection tokens for configuration values or to decouple a service behind an interface for testing.
- Keep HTTP calls and business rules in services, not components — a component should be testable by mocking the service, not by mocking `HttpClient` directly.

### Forms

- Reactive Forms for anything with validation, dynamic fields, or that needs to be unit-tested without the DOM; typed `FormGroup<{...}>` (Angular 14+ strictly-typed forms).
- Custom validators as pure functions (`ValidatorFn`); async validators for anything hitting the network (debounce first).

### Testing

- `TestBed.configureTestingModule` with the component/service under test; override providers with mocks/stubs via DI rather than reaching into internals.
- Test services in isolation (plain instantiation or `TestBed.inject`) before testing components that consume them.
- Marble testing (`TestScheduler`) for RxJS operators/pipelines with real timing behavior; avoid over-mocking `HttpClient` — use `HttpClientTestingModule`/`provideHttpClientTesting`.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions.

- **RxJS leaks** — a `.subscribe()` with no `takeUntilDestroyed`/`takeUntil`/`async` pipe on a component that can be destroyed while the observable is still alive; nested subscribes that should be a flattening operator.
- **Change detection** — missing `OnPush` on a component that could use it; in-place mutation of an `@Input()` object/array that `OnPush` won't catch; unnecessary `markForCheck`/`detectChanges` papering over a reactivity gap instead of fixing it.
- **DI / architecture** — business logic or HTTP calls inline in a component instead of a service; a service injected but the component still reaches past it into `HttpClient`/browser APIs directly.
- **Templates** — a function call in a template re-evaluated every check when it should be a `computed()`/pure pipe/memoized value; structural directive misuse (`*ngFor` without `trackBy` on a large/reorderable list).
- **Standards** — strict typing on inputs/outputs and forms; run `angular-eslint`/`ng lint` rather than eyeballing.

---

## security

Consumed by `/gm:security`.

- **XSS via `[innerHTML]` / `bypassSecurityTrustHtml`** — flag any use on non-trusted content; prefer Angular's default sanitization (don't bypass it) or a proper sanitizer if raw HTML is genuinely required.
- **Client-exposed config** — anything in `environment.ts`/`environment.prod.ts` ships in the client bundle; no secrets there, only public config (API base URLs, feature flags).
- **HTTP interceptors** — auth tokens attached/refreshed correctly, not logged; error interceptor doesn't leak stack traces or internal details to the UI.
- **Dependencies** — `npm audit` (or the lockfile-matching runner) on the JS tree; separate prod from dev/build-time deps.

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

### Where custom code lives (what we detail)

- The whole app repo is custom: `components/` (or feature folders with co-located standalone components), `services/`, `guards/`, `interceptors/`, `pipes/`, `directives/`, a `store/` if NgRx/signal-based state is used.
- Build/orchestration (`angular.json`, `Makefile`, CI) → **containers** (C2).

**Never detailed (black box, `type: external`)**: `node_modules/**` — Angular runtime, UI libraries, any dependency. Show only what custom code imports/calls (an external API, an auth provider, a DB) as `external` nodes.

### Wiring source of truth

1. **Services** (`@Injectable`) — each service = a `component` node; service→service and component→service DI edges = `uses` edges.
2. **Guards / resolvers / interceptors** — `component` nodes wired into the router config or the HTTP client pipeline.
3. **Router config** (`app.routes.ts` or `RouterModule.forRoot`) — entry points; lazy-loaded feature routes as separate `component` nodes.
4. **Store (NgRx/signal-based)**, if present — actions/effects/selectors as `component` nodes with `uses` edges to/from services and components.
5. **Component template/import graph** — parent→child and component→service `uses` edges; stop at `node_modules`.

Typical containers (C2): the Angular app, and any external API/auth provider/DB the app talks to.

### C4 splitting (code level, on request)

Group by feature/domain into `CODE.views` (e.g. a feature's components, services, and store slice). Represent services/stores as `class`-kind nodes and shared TS contracts (interfaces, DTOs) as `interface`-kind nodes, linked by `assoc`/`use`.
