#!/usr/bin/env bash
# Publish a PRIVATE note on a Mantis ticket (write counterpart of mantis-issue.sh).
#
# Usage:
#   mantis-note.sh <issue-id> <note-file> [--dry-run]
#
#   The note is always private: the body sent is
#   {"text": <note-file content>, "view_state": {"name": "private"}}.
#   --dry-run prints that JSON on stdout and sends nothing (no env needed).
#
# Env:     MANTIS_URL    base URL of the Mantis instance (e.g. https://mantis.example.com)
#          MANTIS_TOKEN  Mantis API token (My Account -> API Tokens)
#          (both required for a real send only, not for --dry-run)
#
# Exit codes:
#   0  note posted (HTTP 200/201), or dry-run done
#   2  bad usage / missing or empty note file / missing env (outside --dry-run)
#   3  HTTP / API error
set -euo pipefail

raw_id="${1:-}"
note_file="${2:-}"
dry_run=0
[[ "${3:-}" == "--dry-run" ]] && dry_run=1

if [[ -z "$raw_id" || -z "$note_file" || ( -n "${3:-}" && $dry_run -eq 0 ) ]]; then
  echo "usage: mantis-note.sh <issue-id> <note-file> [--dry-run]" >&2
  exit 2
fi

if [[ ! "$raw_id" =~ ^[0-9]+$ ]]; then
  echo "issue-id must be numeric, got: ${raw_id}" >&2
  exit 2
fi
id=$((10#$raw_id))

if [[ ! -r "$note_file" ]]; then
  echo "note file not readable: ${note_file}" >&2
  exit 2
fi

payload="$(mktemp)"
body="$(mktemp)"
trap 'rm -f "$payload" "$body"' EXIT

# Read the note once; the text never goes through shell interpolation.
jq -n --rawfile text "$note_file" '{text: $text, view_state: {name: "private"}}' > "$payload"

if ! jq -e '.text | length > 0' "$payload" >/dev/null; then
  echo "note file is empty: ${note_file}" >&2
  exit 2
fi

if [[ $dry_run -eq 1 ]]; then
  cat "$payload"
  echo "DRY-RUN: nothing sent to Mantis" >&2
  exit 0
fi

if [[ -z "${MANTIS_URL:-}" || -z "${MANTIS_TOKEN:-}" ]]; then
  echo "MANTIS_URL and MANTIS_TOKEN must be set (see skill setup)." >&2
  exit 2
fi

base="${MANTIS_URL%/}"

code="$(curl -sS -X POST -o "$body" -w '%{http_code}' \
  -H "Authorization: ${MANTIS_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  --data-binary @"$payload" \
  "${base}/api/rest/issues/${id}/notes")" || { echo "curl failed reaching ${base}" >&2; exit 3; }

if [[ "$code" != "200" && "$code" != "201" ]]; then
  echo "Mantis API returned HTTP ${code} for issue ${id}." >&2
  jq -r '.message // empty' "$body" >&2 2>/dev/null || true
  exit 3
fi

note_id="$(jq -r '.note.id // "?"' "$body" 2>/dev/null || echo '?')"
echo "Posted private note ${note_id} on issue ${id}"
