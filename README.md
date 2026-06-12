# Clara

Collection de skills maison pour [Claude Code](https://claude.com/claude-code), distribuée comme plugin via un marketplace. Tous les skills sont invocables sous le namespace `clara`.

## Skills

| Skill | Invocation | Rôle |
| :--- | :--- | :--- |
| `drupal-11` | `/clara:drupal-11` | Expertise backend Drupal 10/11 — architecture, services, plugins, events, entités, cache, sécurité. |
| `docker-devops` | `/clara:docker-devops` | Docker, Compose, Makefile, CI/CD (GitLab) — builds reproductibles, images minimales, sécurité, DX. |
| `review` | `/clara:review` | Review d'un diff avant merge — correctness, sécurité, cacheability, standards, tests — verdict structuré. |
| `merge-request` | `/clara:merge-request` | Prépare une MR GitLab — commit ciblé, push, `glab mr create` en Draft avec description liée au ticket. |

## Installation

```bash
# Depuis ce dépôt local
/plugin marketplace add ~/clara
/plugin install clara@clara
/reload-plugins

# Ou depuis GitHub
/plugin marketplace add tarto-dev/clara-claude-skills
/plugin install clara@clara
/reload-plugins
```

## Structure

```
clara/
├── .claude-plugin/
│   └── marketplace.json     # déclare le marketplace + le plugin "clara"
└── skills/
    ├── drupal-11/
    │   └── SKILL.md
    └── docker-devops/
        └── SKILL.md
```

## Ajouter un skill

1. Créer `skills/<nom>/SKILL.md` avec un front-matter :
   ```yaml
   ---
   name: <nom>
   description: <quoi + "Use when ..." conditions de déclenchement>
   ---
   ```
2. `git commit` + `git push`.
3. `/plugin marketplace update clara` puis `/reload-plugins`.

Le `name:` du front-matter détermine l'invocation : `/clara:<name>`.
