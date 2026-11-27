# Update mode

The project already has `docs/architecture/tools/architecture.json` (or the documentation folder found by `SKILL.md`; substitute it below). Run every command from the repository root. Bring the views and the matrix back in line with what the repositories and the servers show today. Work in the order below; nothing is changed before step 5, and step 5 only applies what the user chose.

## 1. Read the current state

- the config (`title`, `flow_prefixes`, `owners`, `repositories`, `tool_version`);
- the « État au » date of every view: `python3 docs/architecture/tools/architecture.py extract docs/architecture/views/<vue>.drawio.svg docs/architecture/build/<vue>.drawio`, then read the cartouche;
- the README matrix and known issues, and the facts log `build/faits.md` if it is still there.

Run `python3 docs/architecture/tools/architecture.py check` first: errors that predate this update are reported to the user as such.

## 2. List what changed in the repositories

Take the oldest « État au » date and run `git log --since=<AAAA-MM-JJ> --name-only` in the current repository and in each path of `repositories` (relative to the repository root):

```bash
git -C "<repository>" log --since=<AAAA-MM-JJ> --name-only --format='%h %ad %s' --date=short -- <infrastructure paths>
```

Infrastructure paths: compose files, Dockerfiles, the CI file, deploy scripts, proxy and web server configs, crontabs and scheduled jobs, `.env.example`, infrastructure docs. Read the diffs of the commits that touch them.

## 3. Collect the facts again on the servers

Same rules and commands as `references/collection.md`, read-only, through the configured aliases (ask which ones if they are not known for this session). Compare with the facts log and with the views.

## 4. Present the gaps

Group them by view, then by flow ID, each with its source:

- flow added (new ID to create), removed (its ID becomes unused), changed (port, encryption, authentication, owner, route);
- status changed: « à confirmer » now seen becomes `constaté (JJ/MM/AAAA)`; « décidé » now deployed becomes `constaté`;
- machine, container or service added, removed or moved;
- known issue resolved or new.

Then ask the user what to apply: all, some, or nothing.

## 5. Apply

- For each view to change: `extract` it to `build/`, edit the XML (same visual language as `references/drawio-style.md`), `render` it back with `--png`, read the PNG and fix the layout.
- Update the matrix (rows, statuses, dates), the known issues, the README sections, and the « État au » date of every view you changed.
- A flow ID is never reassigned (jamais réattribué): a new flow takes the next free number of its prefix, a removed flow leaves its number unused.
- Run `make diagrams` (or the `refresh` then `check` commands) and fix every `ERREUR`.
- Update `build/faits.md` with the new facts.

## 6. Tool upgrade

Compare the project tool with the plugin's:

```bash
python3 docs/architecture/tools/architecture.py version
python3 "$T/architecture.py" version
```

When the project's version is older, summarize what changes (show `diff -u` of the two files), and once the user agrees copy `architecture.py` and `dossier.css` from `$T`, set `tool_version` in the config, and run `check` again.

Finish like the create mode: summary, what is still « à confirmer », a proposed commit message (English, Conventional Commits), and the offer of `/gm:technical-architecture pdf`.
