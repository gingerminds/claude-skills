# Qualification questions (create mode)

Ask one question at a time, in this order, with the AskUserQuestion tool when the answer is a choice. Before each question, look for the answer yourself and put what you found in the question as the proposed answer, so the user confirms or corrects instead of typing. Skip a question whose answer is certain, and say in one line what you took.

Where to look before asking: the project `CLAUDE.md` and `README.md`, the `docs/` folder, compose files and Dockerfiles, the CI file (`.gitlab-ci.yml`, `.github/workflows/`), deploy scripts, proxy configs, `.env.example` (key names only), and `~/.ssh/config`.

## 1. The project and its public

- **Ask**: the project name, the client, and who reads the dossier: the agency only, the client, or both.
- **Pre-fill from**: the repository name, `CLAUDE.md`, the README title, domains in configs.
- **Skip when**: never (the public changes the tone and the PDF variant).
- **Changes**: the README introduction and the config `title`. When the client reads it, remind the user that `pdf --sans-points-attention` produces a variant without the known issues, to send outside the team.

## 2. The other repositories

- **Ask**: does the project live in other repositories (separate front and back, an infrastructure repository, deploy scripts)? Which local paths?
- **Pre-fill from**: submodules, CI jobs that clone or trigger other projects, docs that name them.
- **Skip when**: everything (code, Docker, CI, deploy) is visibly in the current repository.
- **Changes**: those repositories are read like the current one, read-only. The dossier lives in the current repository; their local paths go in the config `repositories` so the update mode reads their history too.

## 3. The environments and their SSH aliases

- **Ask**: which environments to cover (production, preproduction, others), and for each one which SSH aliases reach its machines.
- **Pre-fill from**: CI deploy targets, compose files per environment, domains, and the `Host` entries of `~/.ssh/config` whose name or host name matches the project. Propose the matching aliases per environment.
- **Skip when**: never: the agency rule requires the user to choose the aliases before any SSH command.
- **Changes**: the config `flow_prefixes` (one letter per environment, plus one for what does not depend on an environment), the infrastructure views, and the servers you are allowed to read. An environment without an alias is drawn from the repositories only, its facts marked « indiqué par » or « à confirmer ».

## 4. The actors

- **Ask**: who hosts each environment (company, team), who runs what (machines, network, containers, deployment), which third-party services the platform calls or is called by.
- **Pre-fill from**: what the repositories already show (registry, monitoring endpoints, external APIs, SMTP relay, SSO, analytics) and the collection so far.
- **Skip when**: never, but ask only for what the repositories do not show.
- **Changes**: the config `owners` (names for `hebergeur`, `agence`, `client`, `tiers`), the colour of every box (by who runs it), the « Géré par » column of the matrix.

## 5. The views

Propose the catalogue below, adapted to what you found: no preproduction, no preproduction view; a single machine, a simplified infrastructure view; no import or export, no data flow view. The user keeps, removes or adds views. Each view answers one question, written in the README index.

| View | Question (in the README index) |
|------|--------------------------------|
| 01 · Vue d'ensemble | « Qui utilise la plateforme, et avec quels systèmes échange-t-elle ? » |
| 02 · Infra prod | « Comment la production est-elle hébergée, et comment les flux y circulent-ils ? » |
| 03 · Infra preprod | « En quoi la préproduction diffère-t-elle de la production ? » |
| 04 · Déploiement et accès | « Qui peut agir sur la plateforme, et par où ? » |
| 05 · Flux de données | « Quelles données entrent, quand, et où vont-elles ? » |

- **Changes**: the generators to write, the README index and sections. An added view gets its own question, phrased the same way.

## 6. The dossier

- **Ask**: should the dossier carry the « Points d'attention connus » annex (security gaps, fragile points, what is still unknown)?
- **Pre-fill from**: the public (question 1). Default: yes; the variant without it stays available.
- **Changes**: the config `known_issues` and the README section. The flow matrix is always part of the dossier: the consistency check between views and matrix relies on it. The PDF itself is not a question: it is generated on demand (`/gm:technical-architecture pdf`).

## Gaps found during collection

Ask them one at a time as they come, each with what you already know and what the answer changes on the views, for example « Les sauvegardes des VM, c'est qui ? » or « Le relais SMTP 198.51.100.25, il appartient à l'hébergeur ? ». A gap the user cannot answer stays « à confirmer ».
