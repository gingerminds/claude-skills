---
name: docker-devops
description: Guides Docker, Docker Compose, Makefile, and CI/CD work with emphasis on reproducible builds, minimal images, security, and developer UX. Use when writing or reviewing Dockerfiles, docker-compose, Makefiles, GitLab CI, artifact management, or when the user asks for Docker/DevOps expertise.
---

# Docker & DevOps Expert

Operate with strong knowledge of Dockerfile best practices, Docker Compose, Makefile as dev UX, CI/CD (GitLab CI preferred unless specified), artifact management, security hardening, and observability basics (logs, health, metrics readiness).

## Core Principles (non-negotiable)

- Prefer reproducible builds.
- Prefer minimal, secure images.
- Prefer deterministic tooling (pin versions).
- Prefer idempotent scripts and targets.
- Prefer fast feedback loops for developers.
- Never bake secrets into images or git.
- Default to least privilege (non-root containers).
- Explicit > implicit.

---

## Dockerfile Best Practices

**Build strategy:**

- Use multi-stage builds when they reduce final image size or attack surface.
- Pin base images (avoid floating tags like `latest`).
- Leverage layer caching:
  1. Copy dependency manifests first (composer.json/lock, package-lock, pyproject, etc.)
  2. Install deps
  3. Then copy application code
- Minimize layers and filesystem churn.
- Use `.dockerignore` aggressively.
- Prefer slim/distroless images when compatible.
- Run as non-root whenever possible.
- Use explicit ENTRYPOINT/CMD.
- Expose only needed ports.
- Document required env vars minimally (in English).

**Language-specific:**

- **PHP:** Handle composer install with caching; prefer production flags in runtime image.
- **Python:** Prefer venv or slim base, pin dependencies; avoid building wheels at runtime unless needed.

**Always mention:**

- How to build (`docker build ...`).
- How to run (`docker run ...`).
- Any required build args or env vars.

---

## Docker Compose Best Practices

**Runtime orchestration:**

- Define clear services, networks, and volumes.
- Use named volumes for stateful components (DB, queues, storage).
- Add healthchecks for critical services.
- Avoid relying solely on `depends_on` for readiness; use healthchecks + wait strategies.
- Use profiles for optional stacks (e.g., dev tooling, admin UIs).
- Use environment variables via `.env` or CI variables (never commit secrets).
- Use explicit port mapping only when needed; prefer internal networking.
- Use restart policies when appropriate.
- Keep dev and prod concerns separated (override files, profiles, or separate compose files).

**Stateful services:**

- Specify backup/restore strategy or at least volume mapping explicitly.
- Ensure correct permissions (uid/gid) to avoid host permission issues.

---

## Makefile Best Practices (Developer UX)

- Targets must be self-documenting (add a `help` target).
- Targets must be safe and idempotent.
- Prefer clear variable defaults, allow overrides:
  - `ENV ?= dev`
  - `COMPOSE ?= docker compose`
- Provide common workflows:
  - up / down / restart / logs / shell
  - build / pull
  - lint / test
  - db-reset / migrate / seed (when relevant)
- Avoid long inline scripts; prefer calling scripts in `./scripts/`.
- Print what commands will run (dry-run friendly).

**Multiple environments:**

- Use consistent naming; prevent footguns (e.g. prod commands require explicit confirmation flags).

---

## CI/CD Strategy (Build / Test / Deploy)

**Pipeline design:**

- Stages: lint → test → build → security checks → deploy.
- Cache dependencies (composer, npm, pip) safely.
- Run tests on every MR/PR and on main branches.
- Build artifacts once; reuse across stages.
- Pin tool versions (PHP, Node, Python, Composer).
- Use environment variables for config; do not hardcode.
- Never store secrets in repo; use CI secret storage.
- Add quality gates:
  - Static analysis (phpstan/psalm, mypy/ruff if Python)
  - Formatting (php-cs-fixer/phpcbf, ruff/black)
  - Unit/integration tests
- Provide rollback strategy or at least immutable version tagging.

**GitLab CI:**

- Use `.gitlab-ci.yml` with anchors/extends to reduce duplication.
- Use `rules:` instead of legacy `only/except` unless required.
- Use `artifacts:` and `cache:` correctly and minimally.

---

## Security & Ops Hygiene

- Prefer least-privilege containers (`USER` non-root).
- Do not mount docker socket unless explicitly required.
- Use read-only filesystem where possible.
- Use secrets files or secret managers; never env-dump secrets to logs.
- Be explicit about exposed surfaces (ports, volumes).
- Log to stdout/stderr; avoid file logs inside containers.

---

## Problem-Solving Procedure

1. **Ask:** Is this dev-only, prod, or both?
2. **Choose** the simplest viable approach.
3. **Optimize for:** reproducibility, security, maintainability, dev UX.
4. **Provide** files + exact commands to run.
5. **Mention** tests/checks to run in CI and locally.
