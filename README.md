# Gingerminds — skills Claude Code

Collection de skills maison pour [Claude Code](https://claude.com/claude-code), distribuée comme plugin via un marketplace. Tous les skills sont invocables sous le namespace **`gm`** (ex. `/gm:drupal`).

- **Namespace** : `gm`
- **Mainteneur** : Clara
- **Dépôt** : `git@github.com:gingerminds/claude-skills.git`

## Skills

| Skill | Invocation | Rôle |
| :--- | :--- | :--- |
| `ticket` | `/gm:ticket` | Digère un ticket Mantis (ou collé) en brief de dev — objectif, où regarder dans le code, contraintes, critères d'acceptation, pièces jointes chargées à la demande — pour amorcer le contexte avant une session dev spécifique à la stack. Détecte la stack du projet pour router vers le bon skill dev, et classe le ticket en bug ou feature/refactor pour appliquer la discipline adaptée (hypothèse de cause racine, ou esquisse d'ampleur/approches). Orienté Mantis + GitLab. |
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
| `archi-c4` | `/gm:archi-c4` | Prépare, génère et maintient une doc d'architecture **C4 interactive** (HTML autonome) dans `.archi/` — vue projet C1/C2 + une page par unité custom pour C3 (C4 UML à la demande). Trace uniquement le code custom (contrib/core/vendor en boîte noire). Stack-agnostique ; ressources dédiées par techno quand elles existent. |
| `checklist-quality-audit` | `/gm:checklist-quality-audit` (alias `/gm:cqa`) | Audit PRE-MEL de la checklist qualité d'un projet : vérifie le poste et que la préprod sert le code audité, évalue chaque ligne de la copie Notion du projet (code, préprod, boîte à outils Docker, navigateur), écrit statuts et commentaires de preuve après confirmation du lead, puis répartit le reste entre les devs. Lecture seule sur les repos, les serveurs et les bases, sauf le mode `fix`, qui corrige dans ses propres worktrees et livre une MR en brouillon par catégorie et par repo. Prérequis : voir « Préparer son poste ». |

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

### Préparer son poste pour `/gm:checklist-quality-audit`

À faire une fois par poste de lead dev. `/gm:cqa diagnostic` vérifie ensuite le poste sans rien écrire dans Notion.

- **Variables d'environnement** : à exporter dans le profil du shell (valeurs dans Vaultwarden), puis relancer Claude Code.
  - `NOTION_TOKEN` : jeton de l'intégration Notion de l'agence.
  - `GM_CQA_REFERENCE` : URL de la page du modèle de la checklist (le skill refuse d'auditer le modèle lui-même).
- **Registre Docker** : Docker installé (Docker Desktop sous macOS, Docker Engine sous Linux), puis `docker login registry.gitlab.gingerminds.fr` avec un token GitLab en lecture sur le registre (valeur dans Vaultwarden). Les images de la boîte à outils sont tirées au premier audit.
- **`glab`** installé et authentifié sur le GitLab de l'agence (`glab auth status`) : il sert à retrouver le commit déployé en préprod et, en mode `fix`, à créer les MR en brouillon (droit de push sur le projet ; un GitLab client demande sa propre authentification). Le push des branches passe, lui, par `git push origin`, avec les identifiants du remote (clé SSH ou credential helper), sans saisie : un remote qui demande un mot de passe fait échouer le push.
- **Identité git** (`user.name`, `user.email`) configurée dans les repos du projet, pour le mode `fix` : les commits sont ceux du lead. Avec `commit.gpgSign`, la signature doit fonctionner sans saisie.
- **Claude in Chrome** : extension installée et connectée, pour les contrôles navigateur. Le lead s'y connecte lui-même quand le site est protégé.

### Relancer et recetter : `rerun` et `recette`

- **`/gm:cqa rerun`** : relance ciblée, sans refaire tout l'audit. Le skill choisit les lignes selon un motif : cause supprimée, outil relancé, code modifié depuis le commit audité, ou demande du lead. Il les montre au lead avant de lancer quoi que ce soit. Au démarrage, les restes d'une relance interrompue sont mis de côté et un instantané est pris ; à la clôture, les fichiers invalides sont écartés.
- **`/gm:cqa recette`** : revoit les lignes que les devs ont passées en « Mis en place », en lisant leurs commentaires (hors ceux du skill). Un statut humain n'est modifié que pour ces lignes, réévaluées dans la recette : le skill écrit « Ok / Recetté », ou remet la ligne en « À traiter » ou « Mise en place partielle ».
- **Commentaires** : une seule note du skill par ligne Notion, elle remplace la précédente. Les commentaires humains ne sont jamais touchés.
- **Deux questions à chaque lancement** (audit, rerun, recette) : « Mode 100 % autonome ? » et le consentement d'écriture Notion (`none` n'écrit rien, garanti par le code).
  - En mode autonome, tout ce qui demande le lead est mis de côté jusqu'à la fin (`deferred.json`) : connexion, commandes SSH refusées par le garde-fou (jamais exécutées), lacunes et questions. Les lots avec session attendent la connexion, demandée à la fin.
