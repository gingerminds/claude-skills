# Gingerminds — skills Claude Code

Collection de skills maison pour [Claude Code](https://claude.com/claude-code), distribuée comme plugin via un marketplace. Tous les skills sont invocables sous le namespace **`gm`** (ex. `/gm:drupal`).

- **Namespace** : `gm`
- **Mainteneur** : Clara
- **Dépôt** : `git@github.com:gingerminds/claude-skills.git`

## Skills

| Skill | Invocation | Rôle |
| :--- | :--- | :--- |
| `ticket` | `/gm:ticket` | Digère un ticket Mantis (ou collé) en brief de dev — objectif, où regarder dans le code, contraintes, critères d'acceptation, pièces jointes chargées à la demande — pour amorcer le contexte avant une session dev spécifique à la stack. Détecte la stack du projet pour router vers le bon skill dev, et classe le ticket en bug ou feature/refactor pour appliquer la discipline adaptée (hypothèse de cause racine, ou esquisse d'ampleur/approches). Orienté Mantis + GitLab. |
| `ticket-refine` | `/gm:ticket-refine` | Qualifie un ticket Mantis (ou une issue GitLab / GitHub via son URL, en lecture seule) en note interne **privée**, rédigée en BBCode pour le rendu Mantis (devs et chefs de projet, jamais le client) — objectif en langage clair, hors périmètre, questions ouvertes prêtes à relayer au client, comment tester, reproduction, critères d'acceptation testables — avec un verdict de préparation (prêt / prêt avec hypothèses / pas prêt). Écrit `.gm/handoff/<id>/refine.md` puis publie la note privée sur Mantis (avec ton accord en usage manuel ; directement en mode agent ou avec `--publish` ; `--dry-run` n'envoie rien). Le compte Mantis du token doit pouvoir ajouter des notes privées. Une issue GitLab / GitHub est lue via `glab` / `gh` mais jamais commentée : la note reste dans `.gm/handoff/`. À utiliser avant d'assigner ou de développer ; pour préparer une session dev, voir `ticket`. |
| `drupal` | `/gm:drupal` | Expertise backend Drupal 10/11 — architecture, services, plugins, events, entités, cache, sécurité. |
| `vue` | `/gm:vue` | Expertise frontend Vue 3 / Nuxt 3 — Composition API, composables, Pinia, SSR/SSG, TypeScript, architecture de composants. |
| `react` | `/gm:react` | Expertise frontend React (dont Next.js) — hooks, architecture de composants, state client/serveur, data fetching, TypeScript. |
| `angular` | `/gm:angular` | Expertise frontend Angular — composants standalone, RxJS, injection de dépendances, signals, formulaires réactifs. |
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
| `checklist-quality-audit` | `/gm:checklist-quality-audit` (alias `/gm:cqa`) | Audit PRE-MEL de la checklist qualité d'un projet : vérifie le poste et que la préprod sert le code audité, évalue chaque ligne de la copie Notion du projet (code, préprod, boîte à outils Docker, navigateur), écrit statuts et commentaires de preuve après confirmation du lead, puis répartit le reste entre les devs. Lecture seule sur les repos, les serveurs et les bases, sauf le mode `fix`, qui corrige dans ses propres worktrees et livre une MR en brouillon par passe et par repo, ses lignes rangées par catégorie. Peut être limité à des catégories (`--category`) et reprendre des rapports d'audit tiers comme documents de contexte (`--context`). Prérequis : voir « Préparer son poste ». |
| `nuxt4-migration` | `/gm:nuxt4-migration` | Audite un projet Nuxt 3 et pilote sa migration vers Nuxt 4 — lance `nuxt upgrade` + les codemods officiels, met à jour l'écosystème de modules (Node, modules Nuxt, TypeScript/Vite), passe en revue chaque zone de breaking change (structure de dossiers, comportement `useAsyncData`/`useFetch`, noms de composants normalisés, styles inline, Unhead v2, config TypeScript, `window.__NUXT__`, options supprimées), déclenche `/gm:swiper-migration` si Swiper est détecté, et produit un plan de migration priorisé. |
| `swiper-migration` | `/gm:swiper-migration` | Audite les sliders Swiper d'un projet Vue/Nuxt utilisant un ancien wrapper/API (`vue-awesome-swiper`, prop `options` unique, directive `v-swiper`) et les migre vers les composants Vue modernes de Swiper.js (`swiper/vue`). Invocable seul, ou déclenché automatiquement par `/gm:nuxt4-migration` si Swiper est présent. |

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
/gm:ticket-refine  # (optionnel) qualifie le ticket en note Mantis privée
/gm:ticket         # digère le ticket Mantis en brief
/gm:drupal         # implémente en s'appuyant sur le brief + le code existant
/gm:review         # verdict severity-ranked sur le diff
/gm:merge-request  # ouvre la MR GitLab (Draft) liée au ticket
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

