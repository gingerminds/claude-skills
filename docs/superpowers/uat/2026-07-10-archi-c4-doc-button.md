# UAT — Bouton « Ouvrir le détail → » (lien `doc`, index C2 → unité C3)

**Skill :** `gm:archi-c4` · **Date :** 2026-07-10 · **Portée :** navigation index (C1/C2) → page d'unité (C3/C4)

## Objectif

Valider que, dans la vue projet, un conteneur custom (C2) portant un champ `doc` ouvre le drawer avec un bouton **« Ouvrir le détail → »**, et que ce bouton **navigue vers le fichier d'unité correspondant** (`units/<slug>.html`) qui affiche le niveau C3 — le tout en `file://`, sans réseau.

Comportement attendu (décision de design) : parcours en **deux temps** — clic sur le nœud = drawer ; bouton du drawer = navigation. Les nœuds **sans** `doc` (infra en boîte noire) n'affichent pas le bouton.

## Pré-requis

- `python3` disponible ; un navigateur (macOS : `open`).
- Assets présents : `skills/archi-c4/assets/template.html` et `skills/archi-c4/assets/model.example.js`.

## Mise en place de la fixture (reproductible)

Reconstitue un vrai `.archi/` de test (index + une unité cible réellement atteignable) depuis les assets **commités** :

```bash
python3 - <<'PY'
import pathlib, tempfile
base = pathlib.Path(tempfile.gettempdir()) / "archi-c4-uat"
(base / "units").mkdir(parents=True, exist_ok=True)
tpl  = open("skills/archi-c4/assets/template.html").read()
data = open("skills/archi-c4/assets/model.example.js").read()
target = 'META = null; CONTEXT = null; CONTAINER = null; COMPONENT = null; CODE = null;'
assert target in tpl, "ligne d'injection introuvable — template modifié ?"

# index.html : vue projet C1 + C2 uniquement (le conteneur `module` porte doc: units/product-import.html)
index = data + "\nCOMPONENT = null; CODE = null;\n"
(base / "index.html").write_text(tpl.replace(target, index))

# units/product-import.html : la page d'unité, niveau C3 uniquement
unit = data + "\nCONTEXT = null; CONTAINER = null; CODE = null;\n"
(base / "units" / "product-import.html").write_text(tpl.replace(target, unit))

print("index :", base / "index.html")
print("unité :", base / "units" / "product-import.html")
PY
open "$(python3 -c 'import tempfile,pathlib;print(pathlib.Path(tempfile.gettempdir())/"archi-c4-uat"/"index.html")')"
```

## Scénario d'acceptation

| # | Action | Résultat attendu |
|---|--------|------------------|
| 1 | Ouvrir `index.html` | Seuls les onglets **C1 Contexte** et **C2 Conteneurs** sont visibles ; ouverture sur C1. |
| 2 | Aller sur **C2 Conteneurs** | Le conteneur « Module product_import » est présent (nœud `container`). |
| 3 | **Cliquer** ce conteneur | Le **drawer** s'ouvre (nom, rôle, relations) **et** affiche le bouton **« Ouvrir le détail → »**. |
| 4 | Survoler / inspecter le bouton | Son `href` vaut `units/product-import.html` (chemin relatif). |
| 5 | **Cliquer** « Ouvrir le détail → » | Le navigateur **charge `units/product-import.html`** ; la page affiche l'onglet **C3 Composants** (bandes commandes/parsers/handlers…), pas de page blanche, pas d'erreur console. |
| 6 | Revenir à `index.html`, ouvrir un nœud **sans** `doc` (ex. « MariaDB » en C2) | Le drawer s'ouvre **sans** bouton « Ouvrir le détail → ». |

## Critères de succès (PASS)

- Étapes 3, 4, 5, 6 conformes.
- Aucune erreur dans la console navigateur imputable au diagramme (le message `entryTypes … longtask` éventuel est du bruit navigateur externe, hors périmètre).
- Le C3 de l'unité s'affiche après navigation (le fichier cible existe et se rend).

## Échecs typiques à surveiller (FAIL)

- Le clic sur le conteneur navigue directement sans passer par le drawer → régression du comportement deux-temps.
- Bouton absent alors que le nœud a un `doc`, ou présent sur un nœud sans `doc`.
- `href` erroné / page cible 404 (mauvais `<slug>` ou champ `doc` non aligné sur le nom de fichier).
- Page d'unité blanche (contrat d'injection cassé — voir la ligne sous `@@ARCHI-C4:MODEL@@`).

## Note de non-régression

Ce parcours dépend de trois éléments du template figé : le champ `doc` sur le nœud, la logique `openBtn`/`n.doc` dans `openDrawer`, et le style `.dopen`. Rejouer cette UAT après toute modification de `template.html`.
