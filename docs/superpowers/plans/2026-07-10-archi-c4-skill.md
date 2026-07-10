# Skill `gm:archi-c4` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer le skill `/gm:archi-c4` qui prépare, génère et maintient une doc d'architecture C4 interactive dans `.archi/`, avec un template HTML figé alimenté uniquement par des données.

**Architecture :** Un template HTML autonome (chrome CSS+JS figé, dérivé de l'exemple de référence) est versionné dans le skill. L'agent ne rédige que le bloc de données (`META` + `CONTEXT`/`CONTAINER`/`COMPONENT`/`CODE`) et l'injecte dans une copie du template. Le skill reste stack-agnostique via `shared/stack-detect.md` et charge `stack/drupal/archi.md` quand Drupal est détecté. Trois flux : Préparer / Faire / Maintenir.

**Tech Stack :** Markdown (skills), HTML/CSS/JS vanilla ES5 (template, aucun build, aucune dépendance externe), Bash (vérifs), `node --check` (validation syntaxe JS des assets).

## Global Constraints

- **Langue de sortie : français.** Tout le contenu généré (docs, drawer, légendes, leads) et les instructions écrites dans le projet cible sont en français.
- **HTML 100% autonome.** Aucun `fetch`/XHR/CDN/police distante/`import` : tout est inline, ouvrable en `file://`. Un JSON externe est interdit (bloqué en `file://`).
- **Aucune étape de build ni dépendance Node** côté projet cible. `node --check` n'est utilisé QUE comme vérif de syntaxe des assets du dépôt `gm`, pas requis à l'exécution du skill.
- **Chemins ancrés sur `${CLAUDE_SKILL_DIR}`** dans toute ressource référencée (comme les skills existants), ex. `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.
- **Périmètre : seul le code custom est détaillé.** Contrib/core/vendor/`node_modules`/paquets tiers → boîte noire (`type: external`), jamais décomposé.
- **CLAUDE.md projet : édition idempotente** via bloc balisé `<!-- gm:archi-c4 -->` … `<!-- /gm:archi-c4 -->` (jamais de doublon au re-run).
- **Jamais de patch silencieux d'un fichier de déploiement** : détecter, montrer l'édit exacte, appliquer après confirmation.
- **C1–C3 par défaut ; C4 seulement après un `AskUser`** prévenant des risques (volatilité au refactor, coût).
- **Source de référence** (à ne pas modifier) : `docs/superpowers/specs/reference-c4-example.html` — le chrome complet + données d'exemple, stages déjà pilotés par données.
- **Version du plugin** : `.claude-plugin/plugin.json` passe de `0.5.0` à `0.6.0`.

## File Structure

- `skills/archi-c4/assets/model.example.js` — **créer.** Le contrat de données commenté (`META` + 4 niveaux). Sert de gabarit à l'agent et de fixture de test du template.
- `skills/archi-c4/assets/template.html` — **créer.** Le chrome figé : identique au fichier de référence mais bloc de données remplacé par un marqueur d'injection, chrome (titre/sous-titre/pied) piloté par `META`, sous-nav C4 construite depuis les données, onglets de niveau auto-masqués, lien `doc` vers les unités.
- `skills/archi-c4/SKILL.md` — **créer.** Les trois flux + méthode d'extraction + repli générique.
- `stack/drupal/archi.md` — **créer.** Nature « archi » pour Drupal : où vit le custom, source de vérité, quoi mettre en boîte noire.
- `stack/drupal/MAIN.md` — **modifier.** +1 ligne dans la table de routage (`/gm:archi-c4` → `archi.md`).
- `README.md` — **modifier.** +1 ligne dans le tableau des skills + mise à jour de l'arbre « Structure ».
- `.claude-plugin/plugin.json` — **modifier.** Bump version `0.5.0` → `0.6.0`.

---

### Task 1 : Contrat de données `model.example.js`

**Files:**
- Create: `skills/archi-c4/assets/model.example.js`
- Source de vérité des données : `docs/superpowers/specs/reference-c4-example.html` (bloc entre `/* ======= MODEL DATA` et `/* ======= /MODEL DATA ======= */`)

**Interfaces:**
- Produces : cinq globals JS — `META`, `CONTEXT`, `CONTAINER`, `COMPONENT`, `CODE` — consommés par `template.html` (Tasks 2–4).
  - `META = { eyebrow:string, title:string(HTML), subtitle:string(HTML), footer:string(HTML) }`
  - `CONTEXT`/`CONTAINER = { lead, layout:{type:"grid",cols,rows}, nodes:[{id,type,name,id2,desc,col,row,span?,hero?,doc?}], edges:[{from,to,kind,label?}] }`
  - `COMPONENT = { lead, layout:{type:"comp"}, nodes:{<id>:{type,name,id2,desc,file?}}, edges:[{from,to,kind,label?}], groups:[{band,title,grp,ids}], rail:{title,ids} }`
  - `CODE = { layout:{type:"code"}, views:{ <key>:{ label:string, layout:{type:"code"}, lead, nodes:{<id>:{kind,name,id2,desc,file?,col,row,span?,attrs?,methods?}}, edges:[{from,to,kind,label?}] } } }`
  - `type` ∈ `person|system|external|container|store|component` · `kind` ∈ `interface|abstract|class|trait` · edge `kind` ∈ `flow|uses|extends|realize|use|assoc`.

- [ ] **Step 1 : Créer le fichier avec l'en-tête de contrat + `META`**

Créer `skills/archi-c4/assets/model.example.js` commençant par :

```js
/*
 * model.example.js — CONTRAT DE DONNÉES du skill gm:archi-c4.
 *
 * Le template (assets/template.html) est FIGÉ. Seul ce bloc de données change.
 * L'agent régénère META + les niveaux présents, puis les injecte dans une copie
 * du template à la place du marqueur "@@ARCHI-C4:MODEL@@".
 *
 * Règles : sortie en français ; seuls META + les niveaux présents sont fournis
 * (un niveau absent => sa variable vaut null => son onglet est masqué).
 * Types de noeud : person | system | external | container | store | component
 * Kinds UML     : interface | abstract | class | trait
 * Kinds d'edge  : flow (C1/C2) | uses (DI, C3) | extends | realize | use | assoc
 */

var META = {
  eyebrow: "Architecture · Modèle C4",
  title: 'Module <span style="font-family:var(--font-mono);font-weight:640">product_import</span>',
  subtitle: "Exemple de référence : pipeline d'import du catalogue produit depuis le PIM produit.",
  footer: 'Source de vérité : <code>product_import.services.yml</code>, <code>src/Service</code>, <code>product_import.module</code>. Diagramme interactif — survolez ou sélectionnez un élément pour tracer ses relations.'
};
```

- [ ] **Step 2 : Copier les quatre niveaux depuis la référence**

Copier verbatim les objets `var CONTEXT`, `var CONTAINER`, `var COMPONENT`, `var CODE` depuis le bloc MODEL de `docs/superpowers/specs/reference-c4-example.html` à la suite de `META` dans `model.example.js`.

- [ ] **Step 3 : Ajouter le champ `label` à chaque vue de `CODE.views` et un exemple de `doc`**

Dans le `CODE` copié, ajouter une propriété `label` à chaque vue (le template construit la sous-nav depuis ces labels) :

```js
// dans CODE.views.content, en tête de l'objet :
      content: {
        label: "Handlers de contenu",
        layout: { type: "code" },
        lead: "<b>Niveau code — Handlers de contenu.</b> …",
```

De même : `CODE.views.media.label = "Handlers de média"` et `CODE.views.parser.label = "Parsers"`.

Puis, pour documenter le champ `doc`, ajouter sur le nœud `module` de `CONTAINER` (le hero) la propriété : `doc: "units/product-import.html"` (démontre le lien index → unité).

- [ ] **Step 4 : Valider la syntaxe JS**

Run: `node --check skills/archi-c4/assets/model.example.js`
Expected: aucune sortie, exit 0 (le fichier parse).

- [ ] **Step 5 : Vérifier la présence des 5 globals et du contrat**

Run: `grep -nE "^var (META|CONTEXT|CONTAINER|COMPONENT|CODE)\b" skills/archi-c4/assets/model.example.js`
Expected: 5 lignes (une par global).
Run: `grep -c "label:" skills/archi-c4/assets/model.example.js` — attendre ≥ 3 (les labels de vues).

- [ ] **Step 6 : Commit**

```bash
git add skills/archi-c4/assets/model.example.js
git commit -m "feat(archi-c4): contrat de données du modèle C4 (model.example.js)"
```

---

### Task 2 : Template figé `template.html` (chrome piloté par données)

**Files:**
- Create: `skills/archi-c4/assets/template.html`
- Source : `docs/superpowers/specs/reference-c4-example.html`
- Fixture de test : `skills/archi-c4/assets/model.example.js` (Task 1)

**Interfaces:**
- Consumes : les globals `META`/`CONTEXT`/`CONTAINER`/`COMPONENT`/`CODE` de Task 1.
- Produces : `template.html` avec le marqueur d'injection `@@ARCHI-C4:MODEL@@` (ligne unique remplacée par le bloc de données à la génération) ; chrome (titre/sous-titre/eyebrow/pied) et sous-nav C4 pilotés par données.

- [ ] **Step 1 : Copier la référence en template**

```bash
cp docs/superpowers/specs/reference-c4-example.html skills/archi-c4/assets/template.html
```

- [ ] **Step 2 : Remplacer le bloc de données par le marqueur d'injection**

Dans `skills/archi-c4/assets/template.html`, remplacer tout le bloc compris entre `/* ======= MODEL DATA ...` et `/* ======= /MODEL DATA ======= */` (inclus) par exactement :

```js
  /* @@ARCHI-C4:MODEL@@
     Ce marqueur est remplacé par gm:archi-c4 : le bloc "var META = {…}; var CONTEXT = …"
     (voir assets/model.example.js). Un niveau absent doit valoir null. */
  var META = null, CONTEXT = null, CONTAINER = null, COMPONENT = null, CODE = null;
```

- [ ] **Step 3 : Rendre le chrome (titre/sous-titre/eyebrow/pied) data-driven**

Dans le HTML du masthead, remplacer le contenu statique par des cibles vides :

```html
    <div class="title-block">
      <p class="eyebrow" id="eyebrow"></p>
      <h1 id="title"></h1>
      <p class="subtitle" id="subtitle"></p>
    </div>
```

Et le pied de page :

```html
  <footer id="footer"></footer>
```

- [ ] **Step 4 : Construire la sous-nav C4 depuis les données**

Dans le `<script>`, remplacer le bloc statique de liaison de la sous-nav (l'actuel `subnav.querySelectorAll("button").forEach(...)` en bas) par une fonction `buildSubnav()` appelée à l'init, et supprimer tout `<button>` codé en dur dans `#subnav` (déjà vide dans la référence) :

```js
  function buildSubnav() {
    subnav.innerHTML = "";
    if (!(CODE && CODE.views)) return;
    var keys = Object.keys(CODE.views);
    if (!keys.length) return;
    subnav.appendChild(el("span", "slabel", "Vue :"));
    keys.forEach(function (k, i) {
      var b = el("button", null, esc(CODE.views[k].label || k));
      b.dataset.view = k;
      b.setAttribute("aria-pressed", i === 0 ? "true" : "false");
      b.addEventListener("click", function () {
        state.codeView = k;
        subnav.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
        show("code");
      });
      subnav.appendChild(b);
    });
    state.codeView = keys[0];
  }
```

- [ ] **Step 5 : Appliquer META et appeler buildSubnav à l'init**

Juste avant l'appel final `show("component");`, insérer :

```js
  if (META) {
    if (META.title) document.title = String(META.title).replace(/<[^>]+>/g, "").trim();
    $("#eyebrow").innerHTML = META.eyebrow || "";
    $("#title").innerHTML = META.title || "";
    $("#subtitle").innerHTML = META.subtitle || "";
    $("#footer").innerHTML = META.footer || "";
  }
  buildSubnav();
```

(Ne pas encore toucher au `show("component")` final — c'est la Task 3.)

- [ ] **Step 6 : Test manuel — injecter la fixture et ouvrir dans un navigateur**

Créer une copie de test avec les données d'exemple injectées :

```bash
python3 - <<'PY'
tpl = open("skills/archi-c4/assets/template.html").read()
data = open("skills/archi-c4/assets/model.example.js").read()
marker_line = 'var META = null, CONTEXT = null, CONTAINER = null, COMPONENT = null, CODE = null;'
assert marker_line in tpl, "marqueur d'injection introuvable"
open("/tmp/archi-c4-test.html","w").write(tpl.replace(marker_line, data))
print("écrit /tmp/archi-c4-test.html")
PY
open /tmp/archi-c4-test.html
```

Expected (checklist visuelle, doit être identique à l'exemple de référence) :
- Le titre « Module product_import », le sous-titre et le pied s'affichent (via META).
- Onglet C3 (Composants) actif par défaut ; les 4 onglets présents.
- La sous-nav affiche 3 boutons (Handlers de contenu / média / Parsers) au niveau C4.
- Survol d'un composant → traçage des liaisons ; clic → drawer de détail.
- Aucune erreur dans la console navigateur.

- [ ] **Step 7 : Vérifs statiques**

Run: `grep -c "@@ARCHI-C4:MODEL@@" skills/archi-c4/assets/template.html` → attendre `1`.
Run: `grep -c "var CONTEXT = {" skills/archi-c4/assets/template.html` → attendre `0` (aucune donnée en dur).
Run: `grep -nE "https?://|cdn|fonts\.|fetch\(|XMLHttpRequest" skills/archi-c4/assets/template.html` → attendre `0` correspondance (autonomie).

- [ ] **Step 8 : Commit**

```bash
git add skills/archi-c4/assets/template.html
git commit -m "feat(archi-c4): template HTML figé, chrome et sous-nav pilotés par données"
```

---

### Task 3 : Onglets de niveau auto-masqués + niveau par défaut

**Files:**
- Modify: `skills/archi-c4/assets/template.html` (bloc `<script>`, fonction d'init)

**Interfaces:**
- Consumes : `MODELS` (déjà construit : `{ context, container, component, code }`), `CODE`.
- Produces : comportement — seuls les niveaux non-nuls ont un onglet visible ; le niveau affiché au chargement est le premier présent dans l'ordre `context → container → component → code`.

- [ ] **Step 1 : Ajouter la règle CSS qui masque réellement un onglet**

Dans le `<style>`, section « Segmented level switcher », ajouter (sans elle, `display:flex` l'emporte sur l'attribut `[hidden]` et l'onglet resterait visible) :

```css
  .levels button[hidden] { display: none; }
```

- [ ] **Step 2 : Ajouter un helper de présence et masquer les onglets absents**

Dans le `<script>`, juste avant le bloc `if (META) { … }` ajouté en Task 2, insérer :

```js
  var LEVEL_ORDER = ["context", "container", "component", "code"];
  function levelPresent(l) {
    return l === "code" ? !!(CODE && CODE.views && Object.keys(CODE.views).length) : !!MODELS[l];
  }
  document.querySelectorAll(".levels button").forEach(function (b) {
    b.hidden = !levelPresent(b.dataset.level);
  });
```

- [ ] **Step 3 : Démarrer sur le premier niveau présent**

Remplacer l'appel final `show("component");` par :

```js
  var firstLevel = LEVEL_ORDER.filter(levelPresent)[0];
  if (firstLevel) show(firstLevel);
```

- [ ] **Step 4 : Test manuel — modèle à 2 niveaux (index projet)**

```bash
python3 - <<'PY'
tpl = open("skills/archi-c4/assets/template.html").read()
data = open("skills/archi-c4/assets/model.example.js").read()
# ne garder que META + CONTEXT + CONTAINER : simuler l'index projet
data2 = data + "\nCOMPONENT = null; CODE = null;\n"
marker = 'var META = null, CONTEXT = null, CONTAINER = null, COMPONENT = null, CODE = null;'
open("/tmp/archi-c4-index.html","w").write(tpl.replace(marker, data2))
print("écrit /tmp/archi-c4-index.html")
PY
open /tmp/archi-c4-index.html
```

Expected :
- Seuls les onglets **C1 Contexte** et **C2 Conteneurs** sont visibles (C3/C4 masqués).
- Le chargement s'ouvre sur **C1 Contexte** (premier présent).
- Aucune erreur console.

- [ ] **Step 5 : Non-régression — le modèle complet démarre sur C1**

Régénérer `/tmp/archi-c4-test.html` (commande de Task 2 Step 6) et ouvrir.
Expected : 4 onglets visibles ; ouverture sur **C1 Contexte** (le premier présent, plus l'ancien défaut « component »).

- [ ] **Step 6 : Commit**

```bash
git add skills/archi-c4/assets/template.html
git commit -m "feat(archi-c4): onglets de niveau auto-masqués + démarrage sur le premier présent"
```

---

### Task 4 : Lien `doc` — navigation index → unité

**Files:**
- Modify: `skills/archi-c4/assets/template.html` (drawer HTML + `openDrawer`)

**Interfaces:**
- Consumes : champ optionnel `doc` sur un nœud (ex. container `module` → `"units/product-import.html"`).
- Produces : quand le nœud sélectionné a un `doc`, le drawer affiche un bouton « Ouvrir le détail → » qui fait `location.href = <doc>` (navigation locale relative, compatible `file://`).

- [ ] **Step 1 : Ajouter la cible du bouton dans le HTML du drawer**

Dans `.dbody`, juste après `<p class="drole" id="d-role"></p>`, insérer :

```html
    <a class="dopen" id="d-open" hidden>Ouvrir le détail →</a>
```

- [ ] **Step 2 : Styler le bouton (dans le `<style>`, section drawer)**

```css
  .drawer .dopen { display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; text-decoration: none; font-weight: 600; font-size: 13px; color: #fff; background: var(--tc); padding: 8px 13px; border-radius: 8px; }
  .drawer .dopen[hidden] { display: none; }
  .drawer .dopen:hover { filter: brightness(1.06); }
```

- [ ] **Step 3 : Alimenter le bouton dans `openDrawer`**

Dans `openDrawer`, juste après la ligne `$("#d-role").textContent = n.desc || "";`, insérer :

```js
    var openBtn = $("#d-open");
    if (n.doc) { openBtn.setAttribute("href", n.doc); openBtn.hidden = false; }
    else { openBtn.removeAttribute("href"); openBtn.hidden = true; }
```

- [ ] **Step 4 : Test manuel — clic sur un container avec `doc`**

Régénérer `/tmp/archi-c4-index.html` (Task 3 Step 3) et ouvrir. Aller sur **C2 Conteneurs**, cliquer le nœud « Module product_import ».
Expected :
- Le drawer s'ouvre et affiche le bouton **« Ouvrir le détail → »**.
- Le bouton pointe vers `units/product-import.html` (survol/inspection du `href`).
- Cliquer un nœud SANS `doc` (ex. « MariaDB ») → pas de bouton.

- [ ] **Step 5 : Vérif statique**

Run: `grep -c 'id="d-open"' skills/archi-c4/assets/template.html` → attendre `1`.

- [ ] **Step 6 : Commit**

```bash
git add skills/archi-c4/assets/template.html
git commit -m "feat(archi-c4): lien 'Ouvrir le détail' du drawer (index → unité)"
```

---

### Task 5 : Ressource stack `stack/drupal/archi.md` + routage

**Files:**
- Create: `stack/drupal/archi.md`
- Modify: `stack/drupal/MAIN.md` (table de routage)

**Interfaces:**
- Consumes : chargé par `SKILL.md` (Task 6) via `${CLAUDE_SKILL_DIR}/../../stack/drupal/MAIN.md` quand Drupal est détecté.
- Produces : la connaissance Drupal du périmètre custom et de la source de vérité.

- [ ] **Step 1 : Écrire `stack/drupal/archi.md`**

```markdown
# Stack: Drupal — archi (C4)

Spécifique Drupal pour `/gm:archi-c4`. Chargé en plus de `core.md` (vocabulaire standard/Cache API) via le dispatcher `MAIN.md`.

## Où vit le code custom (ce qu'on détaille)

- `web/modules/custom/**` (ou `modules/custom/**` selon l'arbo) — modules maison.
- `web/themes/custom/**` (ou `themes/custom/**`) — thèmes maison.
- Un module custom peut embarquer des scripts d'orchestration (`bin/`, `Makefile`, `*.sh`, `*.py`) : ce sont des **conteneurs** (C2).

**Jamais détaillé (boîte noire, `type: external`)** : `web/core/**`, `web/modules/contrib/**`, `web/themes/contrib/**`, `vendor/**`. On montre uniquement les services core/contrib **consommés** par le custom, comme nœuds externes du rail C3 (ex. `entity_type.manager`, `menu.link_tree`, `search_api_index`).

## Source de vérité du câblage

Construire le modèle à partir de, par ordre :

1. **`*.services.yml`** — chaque service = un nœud `component` ; chaque `arguments: ['@autre.service']` = un edge `uses` (from = ce service, to = l'argument). Les `@service` core/contrib deviennent des nœuds `external` du rail.
2. **`*.info.yml`** — nom lisible, `dependencies` (modules requis → nœuds externes au niveau container/context).
3. **Classes de `src/`** — `extends` (edge `extends` en C3 ; `extend` en C4), `implements` (edge `realize`), `use <Trait>` (edge `use`). Les commandes Drush (`src/Drush/Commands/`, `src/Commands/`) et contrôleurs/plugins sont les **points d'entrée** (C3).
4. **`*.module` / `*.install`** — hooks (`hook_cron`, `hook_*_alter`…) : un nœud `component` « Hooks du module ».
5. **`*.routing.yml`, plugins (`src/Plugin/**`)** — points d'entrée additionnels.
6. **`bin/`, `Makefile`, `composer.json` scripts** — conteneurs d'orchestration (C2).

Omettre `logger.factory`/`logger.channel.*` des liaisons C3 pour la lisibilité (le noter dans le pied `META.footer`, comme la référence).

## Découpage C4 (niveau code, sur demande)

Regrouper les classes par domaine en **vues** (`CODE.views`) : une vue par famille cohérente (ex. `content`, `media`, `parser`), pas une vue géante. Chaque vue = interfaces + abstraites + classes concrètes reliées par `realize`/`extend`/`use`/`assoc`.
```

- [ ] **Step 2 : Ajouter la ligne de routage dans `stack/drupal/MAIN.md`**

Dans la table « Caller → Nature to load », ajouter la ligne (après la ligne `/gm:security`) :

```markdown
| `/gm:archi-c4` | `${CLAUDE_SKILL_DIR}/../../stack/drupal/archi.md` |
```

- [ ] **Step 3 : Vérifs**

Run: `test -f stack/drupal/archi.md && echo OK`
Run: `grep -c "archi-c4" stack/drupal/MAIN.md` → attendre `1`.

- [ ] **Step 4 : Commit**

```bash
git add stack/drupal/archi.md stack/drupal/MAIN.md
git commit -m "feat(archi-c4): nature 'archi' Drupal (périmètre custom + source de vérité) + routage"
```

---

### Task 6 : `SKILL.md` — les trois flux

**Files:**
- Create: `skills/archi-c4/SKILL.md`

**Interfaces:**
- Consumes : `assets/template.html`, `assets/model.example.js`, `shared/stack-detect.md`, `stack/<stack>/MAIN.md`.
- Produces : le comportement complet du skill invoqué `/gm:archi-c4`.

- [ ] **Step 1 : Écrire `skills/archi-c4/SKILL.md`**

````markdown
---
name: archi-c4
description: Prépare, génère et maintient une documentation d'architecture C4 interactive (HTML autonome) dans .archi/. Trace uniquement le code custom (contrib/core/vendor en boîte noire), vue projet C1/C2 dans index.html + une page par unité pour C3 (et C4 UML à la demande). Stack-agnostique via shared/stack-detect.md ; charge stack/drupal/archi.md sur Drupal. Sortie en français. Use when the user asks to set up / generate / update an architecture diagram or C4 model, "documenter l'archi", "à quoi ressemble le projet", or invokes /gm:archi-c4.
---

# Architecture C4 interactive (`.archi/`)

Produire une doc d'archi **vivante** et **interactive** dans `.archi/` du projet cible, à partir d'un template figé alimenté uniquement par des données. Double usage : onboarding (`.archi/index.html`) et doc à jour.

## Modèle mental

- Le **chrome** (CSS+JS interactif) est figé dans `${CLAUDE_SKILL_DIR}/assets/template.html`. On ne le modifie jamais à la génération.
- On ne rédige que le **bloc de données** : `META` + les niveaux présents (`CONTEXT`, `CONTAINER`, `COMPONENT`, `CODE`). Contrat + exemple complet : `${CLAUDE_SKILL_DIR}/assets/model.example.js` — **le lire avant de générer**.
- **Générer un fichier** = copier `template.html` puis remplacer la ligne marqueur
  `var META = null, CONTEXT = null, CONTAINER = null, COMPONENT = null, CODE = null;`
  par le bloc de données rédigé (un niveau absent reste `null` → son onglet se masque).
- **Périmètre : uniquement le code custom.** Tout tiers (contrib/core/vendor, `node_modules`, paquets) = nœud `external` (boîte noire), jamais décomposé.

## Détecter la stack

1. Appliquer `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.
2. Stack connue avec ressource archi (Drupal) → charger `${CLAUDE_SKILL_DIR}/../../stack/<stack>/MAIN.md` pour la nature **archi** (où vit le custom, source de vérité, boîte noire).
3. Stack sans ressource archi → **méthode générique** : demander à l'utilisateur où vit le code custom et quelle est la source de vérité du câblage (points d'entrée + imports/instanciations custom→custom, arrêt à la frontière du tiers). Ne jamais `Read` une ressource stack inexistante.

## Choisir le flux

- `.archi/` **absent** → **Préparer** puis proposer d'enchaîner sur **Faire**.
- `.archi/` **présent** et l'utilisateur veut (re)documenter → **Faire**.
- `.archi/` **présent** et on soupçonne une dérive → **Maintenir**.

## Flux 1 — Préparer

1. Créer `.archi/` et `.archi/units/`.
2. `.gitignore` : s'assurer que `.archi/` **reste commité** (retirer/neutraliser toute règle qui l'exclurait). `.archi/` **doit** être versionné.
3. **Exclusion du déploiement** : détecter l'outil et proposer l'édit exacte, **appliquer seulement après confirmation** :
   - Deployer (`deploy.php`/`deploy.yaml`) → ajouter `.archi` aux chemins exclus / `--exclude`.
   - `.dockerignore` → ligne `.archi/`.
   - GitLab CI rsync (`.gitlab-ci.yml`) → `--exclude='.archi'` dans la commande de sync.
   - Capistrano (`Capfile`/`config/deploy.rb`) → exclure du release.
   - Aucun outil détecté → le dire, demander lequel, ou documenter et passer.
4. **CLAUDE.md** projet (racine, créé si absent) : insérer **idempotemment** ce bloc (ne pas dupliquer s'il existe déjà — repérer les balises) :

   ```markdown
   <!-- gm:archi-c4 -->
   ## Architecture (C4)
   - Pour découvrir ce projet, ouvre `.archi/index.html` (vue C4 interactive).
   - À tout changement d'architecture (nouveau module/service/plugin, refactor de câblage, nouvelle dépendance externe), mets à jour le C4 correspondant dans `.archi/` via `/gm:archi-c4`.
   <!-- /gm:archi-c4 -->
   ```

## Flux 2 — Faire

1. **Lister d'abord les unités custom à tracer** (selon la ressource stack ou la méthode générique). **Présenter la liste et la faire valider** avant toute rédaction.
2. **Vue projet — `.archi/index.html`** : générer avec `META` + `CONTEXT` (C1 : le système, ses acteurs `person`, ses systèmes externes) + `CONTAINER` (C2 : chaque unité custom en `container`/`component` + l'infra partagée en `store`/`external` — DB, moteur de recherche, cron, stockage). Sur chaque container custom, poser `doc: "units/<slug>.html"`.
3. **Par unité — `.archi/units/<slug>.html`** : générer avec `META` + `COMPONENT` (C3, issu de la source de vérité). `<slug>` = nom d'unité en kebab-case.
4. **C4 (UML code)** : ne PAS le produire par défaut. Proposer via `AskUser` en prévenant : « Le niveau C4 (UML par classe) est le plus détaillé mais devient obsolète à chaque refactor et coûte cher à (re)générer. L'ajouter pour cette unité ? ». Si oui, ajouter `CODE.views` à l'unité (voir découpage en vues dans la ressource stack).
5. **Modèle juste, pas décoratif** : chaque edge doit correspondre à une dépendance réelle (argument DI, `extends`, `use`, appel). Sur un gros projet, l'exploration par unité peut être déléguée à des **sous-agents** (une unité = un agent) puis synthétisée.
6. **Stamp** : renseigner dans `META.footer` la source de vérité utilisée ET la mention de génération : `généré le <AAAA-MM-JJ> · commit <hash-court>` (récupérer via `git rev-parse --short HEAD`).

## Flux 3 — Maintenir

1. Lire le stamp de chaque doc `.archi/**` (`généré le … · commit …`).
2. Comparer aux fichiers custom modifiés depuis ce commit :
   `git diff --name-only <commit>..HEAD -- <chemins custom>`.
3. Si des unités custom ont changé **ou** si la génération est ancienne → **`AskUser` « Faut-il vérifier / mettre à jour le C4 ? »** listant les unités impactées.
4. Mise à jour = régénérer les blocs de données des docs concernés (le template ne bouge jamais). Re-stamp.

## Non-goals

- Ne pas modifier `template.html` à la génération.
- Ne pas décomposer le code tiers (boîte noire seulement).
- Ne pas produire de C4 sans accord explicite.
- Pas de mono-HTML multi-unités, pas de JSON externe, pas d'étape de build.
````

- [ ] **Step 2 : Vérifier le frontmatter et les références de chemins**

Run: `grep -nE "^name:|^description:" skills/archi-c4/SKILL.md` → attendre les deux lignes.
Run: `grep -c 'CLAUDE_SKILL_DIR' skills/archi-c4/SKILL.md` → attendre ≥ 3 (chemins ancrés).
Run: `grep -c 'gm:archi-c4' skills/archi-c4/SKILL.md` → attendre ≥ 1 (balise idempotente).

- [ ] **Step 3 : Commit**

```bash
git add skills/archi-c4/SKILL.md
git commit -m "feat(archi-c4): SKILL.md — flux Préparer/Faire/Maintenir + repli générique"
```

---

### Task 7 : README + version du plugin

**Files:**
- Modify: `README.md` (tableau des skills + arbre « Structure »)
- Modify: `.claude-plugin/plugin.json` (version)

**Interfaces:**
- Consumes : rien.
- Produces : le skill est documenté et la version publiée est incrémentée.

- [ ] **Step 1 : Ajouter la ligne dans le tableau des skills du README**

Dans `README.md`, ajouter à la fin du tableau « ## Skills » :

```markdown
| `archi-c4` | `/gm:archi-c4` | Prépare, génère et maintient une doc d'architecture **C4 interactive** (HTML autonome) dans `.archi/` — vue projet C1/C2 + une page par unité custom pour C3 (C4 UML à la demande). Trace uniquement le code custom (contrib/core/vendor en boîte noire). Stack-agnostique ; ressource dédiée Drupal. |
```

- [ ] **Step 2 : Mentionner le dossier assets dans l'arbre « Structure »**

Dans la section `## Structure` du README, sous l'entrée des skills, ajouter (adapter à la mise en forme existante) :

```
├── skills/
│   └── archi-c4/
│       ├── SKILL.md
│       └── assets/          # template.html figé + model.example.js (contrat)
```

- [ ] **Step 3 : Bump de version**

Modifier `.claude-plugin/plugin.json` : `"version": "0.5.0"` → `"version": "0.6.0"`.

- [ ] **Step 4 : Vérifs**

Run: `grep -c 'archi-c4' README.md` → attendre ≥ 2 (tableau + structure).
Run: `grep '"version"' .claude-plugin/plugin.json` → attendre `0.6.0`.
Run: `node --check skills/archi-c4/assets/model.example.js` → exit 0 (non-régression).

- [ ] **Step 5 : Commit**

```bash
git add README.md .claude-plugin/plugin.json
git commit -m "docs(archi-c4): entrée README + bump plugin 0.6.0"
```

---

## Récapitulatif de vérification finale

Après Task 7, ré-injecter la fixture complète et faire un dernier passage visuel :

```bash
python3 - <<'PY'
tpl = open("skills/archi-c4/assets/template.html").read()
data = open("skills/archi-c4/assets/model.example.js").read()
marker = 'var META = null, CONTEXT = null, CONTAINER = null, COMPONENT = null, CODE = null;'
open("/tmp/archi-c4-final.html","w").write(tpl.replace(marker, data))
print("ok")
PY
open /tmp/archi-c4-final.html
```

Checklist : 4 niveaux, chrome via META, sous-nav C4 (3 vues), traçage au survol, drawer avec « Ouvrir le détail → » sur le container `module`, aucune erreur console, aucun accès réseau.
