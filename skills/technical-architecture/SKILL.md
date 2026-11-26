---
name: technical-architecture
description: Documents a project's technical architecture for preprod and prod (servers, VMs, networks, Docker containers, databases, ports, access, data flows, third-party services) as draw.io views (.drawio.svg), a README with the flow matrix and known issues, and an on-demand PDF dossier; installs the tooling in the project (make diagrams, make architecture-pdf, a CLAUDE.md rule) and keeps it up to date. Collects facts from the repositories and read-only over SSH, asking only what they don't tell. All generated output is in French. Use when the user asks to document, draw or update the technical or infrastructure architecture, the flow matrix, « schéma d'architecture technique », « matrice des flux », or invokes /gm:technical-architecture.
argument-hint: "[mise-a-jour | pdf [--sans-points-attention]]"
---

# Technical architecture documentation

You document how a project is hosted and how its flows circulate, for preprod and prod, so that a newcomer at the agency and the client both get a clear, dated reference: machines, networks, containers, databases, ports, access, flows, third-party services. You collect the facts yourself (repositories first, then servers read-only over SSH), ask the user only what neither tells you, draw the views, write the README, and install what keeps it all up to date.

## Mental model

**Deliverables, in the project** (default folder `docs/architecture/`, asked only if the project keeps its docs elsewhere):

- `views/NN-<slug>.drawio.svg`: one view per question. Each file is an SVG image that embeds its draw.io diagram: forges display it, draw.io edits it.
- `README.md`: index of the views with their question, legend, flow matrix (one table per environment plus « Hors environnement »), known issues (optional annex), how to edit a view.
- `tools/architecture.py`, `tools/dossier.css`, `tools/architecture.json`: the tool, the PDF stylesheet and the project config.
- `build/`: git-ignored. Facts log, throwaway generators, PNG previews, and the PDF dossier, generated on demand only and never versioned.
- `make diagrams` (re-render and check) and `make architecture-pdf`, plus a rule in the project's `CLAUDE.md` that keeps views and matrix in step with every infrastructure change.

**In the plugin.** In every Bash call, start with `T="${CLAUDE_SKILL_DIR}/../../scripts/technical_architecture"` (variables do not survive between calls; quote every path, folders may hold spaces):

- `"$T/architecture.py"`: the tool copied as is into projects (`extract`, `render`, `refresh`, `check`, `pdf`, `version`; `--racine <folder>` points it at a documentation folder).
- `"$T/dossier.css"`: the PDF stylesheet copied with it.
- `"$T/drawio_kit.py"`: the drawing library for throwaway view generators.
- `assets/example/`: a complete fictitious project (README, `tools/architecture.json`, `generators/view01.py` to `view03.py`, rendered `views/`). It is the model for every deliverable: read it before writing your own.

Rendering and the PDF run in Docker (draw.io headless, pandoc, Chromium); images are pulled at first use. Check `docker info` before the first render and stop with a clear message if Docker is not reachable.

## Choose the mode

Look at the argument and at the project, in this order:

1. **`pdf`** (optionally `--sans-points-attention`): from the repository root, run `python3 docs/architecture/tools/architecture.py pdf` with the flag if given. The tool checks first and generates nothing when the check fails: relay its `ERREUR` lines. On success give the path it prints (in `docs/architecture/build/`) and stop. If the project has no `docs/architecture/tools/architecture.py`, say so and offer the create mode.
2. **`mise-a-jour`**: follow `references/update.md`.
3. **No argument, `docs/architecture/tools/architecture.json` present**: offer the update mode (`references/update.md`), or a targeted change if the user named one.
4. **No argument, `docs/architecture/` present without that config** (documentation made by hand or by an older tool): never overwrite it. Offer to adopt it (section Adopt) or stop.
5. **Otherwise**: create (section Create).

## Create

