#!/usr/bin/env bash
# SessionStart hook: when the session opens in a project with a detectable stack,
# inject a short reminder that the Checklist Qualité GM applies to dev tasks.
# Silent (no output) outside a project, so it never adds noise elsewhere.

dir="${CLAUDE_PROJECT_DIR:-$PWD}"

for manifest in composer.json package.json pyproject.toml manage.py requirements.txt wp-config.php wp-content; do
  if [ -e "$dir/$manifest" ]; then
    cat <<'MSG'
[Gingerminds] Ce projet est soumis à la Checklist Qualité GM (sécurité, SEO, RGPD, accessibilité, éco-conception, qualité).
Pour toute tâche de dev (feature, bug, refactor) :
- avant de coder : charge le skill /gm:quality (mode context) pour connaître les règles applicables à la tâche ;
- avant de committer : /gm:review, qui inclut le contrôle /gm:quality check sur le diff.
Questions, lectures de code ou tâches hors dev : ignore ce rappel.
MSG
    exit 0
  fi
done

exit 0
