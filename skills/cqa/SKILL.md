---
name: cqa
description: Alias of /gm:checklist-quality-audit (PRE-MEL quality checklist audit).
argument-hint: "[diagnostic | assign] [--phase \"MEL J0\"] [--dry-run]"
disable-model-invocation: true
---

Read `${CLAUDE_SKILL_DIR}/../checklist-quality-audit/SKILL.md` and follow it exactly, with these arguments: $ARGUMENTS.

Resolve every relative path from that skill's directory: read its references in `${CLAUDE_SKILL_DIR}/../checklist-quality-audit/references/`. Wherever it writes `${CLAUDE_SKILL_DIR}`, use the folder of this alias, which has the same depth: `S="${CLAUDE_SKILL_DIR}/../../scripts"` still points to the scripts of the plugin.
