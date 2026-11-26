#!/usr/bin/env bash
# Fetch a Mantis ticket and print a compact digest for review.
#
# Usage:
#   mantis-issue.sh <issue-id>                         print the digest (incl. an attachment list, if any)
#   mantis-issue.sh <issue-id> --file <fid> <dest-dir> download one attachment into <dest-dir>
#
#   <issue-id> and <fid> are digits only (leading zeros stripped).
#   --file names the file itself: <dest-dir>/<fid>-<sanitised original filename>
#   (every char outside [A-Za-z0-9._-] -> "_", ".." removed; empty name -> <fid>.bin).
#   <dest-dir> must be an existing directory under a temp root ($TMPDIR, /tmp,
#   /private/tmp — the Claude scratchpad lives there). A file is never overwritten:
#   if <path> is already a regular file (same attachment fetched earlier in the
#   session), it is reused as is. On success stdout is:
#   Downloaded issue <id> file <fid> -> <path>
#
# Env:     MANTIS_URL    base URL of the Mantis instance (e.g. https://mantis.example.com)
#          MANTIS_TOKEN  Mantis API token (My Account -> API Tokens)
#
# Timeouts: curl uses --connect-timeout 10; --max-time 60 (digest), 180 (--file download).
#
# Exit codes:
#   0  ticket printed / file downloaded
#   2  missing env / bad usage (non-numeric id, dest-dir outside a temp root,
#      target is a symlink or not a regular file) -> skill should fall back to "paste the ticket"
#   3  HTTP / API error, or a 200 that is not a JSON object -> skill should fall back to "paste the ticket"
set -euo pipefail

usage() {
  echo "usage: mantis-issue.sh <issue-id> [--file <file-id> <dest-dir>]" >&2
  exit 2
}

raw_id="${1:-}"
[[ -z "$raw_id" ]] && usage
if [[ ! "$raw_id" =~ ^[0-9]+$ ]]; then
  echo "issue-id must be numeric, got: ${raw_id}" >&2
  exit 2
