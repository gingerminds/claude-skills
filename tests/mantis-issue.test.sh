#!/usr/bin/env bash
# Self-contained tests for scripts/mantis-issue.sh (stub curl, no network).
# Usage: bash tests/mantis-issue.test.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/mantis-issue.sh"
STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT
source "$ROOT/tests/lib/curl-stub.sh"

fails=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

run_script() {
  rm -f "$STUB_DIR/args"
  rc=0
  out="$("$SCRIPT" "$@" 2>"$STUB_DIR/err")" || rc=$?
  err="$(cat "$STUB_DIR/err")"
}

args_flat() { tr '\n' ' ' < "$STUB_DIR/args" 2>/dev/null; }

test_digest_curl_has_timeouts() {
  STUB_CODE=200 STUB_BODY='{"issues":[{"id":7,"summary":"T","notes":[]}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7
  local a; a="$(args_flat)"
  [[ "$a" == *"--connect-timeout 10 "* && "$a" == *"--max-time 60 "* ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "args: $a"
}

test_file_curl_has_timeouts() {
  STUB_CODE=200 STUB_BODY='{"files":[]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 3 "$STUB_DIR/dest"
  local a; a="$(args_flat)"
  [[ "$a" == *"--connect-timeout 10 "* && "$a" == *"--max-time 180 "* ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "args: $a"
}

test_api_error_message_surfaces() {
  STUB_CODE=404 STUB_BODY='{"message":"Issue not found"}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7
  if [[ $rc -ne 3 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ "$err" != *"Issue not found"* ]]; then fail "${FUNCNAME[0]}" "stderr: $err"
  else pass "${FUNCNAME[0]}"; fi
}

test_digest_prints_summary() {
  STUB_CODE=200 STUB_BODY='{"issues":[{"id":7,"summary":"Titre","description":"d","notes":[]}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7
  [[ $rc -eq 0 && "$out" == *"# Mantis #7 — Titre"* ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "rc=$rc out: $out"
}

test_digest_curl_has_timeouts
test_file_curl_has_timeouts
test_api_error_message_surfaces
test_digest_prints_summary

exit $(( fails > 0 ? 1 : 0 ))
