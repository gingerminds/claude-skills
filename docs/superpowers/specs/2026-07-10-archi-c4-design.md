# Spec — skill `gm:archi-c4`

**Date** : 2026-07-10
**Dépôt** : `gingerminds/claude-skills` (plugin `gm`)
**Statut** : design validé, prêt pour plan d'implémentation

## Objectif

Un skill stack-agnostique, invoqué `/gm:archi-c4`, qui **prépare, génère et maintient** une documentation d'architecture **C4 interactive** dans un dossier `.archi/` à la racine du projet cible. Le rendu vise la présentation de référence fournie par l'utilisatrice : un HTML autonome avec sélecteur de niveau C1→C4, nœuds cliquables, tracé des liaisons (wires) et drawer de détail.

But d'usage double : **onboarding** (« découvre le projet via `.archi/index.html` ») et **doc vivante** (« à tout changement d'archi, mets à jour le C4 »).

## Décisions actées (brainstorming)

| # | Décision | Choix retenu |
|---|---|---|
| A | Production du HTML | **Template figé + données seules** : le chrome (CSS+JS interactif) est versionné une fois dans le skill ; l'agent ne rédige que le bloc de données JS. |
| B | Granularité / rôle de l'index | **Index = vue projet C1/C2 ; un HTML par unité pour C3/C4.** L'index est une vraie carte, pas une liste de liens. |
| C | Profondeur C4 (UML code) | **C1–C3 par défaut. C4 uniquement via un `AskUser`** qui prévient des risques (volatil à chaque refactor, coût de génération/maintenance). |
| D | Couverture par stack | **Générique par défaut**, contextualisé via `shared/stack-detect.md`. **Ressource `stack/drupal/archi.md` fournie** en v1 ; autres stacks en générique. |
| E | Exclusion du déploiement | **Détecte l'outil, propose l'édit exacte, applique après confirmation.** Jamais de patch silencieux. |
| F | Langue de sortie | **Français**, cohérent avec `gm`. |

## Périmètre : uniquement le code custom

Seul le **code custom** est détaillé au niveau composant/code. Le code tiers (contrib/core/vendor, `node_modules`, paquets pip…) apparaît **en boîte noire** — un nœud « système externe » ou « dépendance » aux niveaux contexte/container, jamais décomposé.

- **Drupal** (détecté via `shared/stack-detect.md` → `stack/drupal/archi.md`) : custom = `modules/custom/**`, `themes/custom/**` (hors `contrib`, hors `core`, hors `vendor`). Source de vérité du câblage : `*.services.yml` (injection de dépendances), classes de `src/`, `*.module` (hooks), plugins, `*.routing.yml`, `*.info.yml`, scripts d'orchestration (`bin/`, `Makefile`).
- **Stack inconnue** : méthode générique — l'agent **demande où vit le code custom** et quelle est la source de vérité du câblage, puis applique la même logique.

## Ce qui est livré dans le dépôt `gm`

```
skills/archi-c4/
  SKILL.md                     # les 3 flux + la méthode d'extraction du modèle
  assets/
    template.html              # chrome figé : CSS + JS interactif (référence dé-spécialisée)
    model.example.js           # modèle de données commenté = le contrat de données
stack/drupal/
  archi.md                     # nouvelle "nature" : où vit le custom, source de vérité, quoi montrer en boîte noire
  MAIN.md                      # +1 ligne dans la table : /gm:archi-c4 → archi.md
```

Le skill reste stack-agnostique : il applique `shared/stack-detect.md`, charge `stack/drupal/archi.md` si Drupal est détecté, sinon reste générique (ne jamais tenter de `Read` une ressource stack inexistante).

## Contrat de données : template figé + modèle

`assets/template.html` est **identique** dans tous les fichiers générés (index et unités). Seul le bloc `MODEL` change. Le chrome n'affiche que **les niveaux présents** dans les données (les onglets C1→C4 absents sont masqués automatiquement).

Vocabulaire repris de la référence :
- **Types de nœud** : `person`, `system`, `external`, `container`, `store`, `component`.
- **Kinds UML** (niveau code) : `interface`, `abstract`, `class`, `trait`.
- **Kinds d'edge** : `flow` (contexte/container), `uses` (DI composant), `extends`, `realize`, `use` (trait), `assoc`.

Structure des blocs (par niveau) :
- `CONTEXT` / `CONTAINER` : `{ lead, layout:{cols,rows}, nodes:[{id,type,name,id2,desc,col,row,span?,hero?,doc?}], edges:[{from,to,kind,label}] }`.
- `COMPONENT` : `{ lead, layout:{type:"comp"}, nodes:{}, edges:[], groups:[], rail:{} }`.
- `CODE` : `{ views:{ <vue>: { lead, nodes:{}, edges:[] } } }`.