fi
id=$((10#$raw_id))

mode="digest"
if [[ "${2:-}" == "--file" ]]; then
  mode="file"
  (( $# == 4 )) || usage
  raw_fid="$3"
  dest_dir="$4"
  if [[ ! "$raw_fid" =~ ^[0-9]+$ ]]; then
    echo "file-id must be numeric, got: ${raw_fid}" >&2
    exit 2
  fi
  file_id=$((10#$raw_fid))
  if [[ ! -d "$dest_dir" ]]; then
    echo "dest-dir is not an existing directory: ${dest_dir}" >&2
    exit 2
  fi
  # Canonical (symlink-free) paths, so a link cannot smuggle the file out of the temp root.
  dest_dir="$(cd "$dest_dir" && pwd -P)"
  inside_tmp=0
  for root in "${TMPDIR:-}" /tmp /private/tmp; do
    [[ -n "$root" && -d "$root" ]] || continue
    root="$(cd "$root" && pwd -P)"
    [[ "$root" != "/" ]] || continue
    if [[ "$dest_dir" == "$root" || "$dest_dir" == "$root"/* ]]; then inside_tmp=1; break; fi
  done
  if (( ! inside_tmp )); then
    echo "dest-dir must be under a temp root (\$TMPDIR, /tmp, /private/tmp), got: ${dest_dir}" >&2
    exit 2
  fi
elif (( $# != 1 )); then
  usage
fi

if [[ -z "${MANTIS_URL:-}" || -z "${MANTIS_TOKEN:-}" ]]; then
  echo "MANTIS_URL and MANTIS_TOKEN must be set (see skill setup)." >&2
  exit 2
fi

base="${MANTIS_URL%/}"

# A 200 that is not a JSON object (empty body, SSO login page, wrong MANTIS_URL) is an
# API error, not an empty ticket nor a jq crash.
require_json() {
  jq -e 'type == "object"' "$1" >/dev/null 2>&1 && return
  echo "Mantis returned HTTP 200 without a JSON object from ${base}: check MANTIS_URL." >&2
  exit 3
}

# --- Download mode: fetch a single attachment into <dest-dir> ----------------
if [[ "$mode" == "file" ]]; then
  fbody="$(mktemp)"
  trap 'rm -f "$fbody"' EXIT
  code="$(curl -sS --connect-timeout 10 --max-time 180 -o "$fbody" -w '%{http_code}' \
    -H "Authorization: ${MANTIS_TOKEN}" \
    -H "Accept: application/json" \
    "${base}/api/rest/issues/${id}/files/${file_id}")" \
    || { echo "curl failed reaching ${base}" >&2; exit 3; }

  if [[ "$code" != "200" ]]; then
    echo "Mantis API returned HTTP ${code} for issue ${id} file ${file_id}." >&2
    jq -r '.message // empty' "$fbody" >&2 2>/dev/null || true
    exit 3
  fi
  require_json "$fbody"

  # The single-file endpoint wraps the attachment in .files[0]; content is base64.
  # A missing file id still returns HTTP 200 with {"files":[]}, so check for content.
  content="$(jq -r '.files[0].content // .content // empty' "$fbody")"
  if [[ -z "$content" ]]; then
    echo "No attachment ${file_id} on issue ${id} (empty API response)." >&2
    exit 3
  fi

  # The client-chosen filename is sanitised in jq (never through the shell).
  safe_name="$(jq -r '(.files[0].filename // .filename // "") | tostring
    | gsub("[^A-Za-z0-9._-]"; "_") | gsub("[.]{2,}"; "")' "$fbody")"
  if [[ -z "$safe_name" ]]; then
    dest="${dest_dir}/${file_id}.bin"
  else
    dest="${dest_dir}/${file_id}-${safe_name}"
  fi

  # Never overwrite. A regular file there is the same attachment, fetched earlier in
  # the session (or by a parallel run): reuse it. Anything else (symlink, dir) is refused.
  reuse_or_refuse() {
    if [[ -f "$dest" && ! -L "$dest" ]]; then
      echo "Downloaded issue ${id} file ${file_id} -> ${dest}"
      exit 0
    fi
    echo "Refusing to overwrite existing file: ${dest}" >&2
    exit 2
  }
  if [[ -e "$dest" || -L "$dest" ]]; then reuse_or_refuse; fi

  # Decode next to the target, then publish it with a hard link: atomic and never
  # clobbering, so the target only ever appears complete.
  part="$(mktemp "${dest_dir}/.part.XXXXXX")"
  trap 'rm -f "$fbody" "$part"' EXIT
  if ! printf '%s' "$content" | base64 -d > "$part" 2>/dev/null; then
    echo "Could not write attachment ${file_id} to ${dest} (decode error)." >&2
    exit 3
  fi
  ln "$part" "$dest" 2>/dev/null || reuse_or_refuse
  echo "Downloaded issue ${id} file ${file_id} -> ${dest}"
  exit 0
fi

# --- Digest mode -------------------------------------------------------------
body="$(mktemp)"
trap 'rm -f "$body"' EXIT

code="$(curl -sS --connect-timeout 10 --max-time 60 -o "$body" -w '%{http_code}' \
  -H "Authorization: ${MANTIS_TOKEN}" \
  -H "Accept: application/json" \
  "${base}/api/rest/issues/${id}")" || { echo "curl failed reaching ${base}" >&2; exit 3; }

if [[ "$code" != "200" ]]; then
  echo "Mantis API returned HTTP ${code} for issue ${id}." >&2
  jq -r '.message // empty' "$body" >&2 2>/dev/null || true
  exit 3
fi
require_json "$body"

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
    # Attachments live at issue level AND on individual notes; collect both.
    ( ([ $i.attachments[]? ] + [ $i.notes[]? | .attachments[]? ]) as $atts
      | if ($atts | length) > 0
        then "\n## Attachments\nDownload with: mantis-issue.sh \($i.id) --file <file-id> <dest-dir>\n"
             + ([ $atts[]
                  | "- [\(.id)] \(.filename) (\((.content_type // "?") | split(";")[0]), \(.size // 0) bytes)" ]
                | join("\n"))
        else empty end)
' "$body"
