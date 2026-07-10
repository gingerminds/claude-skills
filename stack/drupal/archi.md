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
