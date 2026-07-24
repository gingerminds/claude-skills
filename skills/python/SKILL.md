---
name: python
description: Guides generic Python development (no web framework) with packaging, typing, module structure, error handling, and testing. Use when implementing or reviewing plain Python code, libraries, CLIs, or scripts — not Django (use /gm:django) — or when the user asks for Python expertise.
---

# Python Expert (generic)

Entry point for generic Python development — libraries, CLIs, scripts, and services **without** a web framework we cover (for Django, use `/gm:django`). This skill carries the **workflow**; the Python knowledge itself lives in the shared stack resource so `/gm:review`, `/gm:security` and this skill draw from one source.

## Load the Python knowledge

Load `${CLAUDE_SKILL_DIR}/../../stack/python/MAIN.md` for the **dev** nature — read its `## core` + `## dev` sections (packaging, typing, structure, errors, testing). When the ticket reaches beyond plain feature work (security implications, or a change where standards/regressions are the crux), also read the `## security` or `## review` section so you build with the right lens from the start.

## Start from the ticket

If the work comes from a ticket and you don't already have its intent in context, run **`/gm:ticket`** first — it digests the ticket into a brief (goal, "À regarder", acceptance criteria) that tells you where to focus the survey below. If the user already gave you the intent, carry on.

## Survey the existing custom code first

Before writing any code, **read the project's existing modules** to build a picture of what already exists — don't reinvent or duplicate. When a ticket brief exists, let its "À regarder" list steer where you look first. Unless you've already done this in the current session:

- Skim the top-level package (`src/<pkg>/` or the package directory) and its `pyproject.toml` for entry points and dependencies.
- Identify reusable **modules, classes, and functions** you could extend or reuse rather than rebuild.
- Note the **local idioms** (typing style, error handling, structure) so new code matches the surrounding style.
- Surface any existing code your change should touch, extend, or supersede — and flag conflicts before coding.

State briefly what you found (or confirm there's nothing relevant) before proposing an approach.

## Problem-Solving Procedure

1. **Understand the ticket** — if it came from one and the intent isn't already in context, run `/gm:ticket`, then **survey** the existing modules (see above) so you build on what's there, not beside it.
2. **Classify** whether the problem is pure logic, I/O/integration, CLI/interface, or packaging/tooling.
3. **Propose** the cleanest approach, keeping side effects at the edges and logic pure/testable.
4. **Then** provide implementation.
5. **Mention** potential edge cases.
6. **Mention** performance considerations (algorithmic complexity, memory, generators vs lists).
7. **Mention** tests to add or update, and how to run them (via the runner — see `${CLAUDE_SKILL_DIR}/../../shared/runner.md`).
8. **Review before wrapping up** — once the change is complete, run **`/gm:review`** on it to get a severity-ranked verdict before it goes anywhere near a merge request. Don't consider the work done until that review has run.