- **Multisite** : les domaines supplémentaires sont détectés ou demandés ; back-office commun ou propre et SSO sont demandés site par site, avec un onglet de connexion par back-office propre.
- **Ordre des lots navigateur** : le front sans session d'abord, puis la pause de connexion, puis les lots `-session`. Les lignes couvertes par la boîte à outils vont dans des lots `-outils`.
- **Contexte** : `key-pages-check` (404 et liens morts de l'accueil), `reuse-context` (reprend les réponses d'un run précédent, sauf le mode et le consentement), repli sur le CLAUDE.md sans PROJECT.md, compte à rôle restreint demandé quand une ligne l'exige.
- **Garde-fou SSH** : compte les formats de hash de mots de passe sans les révéler (4 caractères au plus), colonnes secrètes illisibles, `wp option get` limité à une liste d'options cœur, `wp site list --field=url`, `wp post list` (pages) et `wp user list --role=<rôle> --format=count` autorisés, racines d'app `/var/www /srv /app /opt`, `app_dirs` sous la forme `{host, container}`.
- **Synthèse** : les « Remarques » viennent de `remarques.md` et ne sont jamais effacées ; une section « Décisions mises de côté » et la durée active s'ajoutent.
- **Veille** : si la fermeture du capot ne peut pas être bloquée, l'empêchement se replie sur `sleep:idle`.
- **Boîte à outils 1.2 ou plus** : les nouveaux outils exigent cette version (`cqa-toolbox.sh pull`). Avec une image plus ancienne, ils sont ignorés et la raison donne cette commande.
  - **Affichage** : 6 largeurs et zoom 200 %, avec captures. Les captures de viewport ne sont pas expurgées : les traiter comme les pages elles-mêmes.
  - **Stylelint** : la configuration du projet est comparée à celle de la checklist ; une configuration temporaire sert au besoin.
  - **Dépendances** : `composer audit` et `npm audit` sur le commit audité.
  - **Lighthouse et Yellow Lab** : passages desktop en plus du mobile.
  - **Redocly et Schemathesis** : « non applicable » sans spécification OpenAPI.
- **Page clé 404** (`/gm-cqa-404-<aléa>`) : elle doit répondre 404, toute autre réponse est un soft 404.
- **Durée du crawl** : proposée au diagnostic ; en mode autonome, la proposition est utilisée telle quelle.
- **Plugins WordPress** : le lead confirme lesquels sont custom ; les tiers sont exclus de Stylelint et de l'audit des dépendances.

### Corriger : `fix`

`/gm:cqa fix` (ou `/gm:checklist-quality-audit fix`) part d'un run d'audit existant, corrige les lignes ouvertes que le skill sait corriger et livre une MR en brouillon par catégorie et par repo.

