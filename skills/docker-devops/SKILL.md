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

## Kickoff — scope the stack first

Before generating any Docker asset, **scope the project shape**. Not every
project is a front + back split — many are a single monolith (Drupal, Laravel,
Symfony, a lone Nuxt app…), and generating the wrong blocks wastes everyone's
time.

**Skip this when it's a reprise** — i.e. a `docker/compose/` (or existing
`docker-compose.yml`) is already present: read it, follow its established shape,
and only add/adjust what's asked.

**Otherwise, ask up front with `AskUserQuestion`** (one click beats a free-text
guess) before choosing which files/blocks to emit:

- **Shape** — monolith · front + back (split repos/services) · other. Drives
  whether you emit one exposed service or several.
- **Runtime(s)** — PHP/Apache/FPM · Node/Nuxt · Python · … Drives the
  `docker/<service>/Dockerfile` set.
- **Backing services** — DB (which?), Redis, Elasticsearch, Mailpit, admin UIs…
  Drives the `app-internal`-only services and their healthchecks.
- **Exposure** — which services must be reachable via Traefik (get the label
  block) vs. internal only.

Then emit exactly the layout and Traefik blocks matching the answers — no
speculative services, no front tier for a monolith.

---

## Project Layout (non-negotiable)

Docker assets live under a top-level `docker/` directory. **Never** drop the
`docker-compose.yml` (or the `Makefile`'s compose file) at the project root.

```
docker/
  compose/
    docker-compose.yml        # the compose file lives HERE, not at the repo root
  <service>/                  # one dir per service that needs a build/config
    Dockerfile
    <config files…>           # php.ini, entrypoint.sh, elasticsearch.yml, …
Makefile                      # stays at the repo root
.env                          # stays at the repo root
```

Because the compose file sits two levels down, **build contexts are relative to
the repo root**, i.e. `context: ../..`, and dockerfiles are addressed from there:

```yaml
build:
  context: ../..
  dockerfile: docker/app/Dockerfile
```

The `Makefile` (at the root) always targets the compose file explicitly, and
pins the project directory so the root `.env` is resolved regardless of the
current working directory:

```make
COMPOSE ?= docker compose --project-directory . -f docker/compose/docker-compose.yml
```

Without `--project-directory .` (or `--env-file .env`), running the command from
another directory can leave `${COMPOSE_PROJECT_NAME}` unset — silently breaking
container, volume, and network names.

---

## Traefik Architecture (non-negotiable for exposed services)

Local/dev stacks are fronted by a shared, externally-managed **Traefik** reverse
proxy. Generated compose files MUST follow this pattern.

**Two networks:**

- `app-internal` — a `bridge` network, named `${COMPOSE_PROJECT_NAME}-internal`,
  for service-to-service traffic (DB, cache, app ⇄ web server).
- `traefik-public` — declared `external: true` (Traefik owns it); joined **only**
  by services that must be reachable through the proxy.

```yaml
networks:
  app-internal:
    name: ${COMPOSE_PROJECT_NAME}-internal
    driver: bridge
  traefik-public:
    external: true
```

**Which services get exposed:** only user-facing ones (web server, admin UIs like
Adminer/Mailpit/Elasticvue). Pure backend services (`db`, `redis`, `node`,
workers) stay on `app-internal` only — **no Traefik labels, not on
`traefik-public`**.

**Standard label block** for every exposed service — one HTTP router (`web`) and
one HTTPS router suffixed `-secure` (`websecure`, `tls=true`):

```yaml
labels:
  - "traefik.enable=true"
  # HTTP Router
  - "traefik.http.routers.${COMPOSE_PROJECT_NAME}.rule=Host(`${COMPOSE_PROJECT_NAME}.dev.localhost`)"
  - "traefik.http.routers.${COMPOSE_PROJECT_NAME}.entrypoints=web"
  - "traefik.http.routers.${COMPOSE_PROJECT_NAME}.service=${COMPOSE_PROJECT_NAME}"
  # HTTPS Router
  - "traefik.http.routers.${COMPOSE_PROJECT_NAME}-secure.rule=Host(`${COMPOSE_PROJECT_NAME}.dev.localhost`)"
  - "traefik.http.routers.${COMPOSE_PROJECT_NAME}-secure.entrypoints=websecure"
  - "traefik.http.routers.${COMPOSE_PROJECT_NAME}-secure.tls=true"
  - "traefik.http.routers.${COMPOSE_PROJECT_NAME}-secure.service=${COMPOSE_PROJECT_NAME}"
  # Service (container's internal port)
  - "traefik.http.services.${COMPOSE_PROJECT_NAME}.loadbalancer.server.port=80"
  # Network
  - "traefik.docker.network=traefik-public"
```

**Conventions:**

- Prefix everything with `${COMPOSE_PROJECT_NAME}` — container names, volume
  names, network name, router/service names. For a secondary UI, suffix it
  (`${COMPOSE_PROJECT_NAME}-adminer`, `-mailpit`, …).
- Host pattern: `${COMPOSE_PROJECT_NAME}[-<tool>].dev.localhost`, or a dedicated
  `${APP_HOST}` variable when the project defines one.
- Set `traefik.docker.network=traefik-public` so Traefik resolves the right
  network in a multi-network service.
- Don't publish ports for services reached through Traefik; use `ports:` only for
  direct host access (e.g. exposing the DB to a local client).

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

- Place the compose file at `docker/compose/docker-compose.yml` with `../..`
  build contexts (see *Project Layout*).
- Front exposed services with Traefik using the two-network pattern and the
  standard label block (see *Traefik Architecture*).
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
  - `COMPOSE ?= docker compose --project-directory . -f docker/compose/docker-compose.yml`
    (point at the compose file under `docker/compose/`; `--project-directory .`
    keeps the root `.env` resolvable from any CWD)
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