1. **Qualify.** Follow `references/questions.md`: six questions, one at a time, each pre-filled with what the repositories already tell (project `CLAUDE.md`, docs, compose files, CI, `~/.ssh/config`). The answers fix the public, the other repositories, the environments and their SSH aliases, the actors, the views and the known issues annex.
2. **Collect the facts.** Follow `references/collection.md`: repositories first, then each server over SSH, read-only, under the agency rule. Before writing anything in `docs/architecture/build/`, propose the `.gitignore` line `docs/architecture/build/` (it protects the facts log from a commit); if the user declines, keep the log and the generators in your scratchpad instead. Log every fact in `build/faits.md` with its source and date. Ask the gaps one at a time (« Les sauvegardes des VM, c'est qui ? »).
3. **Write the config** `docs/architecture/tools/architecture.json`, on the model of `assets/example/tools/architecture.json`:
   - `tool_version`: the plugin's, from `python3 "$T/architecture.py" version`;
   - `title` (« Architecture de la plateforme <nom> ») and `pdf` (a file name such as `architecture-<projet>.pdf`);
   - `flow_prefixes`: one capital letter per environment, in reading order, plus one for what does not depend on an environment (default `P` Production, `Q` Préproduction, `X` Hors environnement);
   - `owners`: the name of each role present among `hebergeur`, `agence`, `client`, `tiers`;
   - `known_issues`: the answer to question 6; `repositories`: the local paths of the other repositories (question 2).
4. **Draw the views**, one at a time, following `references/drawio-style.md`:
   - write a generator `docs/architecture/build/gen/NN-<slug>.py` on the model of `assets/example/generators/view02.py` (a `build() -> Diagram` function, owners and date at the top);
   - run it with `PYTHONPATH="$T"` to write `build/gen/NN-<slug>.drawio`, and require `build().overlaps() == []`;
   - render it: `python3 "$T/architecture.py" render build/gen/NN-<slug>.drawio views/NN-<slug>.drawio.svg --png build/NN-<slug>.png`;
   - read the PNG, fix any label or arrow that collides, render again; then give the user the PNG path and iterate on their comments;
   - once the user validates the view, the `.drawio.svg` is the source: later edits go through `extract` and `render`, or through draw.io.
5. **Write the README** from `assets/README.template.md`, on the model of `assets/example/README.md`: replace every `<...>`, one section per view, one matrix table per environment prefix. Drop the « Points d'attention connus » section when `known_issues` is false. Every flow ID drawn on a view has its matrix row, and the reverse; statuses come from the facts log.
6. **Install**, showing each change to an existing file and applying it only once the user agrees:
   - copy `"$T/architecture.py"` and `"$T/dossier.css"` into `docs/architecture/tools/`;
   - `.gitignore`: `docs/architecture/build/` (if not added at step 2);
   - `Makefile`: append `assets/makefile-snippet.mk`, adapted to the file's style (its `.PHONY` habits, its `##` help comments if it has a help target). Without a Makefile, do not create one: the README's « Modifier un schéma » section gives the `python3 docs/architecture/tools/architecture.py refresh` then `check` commands, and `pdf`;
   - project `CLAUDE.md`: insert `assets/claude-md-block.md` between its markers, idempotently (replace what sits between existing markers, never duplicate). Put it in the documentation section if the file has one. Without a Makefile, replace `make diagrams` in the block by the two `python3` commands. If the project has no `CLAUDE.md`, ask before creating one;
   - doc map: if the project has one (`docs/ARCHITECTURE.md`, `docs/README.md` or the like), add a line linking to `docs/architecture/README.md`.
7. **Check**: `python3 docs/architecture/tools/architecture.py check` must print `Schémas vérifiés : <n> vue(s), <m> flux.` Fix every `ERREUR` before going on.
8. **Finish**: summarize what was created and changed, list what is still « à confirmer », propose a commit message (English, Conventional Commits, for example `docs(architecture): document the technical architecture`), and offer `/gm:technical-architecture pdf`.

## Adopt

For a `docs/architecture/` folder that has views but no `tools/architecture.json`:

1. Read its README and views. Infer the config: title from the README, prefixes from the matrix IDs and their table headings, owners from the legend. Show it and let the user correct it.
2. If the folder has its own `tools/architecture.py`, show `diff -u` between it and `"$T/architecture.py"`, explain what changes (config instead of hardcoded paths, PDF in `build/` and no longer versioned), and replace it once the user agrees. Copy `dossier.css` if it is missing.
3. Run `check` and fix what it reports with the user, one error type at a time. A versioned PDF, a README line or a `CLAUDE.md` rule that asks to regenerate it: propose to remove them (the PDF is generated on demand now).
4. Install what is missing (step 6 of Create), then finish as in step 8.

## Rules

- **Language and form.** Everything the project receives is in French: README, labels, legends, matrix, `CLAUDE.md` block, Makefile help. No em dash anywhere (`check` refuses them).
- **Never guess.** What is neither seen nor told is drawn grey and dashed and written « à confirmer ». A status always says how the fact is known: `constaté (JJ/MM/AAAA)`, `décidé`, `indiqué par <qui> (JJ/MM/AAAA)`, `à confirmer`.
- **No secret**, ever: not in the facts log, not in a view, not in the README. Addresses, ports, host names and service accounts are fine.
- **Questions.** One at a time, and only when neither the repositories nor the servers answer.
- **SSH** follows the agency rule and `references/collection.md`: read commands without asking, anything else after the user agrees with its impact in hand, no connection outside the aliases the user chose.
- **Git.** Never commit, push or open a merge request: propose the commit message, the user commits.
- **Flow IDs** are stable: an ID is never reassigned, a removed flow leaves its number unused.
