# Stack: Python (generic) — specifics

Entry point for generic Python specifics — projects **without** a web framework we cover (for Django, use `stack/django`). Form 1: single sectioned file. The caller reads `## core` plus the section for its nature.

Nature → section:

| Caller | Sections |
|---|---|
| `/gm:python` | core + dev |
| `/gm:review`, `/gm:merge-review` | core + review |
| `/gm:security` | core + security |
| `/gm:archi-c4` | core + archi |

Cross-stack resources: `${CLAUDE_PLUGIN_ROOT}/shared/runner.md`, `${CLAUDE_PLUGIN_ROOT}/shared/stack-detect.md`.

---

## core

- Python 3.11+, **PEP 8**; format/lint with black + ruff; type hints + mypy on new code.
- Packaging via `pyproject.toml`; dependencies pinned in a lockfile; run inside a virtualenv.
- Modules/packages have one clear responsibility; public API surfaced via `__init__.py` deliberately.
- Detection: `pyproject.toml` / `requirements*.txt` with no django (see `${CLAUDE_PLUGIN_ROOT}/shared/stack-detect.md`).

---

## dev

Consumed by `/gm:python`.

- **Structure**: small, focused modules; pure functions where possible; side effects at the edges.
- **Typing**: type hints on public functions; dataclasses/`TypedDict`/`pydantic` for structured data; avoid `Any` unless bridging untyped libs.
- **Errors**: raise specific exceptions; never bare `except:`; use context managers for resources.
- **Config**: read from env/config files, not hardcoded; keep I/O out of pure logic.
- **Testing**: `pytest` via the runner (`${CLAUDE_PLUGIN_ROOT}/shared/runner.md`); fixtures over setup boilerplate; test behaviour, not implementation.

---

## review

Consumed by `/gm:review`, `/gm:merge-review`. Layered on the generic dimensions.

- **Error handling** — bare `except:`, swallowed exceptions, resources not closed (no context manager).
- **Typing/clarity** — missing type hints on public functions; `Any` where a real type exists; mutable default arguments.
- **Structure** — logic and I/O tangled; god-modules doing too much.
- **Performance** — needless O(n²) loops, repeated work that belongs in a comprehension/generator, unbounded memory.
- **Standards** — black/ruff clean; mypy clean where configured.

---

## security

Consumed by `/gm:security`.

- **Injection/eval** — `eval`/`exec`/`pickle` on untrusted input; `subprocess` with `shell=True` + interpolated input.
- **Secrets** — credentials in code; secrets read from env only.
- **Dependencies** — `pip-audit` (or the lockfile-matching tool) on the tree; separate prod from dev/build deps.
- **Files/paths** — path traversal on user-supplied paths; unsafe temp-file handling.

---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

- **Custom code** = the project package(s) under `src/` or the top-level package. Entry points: CLI (`__main__`, console scripts), library public API. Orchestration (`pyproject.toml`, CI, Makefile) → containers (C2).
- **Never detailed (black box, `type: external`)**: `site-packages`/`venv` — all third-party libraries. Show only what custom code calls (DB, external API, filesystem) as `external` nodes.
- **Wiring source of truth**: module import graph (custom→custom `uses` edges); classes/functions as `component` nodes; entry points (CLI/API) as roots. Stop at the third-party boundary.
