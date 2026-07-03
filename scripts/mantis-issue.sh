#!/usr/bin/env bash
# Fetch a Mantis ticket and print a compact digest for review.
#
# Usage:
#   mantis-issue.sh <issue-id>                     print the digest (incl. an attachment list, if any)
#   mantis-issue.sh <issue-id> --file <fid> <dest> download one attachment to <dest>
#
# Env:     MANTIS_URL    base URL of the Mantis instance (e.g. https://mantis.example.com)
#          MANTIS_TOKEN  Mantis API token (My Account -> API Tokens)
#
# Exit codes:
#   0  ticket printed / file downloaded
#   2  missing env / bad usage   -> skill should fall back to "paste the ticket"
#   3  HTTP / API error          -> skill should fall back to "paste the ticket"
set -euo pipefail

id="${1:-}"
if [[ -z "$id" ]]; then
  echo "usage: mantis-issue.sh <issue-id> [--file <file-id> <dest>]" >&2
  exit 2
fi

if [[ -z "${MANTIS_URL:-}" || -z "${MANTIS_TOKEN:-}" ]]; then
  echo "MANTIS_URL and MANTIS_TOKEN must be set (see skill setup)." >&2
  exit 2
fi

base="${MANTIS_URL%/}"

# --- Download mode: fetch a single attachment and write it to <dest> ---------
if [[ "${2:-}" == "--file" ]]; then
  file_id="${3:-}"
  dest="${4:-}"
  if [[ -z "$file_id" || -z "$dest" ]]; then
    echo "usage: mantis-issue.sh <issue-id> --file <file-id> <dest>" >&2
    exit 2
  fi

  fbody="$(mktemp)"
  trap 'rm -f "$fbody"' EXIT
  code="$(curl -sS -o "$fbody" -w '%{http_code}' \
    -H "Authorization: ${MANTIS_TOKEN}" \
    -H "Accept: application/json" \
    "${base}/api/rest/issues/${id}/files/${file_id}")" \
    || { echo "curl failed reaching ${base}" >&2; exit 3; }

  if [[ "$code" != "200" ]]; then
    echo "Mantis API returned HTTP ${code} for issue ${id} file ${file_id}." >&2
    jq -r '.message // empty' "$fbody" 2>/dev/null >&2 || true
    exit 3
  fi

  # The single-file endpoint wraps the attachment in .files[0]; content is base64.
  # A missing file id still returns HTTP 200 with {"files":[]}, so check for content.
  content="$(jq -r '.files[0].content // .content // empty' "$fbody")"
  if [[ -z "$content" ]]; then
    echo "No attachment ${file_id} on issue ${id} (empty API response)." >&2
    exit 3
  fi
  if ! printf '%s' "$content" | base64 -d > "$dest" 2>/dev/null; then
    echo "Could not write attachment ${file_id} to ${dest} (bad path or decode error)." >&2
    rm -f "$dest"
    exit 3
  fi
  echo "Downloaded issue ${id} file ${file_id} -> ${dest}"
  exit 0
fi

# --- Digest mode -------------------------------------------------------------
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
       else empty end),
    (if (($i.attachments // []) | length) > 0
       then "\n## Attachments\nDownload with: mantis-issue.sh \($i.id) --file <file-id> <dest>\n"
            + ([ $i.attachments[]
                 | "- [\(.id)] \(.filename) (\((.content_type // "?") | split(";")[0]), \(.size // 0) bytes)" ]
               | join("\n"))
       else empty end)
' "$body"
