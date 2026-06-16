#!/usr/bin/env bash
# Fetch a Mantis ticket and print a compact digest for review.
#
# Usage:   mantis-issue.sh <issue-id>
# Env:     MANTIS_URL    base URL of the Mantis instance (e.g. https://mantis.example.com)
#          MANTIS_TOKEN  Mantis API token (My Account -> API Tokens)
#
# Exit codes:
#   0  ticket printed
#   2  missing env / bad usage   -> skill should fall back to "paste the ticket"
#   3  HTTP / API error          -> skill should fall back to "paste the ticket"
set -euo pipefail

id="${1:-}"
if [[ -z "$id" ]]; then
  echo "usage: mantis-issue.sh <issue-id>" >&2
  exit 2
fi

if [[ -z "${MANTIS_URL:-}" || -z "${MANTIS_TOKEN:-}" ]]; then
  echo "MANTIS_URL and MANTIS_TOKEN must be set (see skill setup)." >&2
  exit 2
fi

base="${MANTIS_URL%/}"
body="$(mktemp)"
trap 'rm -f "$body"' EXIT

code="$(curl -sS -o "$body" -w '%{http_code}' \
  -H "Authorization: ${MANTIS_TOKEN}" \
  -H "Accept: application/json" \
  "${base}/api/rest/issues/${id}")" || { echo "curl failed reaching ${base}" >&2; exit 3; }

if [[ "$code" != "200" ]]; then
  echo "Mantis API returned HTTP ${code} for issue ${id}." >&2
  jq -r '.message // empty' "$body" 2>/dev/null >&2 || true
  exit 3
fi

jq -r '
  .issues[0] as $i
  | "# Mantis #\($i.id) — \($i.summary)",
    "Status: \($i.status.name // "?")   Priority: \($i.priority.name // "?")   Severity: \($i.severity.name // "?")",
    "Project: \($i.project.name // "?")   Category: \($i.category.name // "?")",
    "Reporter: \($i.reporter.name // "?")   Handler: \($i.handler.name // "unassigned")",
    "",
    "## Description",
    ($i.description // "(none)"),
    "",
    (if ($i.steps_to_reproduce // "") != "" then "## Steps to reproduce\n\($i.steps_to_reproduce)\n" else empty end),
    (if ($i.additional_information // "") != "" then "## Additional information\n\($i.additional_information)\n" else empty end),
    (if (($i.notes // []) | length) > 0
       then "## Notes\n" + ([ $i.notes[] | "- [\(.reporter.name // "?")] \(.text)" ] | join("\n"))
       else empty end)
' "$body"
