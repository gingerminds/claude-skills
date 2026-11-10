# Stack: React (incl. Next.js) — specifics

Entry point for React specifics (Form 1: single sectioned file). The caller reads `## core` plus the section for its nature. If a section grows too large it can be promoted to a sibling file (Form 2) without touching the callers.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:react` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_SKILL_DIR}/../../shared/runner.md`, `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md` (anchored on the calling skill's base dir).

---

## core

Shared baseline (all natures):

- **Function components + hooks** as the default; no class components in new code unless the codebase is legacy class-based (match the surrounding style rather than fighting it).
- TypeScript strict mode (`.tsx`), no `any` unless interfacing an untyped third-party API. Typed props via an interface/type, never `React.FC` with implicit `children` (prefer explicit `children?: React.ReactNode`).
- Follow the [Rules of Hooks](https://react.dev/warnings/invalid-hook-call-warning): only call hooks at the top level, only from React functions/custom hooks.
- Lint via the project runner (see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`): eslint (`eslint-plugin-react`, `eslint-plugin-react-hooks` — `exhaustive-deps` as an error, not a warning), `tsc --noEmit` for type checks.
- Detection: `react` / `react-dom` in `package.json` (see `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`). Next.js is detected the same way — `next` is itself a dependency; treat a Next.js app as React with the Next-specific dev/review notes below layered on top.

---

## dev

Consumed by `/gm:react`.

### Core knowledge areas

- Hooks: `useState`, `useEffect`, `useMemo`, `useCallback`, `useRef`, `useReducer`, `useContext`, `useId`, `useTransition`/`useDeferredValue`; writing custom hooks.
- Rendering model: reconciliation, keys, Suspense, concurrent features (`useTransition`, streaming).
- Component communication: props, callbacks, context, composition (children/render props) over inheritance.
- Next.js App Router: Server Components vs Client Components (`"use client"` boundary), Server Actions, route handlers (`app/api/**`), layouts/templates, `loading.tsx`/`error.tsx`, middleware, `next/image`, `next/link`.
- Data fetching: `fetch` in Server Components (with `cache`/`revalidate`), TanStack Query (React Query) for client-side server-state caching, SWR as an alternative.
- Client state: `useState`/`useReducer` for local; Context for cross-tree app state; Zustand/Redux Toolkit/Jotai for larger shared state — pick the lightest tool that fits, don't default to Redux.
- Forms: React Hook Form (or native controlled inputs for simple cases) + a schema validator (Zod/Yup).
- Routing: Next.js file-based routing, or React Router (`createBrowserRouter`, loaders/actions) for non-Next SPAs.
- Code-splitting: `React.lazy` + `Suspense`, Next.js automatic route-level splitting, `next/dynamic`.
- Testing: React Testing Library (behavior-first), Jest or Vitest as the runner, MSW for mocking network at the boundary.

### Architecture mindset

- Business logic lives in **custom hooks** or a **service/query layer**, never inline in the component body beyond simple derivations.
- Components **orchestrate** — call hooks, bind handlers, render; they don't fetch or compute business rules directly.
- Prefer composition (children, render props, custom hooks) over deep inheritance or over-abstracted HOCs.
- Keep **server state** (data from an API/DB, cached via React Query or Server Components) and **client state** (UI-only: open/closed, selected tab) clearly separate — don't mirror server state into `useState` by hand.
- Avoid prop-drilling beyond two or three levels; reach for Context or a store instead.

### Component design

- Keep components focused: one responsibility, one visual concern; extract a sub-component rather than growing a deep conditional tree.
- Typed props via an interface; destructure props in the signature; default values via default parameters, not `defaultProps` (legacy API).
- Avoid recreating objects/functions/arrays inline in JSX when they defeat memoization on a hot path (`useMemo`/`useCallback` deliberately, not reflexively — don't memoize everything).
- Lists: a **stable, unique `key`** (never array index if the list can reorder/filter/insert).

### Custom hooks

- `use` prefix; one file per hook when it grows past a few lines. Pure with respect to render — side effects belong inside `useEffect`, not in the hook body directly.
- Return a stable shape (an object or tuple, consistently) so callers don't guess. Handle cleanup (`return () => {...}` in effects) for subscriptions/timers/listeners.
- Watch the dependency array: `exhaustive-deps` findings are almost always real bugs (stale closure), not lint noise to silence with a blanket `// eslint-disable`.

### Next.js / SSR specifics

- Always reason about **where code runs**: Server Component (default in the App Router), Client Component (`"use client"`), or a Server Action.
- Never access browser globals (`window`, `document`, `localStorage`) in a Server Component or at module top-level shared with the server. Guard client-only code, or move it into a Client Component.
- Server Components fetch data directly (no `useEffect`/`useState` dance); Client Components use React Query/SWR or props passed down from a Server Component parent.
- `NEXT_PUBLIC_*` env vars ship to the browser bundle — never put a secret there; keep secrets server-only (Server Components, Server Actions, route handlers).

### Testing

- **Component tests** with React Testing Library: query by role/text like a user would, not by implementation detail (avoid `data-testid` as a first resort).
- **Hook tests** via `renderHook` for pure custom-hook logic in isolation.
- Mock network at the HTTP boundary (MSW) rather than mocking `fetch`/the query client internals; avoid snapshot tests beyond trivial static markup.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions.

- **Hooks correctness** — Rules of Hooks violations (conditional/looped hook calls); missing or incorrect `useEffect`/`useMemo`/`useCallback` dependencies (stale closures reading old state/props); effects with side effects but no cleanup (leaked subscriptions/listeners/timers).
- **Rendering / performance** — unstable inline objects/functions passed as props to memoized children (defeats `React.memo`); missing/incorrect `key` on dynamic lists; unnecessary `useEffect` to derive state that should be a plain computation or `useMemo`.
- **State placement** — server-state data manually mirrored into `useState` instead of a query cache; state lifted too high (causing broad re-renders) or too low (duplicated across siblings).
- **Next.js boundary** — browser globals or `useState`/`useEffect` used inside a Server Component; a secret read from a `NEXT_PUBLIC_*` var or otherwise reaching a Client Component; unnecessary `"use client"` pushed higher up the tree than needed.
- **Standards** — TypeScript strict, no `any`, hooks respect their rules. Run eslint (`react-hooks/exhaustive-deps`) / `tsc` rather than eyeballing.

---

## security

Consumed by `/gm:security`.

- **XSS via `dangerouslySetInnerHTML`** — flag any use on non-trusted/non-sanitized content; prefer text content or a sanitizer (DOMPurify) if HTML rendering is genuinely required.
- **Client-exposed secrets** — anything under `NEXT_PUBLIC_*` (Next.js) or `VITE_*` (Vite — only that prefix is inlined into `import.meta.env` client-side, everything else stays server/build-only) ships to every visitor; audit for secrets accidentally given one of these prefixes.
- **Dependencies** — `npm audit` (or the lockfile-matching runner) on the JS tree; separate prod from dev/build-time deps.
- **Server Actions / route handlers** — treat them like any other server endpoint: validate input, check auth/ownership, don't trust client-supplied IDs blindly.

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

### Where custom code lives (what we detail)

- The whole app repo is custom. Next.js (App Router): `app/` (routes, layouts, Server Components, route handlers, Server Actions), `components/`, `hooks/`, `lib/`/`services/`, `context/` or `store/`. Plain React (Vite/CRA): `src/**`.
- Build/orchestration (`next.config.*`, `vite.config.*`, `Makefile`, CI) → **containers** (C2).

**Never detailed (black box, `type: external`)**: `node_modules/**` — React runtime, UI libraries, any dependency. Show only what custom code imports/calls (an external API, a headless CMS, a DB, an auth provider) as `external` nodes.

### Wiring source of truth

1. **Custom hooks** (`useX`) — `component` nodes; a component/hook importing another = `uses` edge.
2. **Context providers / stores** (Context, Zustand, Redux) — each provider/store = a `component` node; component→store `uses` edges.
3. **Server Actions / route handlers** (`app/api/**`, `actions.ts`) — entry points; external calls (DB, upstream API) = `external` nodes.
4. **Routing** (`app/**` file-based routes, or React Router config) — entry points; middleware/guards as `component` nodes.
5. **Component import graph** — parent→child and component→hook `uses` edges; stop at `node_modules`.

Typical containers (C2): the Next.js/Vite app, and any external API/CMS/DB/auth provider the app talks to.

### C4 splitting (code level, on request)

Group by feature/domain into `CODE.views` (e.g. a feature's components, hooks, and route handlers). React has no interfaces beyond TS types: represent hooks/stores as `class`-kind nodes and shared TS contracts (props types, API response types) as `interface`-kind nodes, linked by `assoc`/`use`.
