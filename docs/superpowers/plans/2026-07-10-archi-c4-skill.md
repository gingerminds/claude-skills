# Skill `gm:archi-c4` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Livrer le skill `/gm:archi-c4` qui prépare, génère et maintient une doc d'architecture C4 interactive dans `.archi/`, avec un template HTML figé alimenté uniquement par des données.

**Architecture :** Un template HTML autonome (chrome CSS+JS figé, dérivé de l'exemple de référence) est versionné dans le skill. L'agent ne rédige que le bloc de données (`META` + `CONTEXT`/`CONTAINER`/`COMPONENT`/`CODE`) et l'injecte dans une copie du template. Le skill reste stack-agnostique via `shared/stack-detect.md` et charge `stack/drupal/archi.md` quand Drupal est détecté. Trois flux : Préparer / Faire / Maintenir.

**Tech Stack :** Markdown (skills), HTML/CSS/JS vanilla ES5 (template, aucun build, aucune dépendance externe), Bash (vérifs), `node --check` (validation syntaxe JS des assets).

## Global Constraints

- **Langue : skill rédigé en anglais, sortie en français.** `SKILL.md` (frontmatter + corps) et les ressources `stack/**` sont en **anglais** (convention du dépôt). Tout le contenu **généré** (docs `.archi/**`, drawer, légendes, leads, messages `AskUser`, bloc CLAUDE.md injecté dans le projet cible) est en **français**. Les exemples de rendu (`reference-c4-example.html`, `model.example.js`) sont en français car ils illustrent la sortie.
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
- `stack/drupal/archi.md` — **créer.** Nature « archi » pour Drupal (anglais) : où vit le custom, source de vérité, quoi mettre en boîte noire.
- `stack/drupal/MAIN.md` — **modifier.** +1 ligne dans la table de routage (`/gm:archi-c4` → `archi.md`).
- `stack/vue/MAIN.md` — **modifier.** +1 ligne (table nature→section) + nouvelle section `## archi` (anglais).
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

### Task 5 : Stack archi resources (Drupal + Vue)

> Ressources rédigées **en anglais** (convention du dépôt) ; elles décrivent une sortie **en français**.

**Files:**
- Create: `stack/drupal/archi.md`
- Modify: `stack/drupal/MAIN.md` (dispatcher table)
- Modify: `stack/vue/MAIN.md` (nature→section table + nouvelle section `## archi`)

**Interfaces:**
- Consumes : chargé par `SKILL.md` (Task 6) via `${CLAUDE_SKILL_DIR}/../../stack/<stack>/MAIN.md` selon la stack détectée.
- Produces : la connaissance Drupal et Vue du périmètre custom et de la source de vérité.

- [ ] **Step 1 : Écrire `stack/drupal/archi.md`** (en anglais)

```markdown
# Stack: Drupal — archi (C4)

Drupal specifics for `/gm:archi-c4`. Loaded on top of `core.md` (standard vocabulary / Cache API) via the `MAIN.md` dispatcher. Instructions are in English; the generated documentation is in French.

## Where custom code lives (what we detail)

- `web/modules/custom/**` (or `modules/custom/**` depending on layout) — in-house modules.
- `web/themes/custom/**` (or `themes/custom/**`) — in-house themes.
- A custom module may ship orchestration scripts (`bin/`, `Makefile`, `*.sh`, `*.py`): these are **containers** (C2).

**Never detailed (black box, `type: external`)**: `web/core/**`, `web/modules/contrib/**`, `web/themes/contrib/**`, `vendor/**`. Show only the core/contrib services **consumed** by custom code, as external nodes in the C3 rail (e.g. `entity_type.manager`, `menu.link_tree`, `search_api_index`).

## Wiring source of truth

Build the model from, in order:

1. **`*.services.yml`** — each service = a `component` node; each `arguments: ['@other.service']` = a `uses` edge (from = this service, to = the argument). Core/contrib `@service` become `external` rail nodes.
2. **`*.info.yml`** — human-readable name, `dependencies` (required modules → external nodes at container/context level).
3. **`src/` classes** — `extends` (`extends` edge in C3, `extend` in C4), `implements` (`realize` edge), `use <Trait>` (`use` edge). Drush commands (`src/Drush/Commands/`, `src/Commands/`), controllers and plugins are the **entry points** (C3).
4. **`*.module` / `*.install`** — hooks (`hook_cron`, `hook_*_alter`…): a single `component` node named « Hooks du module » (French label in the output).
5. **`*.routing.yml`, plugins (`src/Plugin/**`)** — additional entry points.
6. **`bin/`, `Makefile`, `composer.json` scripts** — orchestration containers (C2).

Omit `logger.factory`/`logger.channel.*` from C3 edges for readability (note it in the `META.footer`, like the reference).

## C4 splitting (code level, on request)

Group classes by domain into **views** (`CODE.views`): one view per coherent family (e.g. `content`, `media`, `parser`), not one giant view. Each view = interfaces + abstracts + concrete classes linked by `realize`/`extend`/`use`/`assoc`.
```

- [ ] **Step 2 : Ajouter la ligne de routage dans `stack/drupal/MAIN.md`**

Dans la table « Caller → Nature to load », ajouter la ligne (après la ligne `/gm:security`) :

```markdown
| `/gm:archi-c4` | `${CLAUDE_SKILL_DIR}/../../stack/drupal/archi.md` |
```

- [ ] **Step 3 : Ajouter la ligne de la table de `stack/vue/MAIN.md`**

Dans la table « Nature → section » (en tête du fichier), ajouter la ligne (après la ligne `/gm:security`) :

```markdown
| `/gm:archi-c4` | core + archi |
```

- [ ] **Step 4 : Ajouter la section `## archi` en fin de `stack/vue/MAIN.md`** (en anglais)

Ajouter à la fin du fichier :

```markdown
---

## archi

Consumed by `/gm:archi-c4`. Layered on `core`. Instructions in English; generated documentation in French.

### Where custom code lives (what we detail)

- The whole app repo is custom. Nuxt: `components/`, `composables/`, `stores/` (Pinia), `pages/`, `layouts/`, `middleware/`, `plugins/`, `server/` (Nitro API/routes), `utils/`. Plain Vue: `src/**`.
- Build/orchestration (`nuxt.config.ts`, `vite.config.*`, `Makefile`, CI) → **containers** (C2).

**Never detailed (black box, `type: external`)**: `node_modules/**` — Vue/Nuxt runtime, UI libraries, any dependency. Show only what custom code imports/calls (an external API, the Nitro runtime, a headless CMS, a DB) as `external` nodes.

### Wiring source of truth

1. **Pinia stores** (`defineStore`) — each store = a `component` node; store→store and composable→store calls = `uses` edges.
2. **Composables** (`useX`) — `component` nodes; a component/composable importing another = `uses` edge.
3. **Nitro server routes/handlers** (`server/api/**`, `server/routes/**`) — entry points; external calls (DB, upstream API) = `external` nodes.
4. **Router / pages** (`pages/**`, route config) — entry points; navigation guards/middleware as `component` nodes.
5. **Component import graph** — parent→child and component→composable `uses` edges; stop at `node_modules`.

Typical containers (C2): the Nuxt/Vite app, the Nitro server, and any external API/CMS/DB the app talks to.

### C4 splitting (code level, on request)

Group by feature/domain into `CODE.views` (e.g. `stores`, `composables`, one feature module). Vue has no classes/interfaces: represent composables/stores as `class`-kind nodes and shared TS contracts (interfaces/types) as `interface`-kind nodes, linked by `assoc`/`use`.
```

- [ ] **Step 5 : Vérifs**

Run: `test -f stack/drupal/archi.md && echo OK`
Run: `grep -c "archi-c4" stack/drupal/MAIN.md` → attendre `1`.
Run: `grep -c "archi-c4" stack/vue/MAIN.md` → attendre `1`.
Run: `grep -c "^## archi" stack/vue/MAIN.md` → attendre `1`.

- [ ] **Step 6 : Commit**

```bash
git add stack/drupal/archi.md stack/drupal/MAIN.md stack/vue/MAIN.md
git commit -m "feat(archi-c4): nature 'archi' pour Drupal et Vue (périmètre custom + source de vérité)"
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
description: Prepare, generate and maintain interactive C4 architecture documentation (self-contained HTML) under .archi/. Traces only custom code (contrib/core/vendor shown as black boxes); project-level C1/C2 in index.html plus one page per custom unit for C3 (C4 UML on request). Stack-agnostic via shared/stack-detect.md; loads a stack archi resource for Drupal and Vue. All generated output is written in French. Use when the user asks to set up / generate / update an architecture diagram or C4 model, "documenter l'archi", "à quoi ressemble le projet", or invokes /gm:archi-c4.
---

# Interactive C4 architecture (`.archi/`)

Produce living, interactive architecture docs under the target project's `.archi/`, from a frozen template fed only with data. **All generated output is in French** (leads, drawer text, legends, and the CLAUDE.md block written into the project). Dual use: onboarding (`.archi/index.html`) and up-to-date documentation.

## Mental model

- The **chrome** (interactive CSS+JS) is frozen in `${CLAUDE_SKILL_DIR}/assets/template.html`. Never edit it when generating.
- You only write the **data block**: `META` + the present levels (`CONTEXT`, `CONTAINER`, `COMPONENT`, `CODE`). Contract + full example: `${CLAUDE_SKILL_DIR}/assets/model.example.js` — **read it before generating**. Its French strings show the expected output language.
- **Generate a file** = copy `template.html`, then replace the marker line
  `var META = null, CONTEXT = null, CONTAINER = null, COMPONENT = null, CODE = null;`
  with the data block you wrote (an absent level stays `null` → its tab auto-hides).
- **Scope: custom code only.** Any third party (contrib/core/vendor, `node_modules`, packages) is an `external` node (black box), never decomposed.

## Detect the stack

1. Apply `${CLAUDE_SKILL_DIR}/../../shared/stack-detect.md`.
2. Stack with an archi resource → load `${CLAUDE_SKILL_DIR}/../../stack/<stack>/MAIN.md` for the **archi** nature (where custom lives, source of truth, black box):
   - Drupal → `stack/drupal/archi.md`.
   - Vue / Nuxt → the `core` + `archi` sections of `stack/vue/MAIN.md`.
3. Stack without an archi resource → **generic method**: ask the user where custom code lives and what the wiring source of truth is (entry points + custom→custom imports/instantiations, stop at the third-party boundary). Never `Read` a non-existent stack resource.

## Choose the flow

- `.archi/` **missing** → **Prepare**, then offer to continue with **Build**.
- `.archi/` **present** and the user wants to (re)document → **Build**.
- `.archi/` **present** and drift is suspected → **Maintain**.

## Flow 1 — Prepare

1. Create `.archi/` and `.archi/units/`.
2. `.gitignore`: ensure `.archi/` **stays committed** (remove/neutralise any rule that would exclude it). `.archi/` **must** be versioned.
3. **Deployment exclusion**: detect the tool and propose the exact edit, **apply only after confirmation**:
   - Deployer (`deploy.php`/`deploy.yaml`) → add `.archi` to excluded paths / `--exclude`.
   - `.dockerignore` → a `.archi/` line.
   - GitLab CI rsync (`.gitlab-ci.yml`) → `--exclude='.archi'` in the sync command.
   - Capistrano (`Capfile`/`config/deploy.rb`) → exclude from the release.
   - No tool detected → say so, ask which one, or document and move on.
4. **Project CLAUDE.md** (root, create if absent): insert this block **idempotently** (do not duplicate if the tags already exist). The block content is in French — it targets the project team:

   ```markdown
   <!-- gm:archi-c4 -->
   ## Architecture (C4)
   - Pour découvrir ce projet, ouvre `.archi/index.html` (vue C4 interactive).
   - À tout changement d'architecture (nouveau module/service/plugin, refactor de câblage, nouvelle dépendance externe), mets à jour le C4 correspondant dans `.archi/` via `/gm:archi-c4`.
   <!-- /gm:archi-c4 -->
   ```

## Flow 2 — Build

1. **List the custom units to trace first** (from the stack resource or the generic method). **Present the list and get it validated** before writing anything.
2. **Project view — `.archi/index.html`**: generate `META` + `CONTEXT` (C1: the system, its `person` actors, its external systems) + `CONTAINER` (C2: each custom unit as `container`/`component` + shared infra as `store`/`external` — DB, search engine, cron, storage). On each custom container, set `doc: "units/<slug>.html"`.
3. **Per unit — `.archi/units/<slug>.html`**: generate `META` + `COMPONENT` (C3, from the source of truth). `<slug>` = kebab-case unit name.
4. **C4 (UML code)**: do NOT produce by default. Offer it via `AskUser`, warning the user (message in French): « Le niveau C4 (UML par classe) est le plus détaillé mais devient obsolète à chaque refactor et coûte cher à (re)générer. L'ajouter pour cette unité ? ». If yes, add `CODE.views` to the unit (see per-view splitting in the stack resource).
5. **Accurate model, not decorative**: every edge maps to a real dependency (DI argument, `extends`, `use`, call). On a large project, per-unit exploration may be delegated to **subagents** (one unit = one agent) then synthesised.
6. **Stamp**: in `META.footer`, record the source of truth used AND the generation stamp: `généré le <YYYY-MM-DD> · commit <short-hash>` (`git rev-parse --short HEAD`).

## Flow 3 — Maintain

1. Read each doc's stamp under `.archi/**` (`généré le … · commit …`).
2. Compare with custom files changed since that commit:
   `git diff --name-only <commit>..HEAD -- <custom paths>`.
3. If custom units changed **or** the generation is old → **`AskUser` « Faut-il vérifier / mettre à jour le C4 ? »** listing the impacted units.
4. Update = regenerate the data blocks of the affected docs (the template never changes). Re-stamp.

## Non-goals

- Never edit `template.html` when generating.
- Never decompose third-party code (black box only).
- Never produce C4 without explicit consent.
- No multi-unit mono-HTML, no external JSON, no build step.
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
| `archi-c4` | `/gm:archi-c4` | Prépare, génère et maintient une doc d'architecture **C4 interactive** (HTML autonome) dans `.archi/` — vue projet C1/C2 + une page par unité custom pour C3 (C4 UML à la demande). Trace uniquement le code custom (contrib/core/vendor en boîte noire). Stack-agnostique ; ressources dédiées Drupal et Vue. |
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