- **Lignes retenues** : statut « À traiter » ou « Mise en place partielle » dans la copie, colonne « Automatisation skill » à « Auto : détection + MR » ou « Semi-auto : MR à valider », dernier constat `ko` ou `partial`, ligne absente des MR de correctifs déjà ouvertes. Le lead en retire ou en ajoute (« Modifier la liste »), y compris une ligne « Détection seule » ou « Manuel ».
- **Avant le lancement** : si l'image `agence` de la boîte à outils manque, le skill propose de la télécharger ; elle n'est jamais téléchargée pendant une passe (sans elle, les contrôles passent par les binaires du poste ou sont déclarés non lancés).
- **Trois questions au lancement**, une à une : « Mode 100 % autonome ? », « Pousser les branches et créer les MR en brouillon ? » et « Ajouter le lien des MR dans Notion ? ». Pour un premier essai sur un projet : mode présent, une ou deux catégories.
- **Déroulé** : une catégorie après l'autre, SÉCURITÉ d'abord. Pour chaque catégorie et chaque repo, un worktree dans le dossier du run, sur une branche `feature/checklist-qa-fix-<catégorie>` partie de `origin/develop` (ou reprise d'une MR ouverte). Le sous-agent `cqa-fixer` corrige, un commit par ligne (`fix(CQ-107): ...`) ; puis contrôles de syntaxe et stylelint, garde-fou sur les commits, relecture par le sous-agent `cqa-fix-reviewer`. Chaque ligne en problème a une seule nouvelle tentative, puis sort de la branche et passe en « Hors-scope » de la MR. Une ligne que le lead laisse aux devs sort de la passe sans nouveau passage du fixer.
- **Modes** : en mode présent, les questions sont posées au fil de l'eau et chaque catégorie est publiée dès qu'elle est relue. En mode autonome, ni question ni push avant la fin : les questions mises de côté sont posées à la fin, puis le skill demande « Publier les branches maintenant ? » ; s'il publie, toutes les branches sont poussées, le lead présent (Claude Code peut demander de confirmer un push), sinon elles restent en local.
- **Jamais** : toucher au dossier du dev (fichiers, branche courante, `develop` local, stash), lancer une commande du projet (conteneurs, `make`, `composer`, `npm`, tests), contacter la préprod ou un serveur, ajouter ou monter une dépendance, passer une MR en Ready, l'approuver ou la fusionner, changer un statut Notion. Les hooks git du repo ne tournent pas : la MR les déclare « Non lancé ».
- **Vérifications** : la syntaxe (PHP, JavaScript, JSON, YAML) et stylelint tournent dans l'image `agence` de la boîte à outils, avec ses propres versions de PHP et de Node, qui peuvent différer de celles du projet. Le lint et les tests du projet restent au relecteur de la MR, avec leur commande, et au pipeline GitLab.
- **`--dry-run`** : correctifs, commits et relectures sur des branches locales `dry-run/checklist-qa-fix-*`, jamais poussées ni reprises ; aucune MR, rien dans Notion.
- **Notion** : avec l'accord du lead, le commentaire du skill de chaque ligne mise en MR reçoit un bloc « MR : » avec le lien ; une relance ou une recette le garde. Statuts et assignations ne changent pas.
- **Reprise** : une passe interrompue se reprend (« Reprendre les correctifs ») ou s'abandonne (« Abandonner » : worktrees retirés, branches locales gardées). Une passe suivante complète les MR ouvertes sans reprendre leurs lignes.
- **Fin** : synthèse dans `fix/synthese.md` et message prêt à coller pour l'équipe. Ensuite : relire et ajuster les MR, les fusionner, déployer en préprod, passer les lignes en « Mis en place », puis `/gm:cqa recette`.

## Structure

```
claude-skills/
├── .claude-plugin/
│   ├── marketplace.json     # déclare le marketplace "gingerminds" + le plugin "gm"
│   └── plugin.json          # manifeste du plugin "gm" (nom + version) — sert au dev local via --plugin-dir
├── agents/
│   ├── cqa-auditor.md       # sous-agent d'analyse des lignes de checklist (code, préprod) : consommé par checklist-quality-audit
│   ├── cqa-browser.md       # sous-agent des contrôles navigateur (Claude in Chrome) : consommé par checklist-quality-audit
│   ├── cqa-fixer.md         # sous-agent du mode fix : corrige les lignes d'une catégorie dans son worktree
│   └── cqa-fix-reviewer.md  # sous-agent du mode fix : relit, en lecture seule, les commits d'une passe du fixer
├── scripts/
│   ├── mantis-issue.sh      # helper Mantis partagé (ticket, review, merge-review)
│   ├── cqa_fix.py  cqa_notion.py  cqa_plan.py  cqa_ssh_ro.py   # CLI de l'audit : mode correction, API Notion, plan, garde SSH en lecture seule
│   ├── cqa/                 # bibliothèque de l'audit (Python >= 3.9, bibliothèque standard seule)
│   ├── cqa-keepawake.sh     # empêche la mise en veille du poste pendant l'audit (systemd-inhibit, caffeinate)
│   └── cqa-toolbox.sh       # lanceur de la boîte à outils Docker de l'audit
├── shared/                  # ressources transverses (indépendantes de la techno)
│   ├── runner.md            # ordre de priorité du runner : make → docker → lando
│   ├── stack-detect.md      # cheatsheet de détection de stack + règle de chargement
│   ├── root-cause.md        # investigation de cause racine (tickets bug) — consommé par ticket
│   └── scope-classification.md  # classification bounded/architectural (tickets feature) — consommé par ticket
├── stack/                   # spécificités par techno, chargées à la demande
│   ├── drupal/              # Forme 2 : dispatcher + natures découpées
│   │   ├── MAIN.md          #   route vers core + la nature (dev | review | security)
│   │   ├── core.md  dev.md  review.md  security.md
│   │   └── …
│   └── vue/
│       └── MAIN.md          # Forme 1 : fichier unique sectionné (## core / dev / review / security)
├── tests/
│   └── cqa/                 # tests unittest de l'audit et hygiène du dépôt public (voir « Contribuer »)
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
    ├── checklist-quality-audit/
    │   ├── SKILL.md         # orchestrateur de l'audit PRE-MEL
    │   ├── references/      # lues au fil des étapes de l'audit (fix.md : conventions du mode fix)
    │   └── schemas/         # finding, fix-report et fix-review (contrats des sous-agents)
    ├── cqa/SKILL.md         # alias /gm:cqa de checklist-quality-audit
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

**Tests** : le code de l'audit (`scripts/cqa/`, `scripts/cqa_*.py`) et les contrats de ses skills et sous-agents sont couverts par des tests `unittest` (Python 3.9 minimum, bibliothèque standard seule). À lancer avant de modifier ces fichiers :

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -t . -v
```

Le dépôt est public : `tests/cqa/test_public_hygiene.py` fait échouer la suite sur toute adresse IP, tout identifiant Notion hors de la liste d'identifiants factices du test, tout secret dans les fichiers de contenu, et toute version désalignée entre `marketplace.json` et `plugin.json`. Dans les tests et les fixtures, utilise des identifiants factices (`0123456789abcdef0123456789abcdef`), des prénoms inventés et des domaines `example.org`.
