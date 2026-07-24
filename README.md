# Gingerminds — skills Claude Code

Collection de skills maison pour [Claude Code](https://claude.com/claude-code), distribuée comme plugin via un marketplace. Tous les skills sont invocables sous le namespace **`gm`** (ex. `/gm:drupal`).

- **Namespace** : `gm`
- **Mainteneur** : Clara
- **Dépôt** : `git@github.com:gingerminds/claude-skills.git`

## Skills

| Skill | Invocation | Rôle |
| :--- | :--- | :--- |
| `ticket` | `/gm:ticket` | Digère un ticket Mantis (ou collé) en brief de dev — objectif, où regarder dans le code, contraintes, critères d'acceptation, pièces jointes chargées à la demande — pour amorcer le contexte avant une session dev spécifique à la stack. Détecte la stack du projet pour router vers le bon skill dev. Orienté Mantis + GitLab. |
| `drupal` | `/gm:drupal` | Expertise backend Drupal 10/11 — architecture, services, plugins, events, entités, cache, sécurité. |
| `vue` | `/gm:vue` | Expertise frontend Vue 3 / Nuxt 3 — Composition API, composables, Pinia, SSR/SSG, TypeScript, architecture de composants. |
| `laravel` | `/gm:laravel` | Expertise backend Laravel — Eloquent, migrations, form requests, services, queues, events, tests. |
| `django` | `/gm:django` | Expertise backend Django — apps, modèles, ORM, migrations, DRF, forms/serializers, tests. |
| `python` | `/gm:python` | Expertise Python générique (hors framework web) — packaging, typing, structure, erreurs, tests. |
| `wordpress` | `/gm:wordpress` | Expertise WordPress — hooks, plugin/thème custom, APIs data WP, CPT, i18n, sécurité (nonces, capabilities, escaping). |
| `docker-devops` | `/gm:docker-devops` | Docker, Compose, Makefile, CI/CD (GitLab) — builds reproductibles, images minimales, sécurité, DX. |
| `review` | `/gm:review` | Review d'un diff avant merge — correctness, sécurité, cacheability, standards, tests — verdict structuré. Stack-agnostique : charge les spécificités techno depuis `stack/`. Orienté GitLab. |
| `merge-request` | `/gm:merge-request` | Prépare une MR GitLab — commit ciblé, push, `glab mr create` en Draft avec description liée au ticket. |
| `merge-review` | `/gm:merge-review` | Review côté reviewer d'une MR GitLab existante — fond, forme, langue, standards — charge le ticket Mantis lié et rédige une note + recommandation approve / request-changes. Stack-agnostique (via `gm:review`). |
| `security` | `/gm:security` | Audit sécurité des dépendances et de l'infra — `composer audit` / `npm audit`, advisories CMS/framework, config Docker — priorisé par criticité réelle, avec correctifs ou marches à suivre. Stack-agnostique : les checks spécifiques (ex. SA Drupal) viennent de `stack/`. |
| `archi-c4` | `/gm:archi-c4` | Prépare, génère et maintient une doc d'architecture **C4 interactive** (HTML autonome) dans `.archi/` — vue projet C1/C2 + une page par unité custom pour C3 (C4 UML à la demande). Trace uniquement le code custom (contrib/core/vendor en boîte noire). Stack-agnostique ; ressources dédiées Drupal et Vue. |

## Installation

Un marketplace se déclare une fois, puis on installe le plugin `gm` depuis celui-ci.

### Depuis GitHub (recommandé)

```bash
/plugin marketplace add gingerminds/claude-skills
/plugin install gm@gingerminds
```

### Depuis un clone local

```bash
git clone git@github.com:gingerminds/claude-skills.git
```

```bash
/plugin marketplace add ~/claude-skills
/plugin install gm@gingerminds
```

Après l'installation, les skills sont disponibles immédiatement — tape `/gm:` pour voir la liste.

## Mise à jour

```bash
# Rafraîchit la définition du marketplace depuis sa source (GitHub ou local)
/plugin marketplace update gingerminds

# Réinstalle le plugin pour récupérer la dernière version des skills
/plugin install gm@gingerminds
```

Sur un clone local, fais d'abord un `git pull` dans le dépôt, puis `/plugin marketplace update gingerminds`.

## Utilisation

Tape `/gm:<skill>` dans Claude Code. Les skills se chaînent — un flux Drupal typique :

```
/gm:ticket        # digère le ticket Mantis en brief
/gm:drupal        # implémente en s'appuyant sur le brief + le code existant
/gm:review        # verdict severity-ranked sur le diff
/gm:merge-request # ouvre la MR GitLab (Draft) liée au ticket
```

Côté reviewer, `/gm:merge-review` prend une MR existante (URL ou numéro), charge le ticket lié et rédige la note de review. `/gm:security` fait un audit dépendances + infra à part entière, `/gm:docker-devops` couvre tout le volet conteneurs / CI.

Pour découvrir ou documenter l'architecture d'un projet, `/gm:archi-c4` prépare, génère et maintient un modèle **C4 interactif** (HTML autonome) dans `.archi/` — vue projet (C1/C2) dans `.archi/index.html` et une page par unité custom (C3, C4 à la demande), à ouvrir dans le navigateur.

## Structure

```
claude-skills/
├── .claude-plugin/
│   ├── marketplace.json     # déclare le marketplace "gingerminds" + le plugin "gm"
│   └── plugin.json          # manifeste du plugin "gm" (nom + version) — sert au dev local via --plugin-dir
├── scripts/
│   └── mantis-issue.sh      # helper Mantis partagé (ticket, review, merge-review)
├── shared/                  # ressources transverses (indépendantes de la techno)
│   ├── runner.md            # ordre de priorité du runner : make → docker → lando
│   └── stack-detect.md      # cheatsheet de détection de stack + règle de chargement
├── stack/                   # spécificités par techno, chargées à la demande
│   ├── drupal/              # Forme 2 : dispatcher + natures découpées
│   │   ├── MAIN.md          #   route vers core + la nature (dev | review | security)
│   │   ├── core.md  dev.md  review.md  security.md
│   │   └── …
│   └── vue/
│       └── MAIN.md          # Forme 1 : fichier unique sectionné (## core / dev / review / security)
└── skills/
    ├── ticket/SKILL.md
    ├── drupal/SKILL.md      # point d'entrée : charge stack/drupal/MAIN.md (dev)
    ├── vue/SKILL.md         # point d'entrée : charge stack/vue/MAIN.md (dev)
    ├── laravel/SKILL.md     # point d'entrée : charge stack/laravel/MAIN.md (dev)
    ├── django/SKILL.md      # point d'entrée : charge stack/django/MAIN.md (dev)
    ├── python/SKILL.md      # point d'entrée : charge stack/python/MAIN.md (dev)
    ├── wordpress/SKILL.md   # point d'entrée : charge stack/wordpress/MAIN.md (dev)
    ├── docker-devops/SKILL.md
    ├── review/SKILL.md      # générique : détecte la stack → charge stack/<x>/MAIN.md (review)
    ├── merge-request/SKILL.md
    ├── merge-review/SKILL.md
    ├── security/SKILL.md    # générique : détecte la stack → charge stack/<x>/MAIN.md (security)
    └── archi-c4/
        ├── SKILL.md
        └── assets/          # template.html figé + model.example.js (contrat)
```

Les skills qui lisent Mantis (`ticket`, `review`, `merge-review`) appellent le helper partagé via `${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh` — une seule copie, pas de duplication.

### Architecture stack-agnostique

Les skills génériques (`review`, `security`, `merge-review`) ne codent aucune techno en dur. Ils **détectent la stack** (`shared/stack-detect.md`), puis chargent son point d'entrée `stack/<techno>/MAIN.md` en précisant la **nature** utile (`core` + `dev` | `review` | `security`). Deux formes selon la densité de la techno :

- **Forme 1** — un fichier unique sectionné (ex. `stack/vue/MAIN.md`).
- **Forme 2** — un dossier découpé par nature (ex. `stack/drupal/`), quand une nature déborde.

Le point d'entrée est **toujours** `stack/<techno>/MAIN.md`, quelle que soit la forme — l'appelant n'a pas à le savoir. Invariant : le **`core`** d'une techno n'existe qu'**une seule fois** (pas de re-duplication entre `review` / `security` / `merge-review`). Le **runner** (`make → docker → lando`, Lando étant temporaire) est transverse : `shared/runner.md`.

## Contribuer

Un skill = un dossier `skills/<nom>/` avec un `SKILL.md` (frontmatter `name` + `description`, puis le corps). Ajoute l'entrée correspondante dans la table ci-dessus et dans la `description` du plugin (`.claude-plugin/marketplace.json`), et bump la `version` dans **`marketplace.json` _et_ `plugin.json`** (elles doivent rester alignées).

**Ajouter une techno** (`stack/<techno>/`) : crée un `MAIN.md` comme point d'entrée. Commence en **Forme 1** (un seul fichier sectionné `## core / dev / review / security`) ; passe en **Forme 2** (un dossier avec `MAIN.md` dispatcher + `core.md` / `dev.md` / `review.md` / `security.md`) seulement quand une nature déborde. Garde le `core` unique et ne code aucune techno en dur dans les skills génériques — ils la découvrent via `shared/stack-detect.md`.

Pour développer en local, lance Claude Code avec le plugin lu **directement depuis ton clone** — pas de réinstallation à chaque modif :

```bash
claude --plugin-dir ~/claude-skills   # lit les skills en place (le plugin.json fixe le namespace gm)
/reload-plugins                       # applique tes édits dans la session, sans redémarrer
```

À l'inverse, `/plugin marketplace update gingerminds` puis `/plugin install gm@gingerminds` recopient depuis la source — plus lourd, à réserver à la vérification d'une version installée.
