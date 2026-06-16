# Clara

Collection de skills maison pour [Claude Code](https://claude.com/claude-code), distribuée comme plugin via un marketplace. Tous les skills sont invocables sous le namespace `clara`.

## Skills

| Skill | Invocation | Rôle |
| :--- | :--- | :--- |
| `ticket` | `/clara:ticket` | Digère un ticket Mantis (ou collé) en brief de dev — objectif, où regarder dans le code, contraintes, critères d'acceptation — pour amorcer le contexte avant une session `/clara:drupal-11`. Orienté Drupal/PHP + Mantis + GitLab. |
| `drupal-11` | `/clara:drupal-11` | Expertise backend Drupal 10/11 — architecture, services, plugins, events, entités, cache, sécurité. |
| `docker-devops` | `/clara:docker-devops` | Docker, Compose, Makefile, CI/CD (GitLab) — builds reproductibles, images minimales, sécurité, DX. |
| `review` | `/clara:review` | Review d'un diff avant merge — correctness, sécurité, cacheability, standards, tests — verdict structuré. Orienté Drupal/PHP + GitLab. |
| `merge-request` | `/clara:merge-request` | Prépare une MR GitLab — commit ciblé, push, `glab mr create` en Draft avec description liée au ticket. |
| `merge-review` | `/clara:merge-review` | Review côté reviewer d'une MR GitLab existante — fond, forme, langue, standards — charge le ticket Mantis lié et rédige une note + recommandation approve / request-changes. |

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
├── scripts/
│   └── mantis-issue.sh      # helper Mantis partagé (ticket, review, merge-review)
└── skills/
    ├── ticket/
    │   └── SKILL.md
    ├── drupal-11/
    │   └── SKILL.md
    ├── docker-devops/
    │   └── SKILL.md
    ├── review/
    │   └── SKILL.md
    ├── merge-request/
    │   └── SKILL.md
    └── merge-review/
        └── SKILL.md
```

Les skills qui lisent Mantis (`ticket`, `review`, `merge-review`) appellent le helper partagé via `${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh` — une seule copie, pas de duplication.