**Extension mineure du template** — champ optionnel `doc` sur un nœud : `doc:"units/<slug>.html"`. Quand présent (typiquement sur les containers de l'index), le drawer de détail affiche un bouton **« Ouvrir le détail → »** qui navigue vers le HTML de l'unité. C'est le lien entre la vue projet et les vues unité. Navigation locale `file://` (aucun fetch externe : compatible ouverture directe).

## Les trois flux

### Préparer (si `.archi/` absent)
1. Crée `.archi/` et `.archi/units/`.
2. `.gitignore` : garantit que `.archi/` **reste commité** (retire toute règle qui l'exclurait).
3. **Exclusion déploiement** : détecte l'outil (`deploy.php` Deployer, `.gitlab-ci.yml`, `.dockerignore`/Dockerfile, `Capfile`/Capistrano…), montre la ligne d'exclusion exacte au bon endroit, **applique après confirmation**.
4. **CLAUDE.md projet** (racine, créé si absent) : ajoute de façon **idempotente** (bloc balisé `<!-- gm:archi-c4 -->` … `<!-- /gm:archi-c4 -->`, non dupliqué au re-run) deux instructions :
   - « Pour découvrir ce projet, ouvre `.archi/index.html`. »
   - « À tout changement d'architecture (nouveau service/module/plugin, refactor de câblage), mets à jour le C4 correspondant dans `.archi/`. »

### Faire
1. **Lister les unités custom d'abord** → présenter la liste → **validation utilisateur** avant toute rédaction. Cette liste alimente l'`index.html`.
2. **Vue projet — `.archi/index.html`** : niveaux C1 (contexte : système + acteurs + systèmes externes) et C2 (containers : chaque unité custom + infra partagée — DB, moteur de recherche, cron, stockage…). Les containers custom portent `doc:"units/<slug>.html"` (cliquables).
3. **Par unité — `.archi/units/<slug>.html`** : C3 (composants, issus de la source de vérité : DI, héritage, hooks). C4 (UML par classe) **seulement après `AskUser`** prévenant des risques.
4. Seul le custom est détaillé ; le tiers reste en boîte noire (voir « Périmètre »).

### Maintenir
1. Chaque HTML généré est **stampé** en pied de page : `généré le <date> · commit <hash-court>`.
2. À l'invocation en mode maintenance (ou détecté par une autre skill via l'instruction CLAUDE.md) : comparer le commit stampé aux fichiers custom modifiés depuis. Si l'écart est notable **ou** si la génération est ancienne → **`AskUser` « Faut-il vérifier / mettre à jour le C4 ? »**.
3. Mise à jour = régénère le(s) bloc(s) `MODEL` concerné(s). Le template ne bouge pas.

## Méthode d'extraction du modèle

Le skill décrit comment bâtir un modèle **juste** (pas décoratif) :
- Drupal : lire les `*.services.yml` (nœuds + edges `uses` par arguments injectés), l'arbre de classes de `src/` (edges `extends`/`realize`/`use`), les `*.module` (hooks), plugins et routes ; les scripts `bin/`/`Makefile` deviennent des containers d'orchestration.
- Générique : partir des points d'entrée (CLI, routes, commandes) et suivre les imports/instanciations custom→custom ; s'arrêter à la frontière du tiers (boîte noire).
- Sur un gros projet, l'exploration par unité peut être **déléguée à des sous-agents** (une unité = un agent), puis synthétisée. Guidance, pas obligation.
- Le pied de page de chaque doc **cite explicitement la source de vérité** utilisée (comme la référence : « Source de vérité : `*.services.yml`, `src/…`, `*.module`… »).

## Non-goals (YAGNI)

- Pas de build step ni de dépendance Node : chaque HTML reste **autonome**, ouvrable en `file://`.
- Pas de tracé du code tiers au-delà de la boîte noire.
- Pas de C4 par défaut (opt-in avec avertissement).
- Pas de ressource stack dédiée hors Drupal en v1 (générique sinon).
- Pas de mono-HTML multi-unités ni de génération libre du chrome.

## Critères de succès

- `/gm:archi-c4` sur un projet vierge : scaffolde `.archi/`, patche l'exclusion déploiement (après OK), met à jour CLAUDE.md **sans doublon** au re-run.
- Génère un `index.html` (C1/C2) dont les containers custom ouvrent leur `units/<slug>.html` (C3), le tout **visuellement identique** à la référence (même chrome), en `file://`.
- Le contrib/core/vendor n'apparaît **jamais** décomposé.
- Chaque doc est stampée (date + commit) et le mode maintenance sait proposer une mise à jour.
- Deux runs sur le même code produisent des HTML **stables** (template figé) ; seuls les blocs `MODEL` diffèrent quand le code change.