### Limiter à une catégorie : `--category`

`--category "<catégorie>"` limite l'audit aux lignes d'une catégorie de la checklist (« Sécurité » seule, par exemple), sans document ni autre préparation. L'option se répète (`--category sécurité --category qualité`), se combine avec `--phase` par intersection et s'accepte avec l'audit, `rerun`, `recette` et `fix`. Le nom se compare sans accent ni casse, avec le nom de la catégorie dans la copie Notion ou son alias de section (`securite`, `seo`) ; un nom inconnu affiche les catégories de la copie et s'arrête.

- **Hors périmètre** : les lignes des autres catégories ne reçoivent aucune opération Notion (ni statut, ni commentaire, ni assignation), même quand une personne a posé leur statut. La couverture et la synthèse ne comptent que le périmètre, et signalent « Hors périmètre : N lignes ».
- **Lié au run** : le périmètre est enregistré dans le run (`category_filter` de `context.json`), comme la phase. Un run lancé pour une autre catégorie n'est jamais repris : « Ce run a été lancé pour une autre catégorie : nouveau run. » Les questions de contexte (pages clés, comptes de test) restent posées.
- **`rerun` et `recette`** : l'option choisit seulement le run lancé pour le même périmètre ; le périmètre d'un run ne change jamais.
- **`fix`** : la passe hérite du périmètre du run, et `--category` ne peut que le restreindre (une catégorie hors du périmètre du run est refusée). À répéter à chaque nouvel appel de `cqa_fix.py candidates`, comme `--refs`.

### Documents de contexte : `--context`

