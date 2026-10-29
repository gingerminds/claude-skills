# Evals de déclenchement des skills gm

Suite de 16 cas (`trigger-*` : le skill doit se déclencher ; `negative-*` : il ne doit pas).
Chaque cas = `prompt.md` (prompt en français) + `graders/<skill>.md` (grader `tool_used` sur l'outil `Skill`).

Lancement (via `bash -c`, le `claude` de zsh est encapsulé) :

    bash -c 'claude plugin eval . --runs 1 --ablation none --model sonnet --trust-plugin --no-publish'

Un seul cas : ajouter `--case trigger-ticket`. Export : `--json evals/results/<nom>.json`.

- `evals/results/` n'est pas versionné (`.gitignore`).
- Seuls `Read`, `Glob`, `Grep` et `Skill` sont autorisés ; Bash/WebFetch ne sont pas accordés,
  donc aucun appel Mantis ou GitLab n'est effectué.
- `--ablation none` : les graders `tool_used` comptent dans le score.
