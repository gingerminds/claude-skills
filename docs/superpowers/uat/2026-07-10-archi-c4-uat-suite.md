# UAT — Suite de recette `gm:archi-c4`

**Skill :** `gm:archi-c4` · **Date :** 2026-07-10 · **Branche :** `feat/archi-c4-skill`

Recette du rendu produit par le skill (le template figé alimenté par le modèle d'exemple). À dérouler dans un navigateur, en `file://`, sans réseau. Le bouton « Ouvrir le détail » a sa fiche détaillée : [2026-07-10-archi-c4-doc-button.md](2026-07-10-archi-c4-doc-button.md) (= UAT-07).

## Mise en place (reproductible)

Reconstitue un `.archi/` de démo depuis les assets **commités** : un `index.html` (vue projet C1/C2) et une unité `units/product-import.html` (C3 + C4), réellement reliés.

```bash
python3 - <<'PY'
import pathlib
base = pathlib.Path("/private/tmp/claude-502/-Users-ccassinat-clara/a184b68a-7b72-4792-820e-b466e4d41548/scratchpad/uat-demo")
(base / "units").mkdir(parents=True, exist_ok=True)
tpl  = open("skills/archi-c4/assets/template.html").read()
data = open("skills/archi-c4/assets/model.example.js").read()
target = 'META = null; CONTEXT = null; CONTAINER = null; COMPONENT = null; CODE = null;'
assert target in tpl, "ligne d'injection introuvable — template modifié ?"
# Vue projet : C1 + C2
(base / "index.html").write_text(tpl.replace(target, data + "\nCOMPONENT = null; CODE = null;\n"))
# Unité : C3 + C4
(base / "units" / "product-import.html").write_text(tpl.replace(target, data + "\nCONTEXT = null; CONTAINER = null;\n"))
print("index :", base / "index.html")
print("unité :", base / "units" / "product-import.html")
PY
```

Puis ouvrir `index.html` (macOS : `open <chemin>`).

## Suite UAT

| ID | Titre | Étapes | Attendu (PASS) |
|----|-------|--------|----------------|
| **UAT-01** | Chrome piloté par `META` | Ouvrir `index.html` ; regarder titre / sous-titre / pied | Titre, sous-titre et pied rendus depuis `META` ; le pied porte le **stamp** `Généré le AAAA-MM-JJ · commit <hash>`. |
| **UAT-02** | Niveaux présents / auto-masquage | Comparer `index.html` et `units/product-import.html` | Index : onglets **C1 + C2** seulement, ouverture sur C1. Unité : onglets **C3 + C4** seulement, ouverture sur C3. Les niveaux absents n'ont pas d'onglet. |
| **UAT-03** | Rendu C1 Contexte | Index → onglet C1 | Nœuds `person`/`system`/`external` positionnés ; légende cohérente ; liaisons (wires) tracées avec libellés. |
| **UAT-04** | Rendu C2 Conteneurs | Index → onglet C2 | Conteneurs custom + infra (`store`/`external`) ; le conteneur `module` (hero) est mis en avant. |
| **UAT-05** | Rendu C3 Composants + traçage | Unité → onglet C3 ; survoler un composant | Bandes (commandes / parsers / handlers…) + rail des services externes ; au survol, les liaisons du composant se tracent (les autres s'estompent). |
| **UAT-06** | Rendu C4 Code + sous-nav | Unité → onglet C4 | Boîtes UML (interface/abstract/class/trait) ; la **sous-nav** affiche 3 vues (Handlers de contenu / média / Parsers) et bascule le rendu au clic. |
| **UAT-07** | Bouton « Ouvrir le détail » (index→unité) | Index → C2 → clic `module` → bouton du drawer | Drawer d'abord, puis le bouton navigue vers `units/product-import.html` (C3). Détail : fiche dédiée. |
| **UAT-08** | Drawer + relations | Cliquer un composant en C3 (unité) | Drawer : rôle, fichier, puces « Dépend de / Utilisé par / Hérite » ; cliquer une puce sélectionne le nœud lié. |
| **UAT-09** | Autonomie / offline | DevTools → onglet Network, recharger | **Aucune** requête réseau (tout inline) ; la page fonctionne sans connexion, ouverte en `file://`. |
| **UAT-10** | Boîte noire du tiers | Parcourir C1→C4 | Le contrib/core/vendor n'apparaît qu'en nœuds `external` (jamais décomposé) ; aucun détail de code tiers. |

## Verdict

- **PASS** si UAT-01 → UAT-10 conformes.
- Bruit toléré : message console `entryTypes … longtask` (navigateur/extension, hors périmètre).
- Rejouer la suite après toute modification de `skills/archi-c4/assets/template.html`.

## Journal d'exécution

| ID | Résultat | Note |
|----|----------|------|
| UAT-01 | ⬜ | |
| UAT-02 | ⬜ | |
| UAT-03 | ⬜ | |
| UAT-04 | ⬜ | |
| UAT-05 | ⬜ | |
| UAT-06 | ⬜ | |
| UAT-07 | ⬜ | |
| UAT-08 | ⬜ | |
| UAT-09 | ⬜ | |
| UAT-10 | ⬜ | |