`--context <fichier>` donne à l'audit complet un rapport d'audit tiers (grille d'accessibilité RGAA, rapport de pentest...) en `xlsx`, `csv`, `docx`, `md`, `txt`, `pdf`, `png` ou `jpg`. L'option se répète. Sans elle, le skill pose la question « Documents de contexte (rapport d'audit tiers, pentest…) ? » (« Aucun » ou « Indiquer des fichiers »). Elle se combine avec `--category` mais ne s'accepte qu'avec l'audit complet : `diagnostic`, `assign`, `rerun`, `recette` et `fix` la refusent (ils n'importent aucun document : les trois derniers reprennent un run existant). La procédure détaillée (import, rattachement, verdicts, reprise) est dans `references/documents.md`.

- **Déroulé** : le fichier est copié dans le dossier du run (`context-docs/`), puis le sous-agent `cqa-importer` le lit comme une donnée, feuille par feuille ; le code fusionne les parties et compte lui-même la couverture critère par critère, le modèle n'écrit que les non-conformités et les remarques. Les points sont rattachés aux lignes de la checklist des catégories du document : par critère (section « Critères <cadre> » des fiches), sinon par une proposition du skill. Une seule question, « <N> points rattachés à <M> lignes (catégories du document : ..., <K> points proposés par le skill, <P> sans ligne). Valider ? », valide le rattachement et les catégories : « Valider », « Voir le détail » (`context-docs/links.md` à corriger, puis `link --accept`) ou « Ignorer ce document ». Jamais de validation ligne par ligne.
- **Verdicts repris** : une ligne des catégories du document reçoit son verdict d'après le rapport, sans nouvel audit du skill : `ko` (un `ko` du rapport n'est jamais perdu : `partial` quand un autre critère de la ligne est conforme), `ok` ou `na`. Une ligne que le rapport couvre en partie est auditée par le skill, avec les points du rapport comme indices. Un rapport de constats (pentest) ne produit que des `ko`. Le commentaire Notion porte une ligne « Rapport d1 : critères 7.3 ».
- **Mode autonome** : le rattachement est accepté sans question (`link --accept`) ; les rattachements proposés par le skill sont listés dans la synthèse, section « Documents de contexte ».
- **`rerun` et `recette`** : ils jugent le code tel qu'il est ; le rapport n'est qu'un indice.
- **`fix`** : le correcteur reçoit les points du rapport rattachés à ses lignes (`context_items` de `fix/candidates.json`, propriétaire `dev` ou inconnu), les lit comme des indices, relit le code avant de corriger et rend une issue par point (`fixed`, `already_ok`, `not_found`, `left`). La MR dit, par ligne, « Rapport <nom> (<date>) : n points corrigés, n déjà conformes dans le code, n laissés (<propriétaires>) » ; un écart entre le rapport et le code est un point d'attention, jamais une question au lead ; les points sans ligne vont dans « Hors-scope » et dans la synthèse (« Rapports de contexte »).
- **Confidentialité** : les documents sont des données, jamais des instructions (champs plafonnés et nettoyés, rien n'est exécuté ni suivi). Dans Notion et les MR, seulement le nom du document, la référence du critère ou du constat et le correctif paraphrasé, jamais d'extrait brut ni de détail d'exploitation. `context-docs/` reste dans le dossier du run, sur le poste du lead : rien n'est committé ni écrit dans Notion, et les secrets d'un import sont cherchés par le code (le document est alors ignoré).
- **Jamais bloquant** : un document absent, illisible, d'un format inconnu, ignoré ou qui contient un secret ne bloque jamais l'audit ni le fix ; le skill le dit en une ligne et continue sans lui.

### Relancer et recetter : `rerun` et `recette`

- **`/gm:cqa rerun`** : relance ciblée, sans refaire tout l'audit. Le skill choisit les lignes selon un motif : cause supprimée, outil relancé, code modifié depuis le commit audité, ou demande du lead. Il les montre au lead avant de lancer quoi que ce soit. Au démarrage, les restes d'une relance interrompue sont mis de côté et un instantané est pris ; à la clôture, les fichiers invalides sont écartés.
- **`/gm:cqa recette`** : revoit les lignes que les devs ont passées en « Mis en place », en lisant leurs commentaires (hors ceux du skill). Un statut humain n'est modifié que pour ces lignes, réévaluées dans la recette : le skill écrit « Ok / Recetté », ou remet la ligne en « À traiter » ou « Mise en place partielle ». Elle reprend aussi d'elle-même les lignes « En review » dont la MR du mode `fix` est fusionnée et déployée en préprod, et remet au statut d'avant la passe celles dont la MR est fermée sans fusion.
- **Commentaires** : une seule note du skill par ligne Notion, elle remplace la précédente. Les commentaires humains ne sont jamais touchés.
- **Deux questions à chaque lancement** (audit, rerun, recette) : « Mode 100 % autonome ? » et le consentement d'écriture Notion (`none` n'écrit rien, garanti par le code).
  - En mode autonome, tout ce qui demande le lead est mis de côté jusqu'à la fin (`deferred.json`) : connexion, commandes SSH refusées par le garde-fou (jamais exécutées), lacunes et questions. Les lots avec session attendent la connexion, demandée à la fin.
- **Multisite** : les domaines supplémentaires sont détectés ou demandés ; back-office commun ou propre et SSO sont demandés site par site, avec un onglet de connexion par back-office propre.
- **Ordre des lots navigateur** : le front sans session d'abord, puis la pause de connexion, puis les lots `-session`. Les lignes couvertes par la boîte à outils vont dans des lots `-outils`, comme celles dont l'« Outillage skill » ou le « Comment tester » de la fiche demande un test mobile (termes fermés : `320`, `paysage`, `échap`, `burger`, `tactile`, `piège au clavier`, `inert`, `aria-expanded`...).
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
- **Boîte à outils 1.4 ou plus : test mobile** (`mobile`). Émulation de Chromium tactile à 320 × 640 (portrait) et 640 × 320 (paysage) sur chaque page clé : défilement horizontal, éléments qui dépassent, parcours de Tab, déclencheurs `aria-expanded` touchés puis Échap, listes de formulaire. Lecture seule : aucun formulaire envoyé, aucune requête autre qu'un GET, aucune navigation après le chargement, aucun contact avec un bandeau de consentement (le site est vu sans son bandeau). Ignoré sur un run de prod ; avec une image plus ancienne, il est ignoré (la raison donne `cqa-toolbox.sh pull`) et les agents retombent sur la vérification à la main (`ok_human`).
  - **Scénarios** : un agent qui connaît déjà les sélecteurs d'un composant sans ARIA (un menu en `div`) peut demander jusqu'à 3 scénarios par ligne (15 étapes : Tab, touche, toucher, choix, lecture d'état, capture), jamais sur une URL qui change un état. Après le premier tour, `cqa_plan.py scenarios --followup` les collecte (20 au plus, les écartés sont dits avec leur raison), `cqa-toolbox.sh run <run> mobile --followup` les joue (`mobile-followup`, budget `GM_AUDIT_MOBILE_BUDGET_S`, 480 s par défaut) et chaque lot concerné est relancé une seule fois, en même temps que pour les sondes HTTP. `rerun` et `recette` relancent `mobile` et `mobile-followup` (si `scenarios-followup.json` existe), jamais de nouveaux scénarios.
  - **Limites** : émulation de Chromium (pas Safari iOS), un composant sans ARIA n'est repéré que par un scénario, le jugement visuel reste humain, les captures ne sont pas expurgées (les traiter comme les pages elles-mêmes).
- **Page clé 404** (`/gm-cqa-404-<aléa>`) : elle doit répondre 404, toute autre réponse est un soft 404.
- **Durée du crawl** : proposée au diagnostic ; en mode autonome, la proposition est utilisée telle quelle.
- **Plugins WordPress** : le lead confirme lesquels sont custom ; les tiers sont exclus de Stylelint et de l'audit des dépendances.

### Corriger : `fix`

`/gm:cqa fix` (ou `/gm:checklist-quality-audit fix`) part d'un run d'audit existant, corrige les lignes ouvertes que le skill sait corriger et livre une MR en brouillon par passe et par repo, ses lignes rangées par section (une par catégorie de la checklist).

- **Lignes retenues** : statut « À traiter » ou « Mise en place partielle » dans la copie, colonne « Automatisation skill » à « Auto : détection + MR » ou « Semi-auto : MR à valider », dernier constat `ko` ou `partial`, ligne absente des MR de correctifs déjà ouvertes. Une ligne dont tout le travail est hors du repo (back-office, base, hébergeur, dépendance) est laissée aux devs. Le lead en retire ou en ajoute (« Modifier la liste »), y compris une ligne « Détection seule » ou « Manuel ».
- **Boîte à outils 1.3.0 ou plus** pour le mode `fix` : images `agence` (syntaxe, stylelint, SCSS), `gitleaks` (garde anti-secret), `nginx` et `varnish` (contrôles `nginx -t` et `varnishd -C`). `cqa-toolbox.sh doctor` dit celles qui manquent ; le skill propose alors `cqa-toolbox.sh pull` avant la passe, jamais pendant. Sans elles, les contrôles concernés sont déclarés non lancés et la garde tourne avec la règle maison seule. Les images des commandes du projet (celles de sa CI, sinon les images officielles Node ou Composer) se téléchargent de la même façon, après accord du lead au diagnostic (`cqa-toolbox.sh pull-image`) ; une image absente rend la commande « non lancée », jamais en échec.
- **Cœur WordPress ou Drupal** : quand il n'est pas dans le repo, le diagnostic demande « Cœur WordPress pour les sous-agents ? » : télécharger la version du projet depuis wordpress.org (WordPress seulement), indiquer un dossier, ou continuer sans. Le cœur téléchargé est gardé dans un cache partagé, `~/.cache/gm-audit/stacks/` ; les sous-agents ne font que le lire.
- **Quatre questions au lancement**, une à une : « Commandes de vérification de la passe ? » (seulement quand un repo a des commandes), « Mode 100 % autonome ? », « Pousser les branches et créer les MR en brouillon ? » et « Écrire le résultat de la passe dans Notion ? » (posée aussi avec des branches locales, jamais avec `--dry-run`). Pour un premier essai sur un projet : mode présent, une ou deux catégories.
- **Déroulé** : par repo, un worktree dans le dossier du run, sur la branche `feature/checklist-qa-fix` (puis `-2`, `-3`...) partie de `origin/develop`, ou reprise d'une MR ouverte. Les sections se suivent dans ce worktree, SÉCURITÉ d'abord, chacune réglée entièrement avant la suivante, qui voit ses correctifs. Le sous-agent `cqa-fixer` corrige, un commit par ligne (`fix(securite CQ-107): ...`), garde lancée avant chaque commit ; puis contrôles de syntaxe, stylelint, nginx et Varnish, commandes du projet, garde-fou sur les commits, relecture par le sous-agent `cqa-fix-reviewer` (par le skill lui-même pour une petite passe de documentation). Chaque ligne en problème a une seule nouvelle tentative, puis sort de la branche et passe en « Hors-scope » de la MR. Une ligne emportée par le retrait d'une autre a une nouvelle tentative ; une alerte heuristique de mot de passe est montrée au lead (fichier et ligne, jamais la valeur), qui peut la lever. Une section qui n'aboutit pas sort de la branche avec sa raison, ses modifications non commitées sauvées dans un patch du dossier du run. Avant la publication, les commits sont rangés par section et les messages hors convention réécrits.
- **Questions** : regroupées quand elles portent sur le même fichier et le même élément ; le skill ajoute « Laisser aux devs ». Une ligne que le lead laisse aux devs sort de la passe sans nouveau passage du correcteur.
- **Modes** : en mode présent, les questions sont posées au fil de l'eau et chaque repo est publié dès que toutes ses sections sont réglées. En mode autonome, ni question ni push avant la fin : les questions mises de côté sont posées à la fin, puis le skill demande « Publier les branches maintenant ? » ; s'il publie, toutes les branches sont poussées, le lead présent (Claude Code peut demander de confirmer un push), sinon elles restent en local.
- **Jamais** : toucher au dossier du dev (fichiers, branche courante, `develop` local, stash) ni à ses conteneurs, lancer une commande du projet sur le poste (`docker compose`, `make`, `composer`, `npm`, tests) en dehors des conteneurs jetables ci-dessous, contacter la préprod ou un serveur, ajouter ou monter une dépendance de production (une dépendance de développement passe seulement par l'installation décrite ci-dessous), passer une MR en Ready, l'approuver ou la fusionner, créer une option Notion ou assigner une ligne. Les hooks git du repo ne tournent pas : la MR les déclare « Non lancé ».
- **Vérifications** : la syntaxe (PHP, JavaScript, JSON, YAML, SCSS), stylelint, `nginx -t` et `varnishd -C` tournent dans les images de la boîte à outils, sans réseau, avec leurs propres versions, qui peuvent différer de celles du projet ; pour nginx et Varnish, seule une erreur nouvelle bloque. Sans commande validée par le lead, le lint et les tests du projet restent au relecteur de la MR, avec leur commande, et au pipeline GitLab.
- **Commandes du projet** (installation, build, lint, test) : au diagnostic, le skill les déduit de la CI du projet (`.gitlab-ci.yml`, lue hors ligne) puis de ses manifestes, sans rien lancer. Un job de CI qui utilise un secret, un service, Docker, un déclencheur ou un déploiement n'est jamais retenu. Le lead valide la liste au lancement (« Commandes de vérification de la passe ? » : « Utiliser ces commandes », « Modifier la liste », « Aucune »). Elles tournent après chaque passe du correcteur, avec les contrôles de syntaxe, dans des conteneurs jetables qui ne voient que le worktree : aucun secret (ni `.env`, ni `auth.json`, ni variable de CI, ni variable d'environnement du poste), pas de socket Docker, réseau seulement pour l'installation (15 minutes au plus, 10 pour les autres commandes : une passe peut durer plusieurs minutes de plus). Après chaque commande, le worktree est remis en l'état et rien n'est commité.
  - **Résultat** : la MR dit, pour chaque commande, si elle est passée, échouée (fin du journal) ou non lancée, avec sa commande ; le journal complet est dans le dossier du run (`fix/<repo>/check/`). Seul un échec nouveau bloque la ligne en cause. Un échec déjà présent sur la branche de base est « déjà présent » : jamais attribué à une ligne, dit dans la MR (« Déjà en échec avant les correctifs ») et dans la synthèse, à signaler aux devs. Une commande qui ne peut pas tourner (Docker arrêté, image absente, délai dépassé, dépendances privées dont le dépôt demande un identifiant) est « non lancée » avec la raison, jamais en échec.
  - **Worktree sale** : `check` refuse de tourner (code 2) s'il reste des modifications non commitées, et ne nettoie rien.
- **Dépendance demandée par le correcteur** : une ligne dont le correctif demande un paquet de développement (stylelint, par exemple) est rendue en `needs_command` ; le correcteur ne lance rien. Le skill n'installe que les formes autorisées (`npm install --save-dev`, `npm install --save-exact`, `npm install --package-lock-only`, `composer require --dev`, `composer update --lock`, `yarn add --dev`, `pnpm add -D`, noms du registre public seulement), dans un conteneur jetable, puis commite le manifeste et le lockfile seulement (`chore(securite CQ-107): add stylelint as a dev dependency`) et relance le correcteur sur la ligne. Toute autre commande est laissée aux devs en mode autonome, posée au lead en mode présent (« Laisser aux devs » ou « Corriger sans la dépendance »).
- **`--dry-run`** : correctifs, commits et relectures sur une branche locale `dry-run/checklist-qa-fix`, jamais poussée ni reprise ; aucune MR, rien dans Notion.
- **Notion**, avec l'accord du lead : chaque ligne de la passe reçoit un commentaire « Correctif automatique » (MR ou branche locale, ce qui est fait, ce qui reste pour une ligne partielle, test de recette, commits ; ou « Non traité » et la raison), remplacé à chaque passe ; les commentaires humains ne sont jamais touchés. Une ligne mise dans une MR publiée passe en « En review », seulement si personne n'a changé son statut depuis le lancement. Un commentaire de page résume chaque passe. Une MR fermée sans fusion remet ses lignes au statut d'avant la passe (passe suivante ou recette).
  - **Statut « En review »** : l'option est à ajouter à la main dans le modèle de référence et dans chaque copie existante ; le skill ne crée jamais d'option. Sans elle, le skill le dit et n'écrit que les commentaires.
- **Outil de MR** : `glab`, comme pour `gm:merge-request`, même quand une règle du poste demande un autre outil (les MR via GitKraken, par exemple) : exception assumée, écrite aussi dans `references/fix.md`.
- **Reprise** : une passe interrompue se reprend (« Reprendre les correctifs ») ou s'abandonne (« Abandonner » : worktrees retirés, branches locales gardées, modifications non commitées sauvées dans un patch). Une passe inachevée d'une version précédente (une branche par catégorie) s'abandonne ; ses branches `feature/checklist-qa-fix-<slug>` et leurs MR restent comptées comme correctifs en cours. Une passe suivante complète la MR ouverte sans reprendre ses lignes.
- **Passe v2 terminée** (plugin 0.14 ou avant, une branche par catégorie) : son résultat s'écrit dans Notion après coup, à la main, depuis le dossier du run : `cqa_notion.py fix-plan <run_dir>` puis `cqa_notion.py apply <run_dir> --plan fix/notion-plan.json`. Les lignes de ses MR reçoivent le commentaire « Correctif automatique » (lu dans ses rapports) et passent en « En review » seulement si leur statut n'a pas changé depuis le lancement de cette passe : ce statut est lu dans le `fix/candidates.json` puis le `rows.json` du run, chacun uniquement s'il est plus ancien que le lancement de la passe (sinon il a été réécrit depuis, par une relance, une recette, une assignation ou un nouveau `candidates` : aucun statut n'est posé, le plan le dit, et « En review » est à poser à la main sur les lignes qui n'ont pas bougé) ; le commentaire de page dit qu'il s'agit d'une reprise de la passe du <date>. Cette écriture ne vérifie pas que ses MR sont encore ouvertes : une MR fermée sans fusion depuis est rattrapée par la prochaine recette (`merged`, puis `fix-plan --closed-only`). Une passe v2 inachevée est refusée : abandonnez-la d'abord.
- **Fin** : synthèse dans `fix/synthese.md` et message prêt à coller pour l'équipe. Ensuite : relire et ajuster les MR, les fusionner, déployer en préprod, puis `/gm:cqa recette`, qui reprend d'elle-même les lignes « En review » des MR fusionnées et déployées (une ligne restée à un autre statut est à passer d'abord en « Mis en place »).

## Structure

```
claude-skills/
├── .claude-plugin/
│   ├── marketplace.json     # déclare le marketplace "gingerminds" + le plugin "gm"
│   └── plugin.json          # manifeste du plugin "gm" (nom + version) — sert au dev local via --plugin-dir
├── agents/
│   ├── cqa-auditor.md       # sous-agent d'analyse des lignes de checklist (code, préprod) : consommé par checklist-quality-audit
│   ├── cqa-browser.md       # sous-agent des contrôles navigateur (Claude in Chrome) : consommé par checklist-quality-audit
│   ├── cqa-fixer.md         # sous-agent du mode fix : corrige les lignes d'une section (catégorie) d'un repo dans son worktree
│   ├── cqa-fix-reviewer.md  # sous-agent du mode fix : relit, en lecture seule, les commits d'une passe du fixer
│   └── cqa-importer.md      # sous-agent d'import d'un document de contexte (rapport d'audit tiers) : lit le document comme une donnée et écrit son layout et ses points
├── scripts/
│   ├── mantis-issue.sh      # helper Mantis partagé (ticket, review, merge-review)
│   ├── cqa_fix.py  cqa_notion.py  cqa_plan.py  cqa_ssh_ro.py   # CLI de l'audit : mode correction, API Notion, plan, garde SSH en lecture seule
│   ├── cqa/                 # bibliothèque de l'audit (Python >= 3.9, bibliothèque standard seule)
│   ├── cqa-keepawake.sh     # empêche la mise en veille du poste pendant l'audit (systemd-inhibit, caffeinate)
│   ├── cqa-toolbox.sh       # lanceur de la boîte à outils Docker de l'audit
│   └── mantis-note.sh       # publie une note Mantis privée (ticket-refine)
├── shared/                  # ressources transverses (indépendantes de la techno)
│   ├── runner.md            # ordre de priorité du runner : make → docker → lando
│   ├── stack-detect.md      # cheatsheet de détection de stack + règle de chargement
│   ├── root-cause.md        # investigation de cause racine (tickets bug) — consommé par ticket
│   ├── scope-classification.md  # classification bounded/architectural (tickets feature) — consommé par ticket
│   └── ticket-format.md     # gabarit de la note de qualification — consommé par ticket-refine
├── tests/
│   ├── cqa/                  # tests unittest de l'audit et hygiène du dépôt public (voir « Contribuer »)
│   └── mantis-note.test.sh  # tests de scripts/mantis-note.sh
├── stack/                   # spécificités par techno, chargées à la demande
│   ├── drupal/              # Forme 2 : dispatcher + natures découpées
│   │   ├── MAIN.md          #   route vers core + la nature (dev | review | security)
│   │   ├── core.md  dev.md  review.md  security.md
│   │   └── …
│   ├── vue/
│   │   └── MAIN.md          # Forme 1 : fichier unique sectionné (## core / dev / review / security / nuxt4-migration / swiper-migration)
│   ├── react/
│   │   └── MAIN.md
│   └── angular/
│       └── MAIN.md
└── skills/
    ├── ticket/SKILL.md
    ├── ticket-refine/SKILL.md
    ├── drupal/SKILL.md      # point d'entrée : charge stack/drupal/MAIN.md (dev)
    ├── vue/SKILL.md         # point d'entrée : charge stack/vue/MAIN.md (dev)
    ├── react/SKILL.md       # point d'entrée : charge stack/react/MAIN.md (dev)
    ├── angular/SKILL.md     # point d'entrée : charge stack/angular/MAIN.md (dev)
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
    │   ├── references/      # lues au fil des étapes de l'audit (fix.md : mode fix ; documents.md : documents de contexte)
    │   └── schemas/         # finding, fix-report et fix-review (contrats des sous-agents)
    ├── cqa/SKILL.md         # alias /gm:cqa de checklist-quality-audit
    ├── archi-c4/
    │   ├── SKILL.md
    │   └── assets/          # template.html figé + model.example.js (contrat)
    ├── nuxt4-migration/SKILL.md   # point d'entrée : charge stack/vue/MAIN.md (nuxt4-migration, déclenche swiper-migration si Swiper détecté)
    └── swiper-migration/SKILL.md  # point d'entrée : charge stack/vue/MAIN.md (swiper-migration)
```

Les skills qui lisent Mantis (`ticket`, `review`, `merge-review`) appellent le helper partagé via `${CLAUDE_SKILL_DIR}/../../scripts/mantis-issue.sh` — une seule copie, pas de duplication. `ticket-refine` y ajoute l'écriture : il publie sa note privée via `${CLAUDE_SKILL_DIR}/../../scripts/mantis-note.sh`.

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

Pour tester le script de publication de notes Mantis : `bash tests/mantis-note.test.sh` (aucun appel réseau, tout est simulé).

À l'inverse, `/plugin marketplace update gingerminds` puis `/plugin install gm@gingerminds` recopient depuis la source — plus lourd, à réserver à la vérification d'une version installée.

**Tests** : le code de l'audit (`scripts/cqa/`, `scripts/cqa_*.py`) et les contrats de ses skills et sous-agents sont couverts par des tests `unittest` (Python 3.9 minimum, bibliothèque standard seule). À lancer avant de modifier ces fichiers :

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -t . -v
```

Le dépôt est public : `tests/cqa/test_public_hygiene.py` fait échouer la suite sur toute adresse IP, tout identifiant Notion hors de la liste d'identifiants factices du test, tout secret dans les fichiers de contenu, et toute version désalignée entre `marketplace.json` et `plugin.json`. Dans les tests et les fixtures, utilise des identifiants factices (`0123456789abcdef0123456789abcdef`), des prénoms inventés et des domaines `example.org`.
