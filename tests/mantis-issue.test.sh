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
  STUB_CODE=200 STUB_BODY='{"files":[]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 3 "$STUB_DIR"
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
# new_dest_dir: fresh empty directory under the stub dir (inside $TMPDIR), canonical path.
new_dest_dir() { local d; d="$(mktemp -d "$STUB_DIR/dest.XXXXXX")"; (cd "$d" && pwd -P); }

test_non_numeric_id_exits_2() {
  local d; d="$(new_dest_dir)"
  local bad
  for bad in "7abc" "../7" "7 --file" ""; do
    MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script "$bad"
    if [[ $rc -ne 2 ]]; then fail "${FUNCNAME[0]}" "issue id '$bad' rc=$rc"; return; fi
  done
  for bad in "3abc" "../3" "3/x" ""; do
    MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file "$bad" "$d"
    if [[ $rc -ne 2 ]]; then fail "${FUNCNAME[0]}" "file id '$bad' rc=$rc"; return; fi
  done
  MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script "x7" --file 3 "$d"
  if [[ $rc -ne 2 ]]; then fail "${FUNCNAME[0]}" "issue id in file mode rc=$rc"
  elif [[ -e "$STUB_DIR/args" ]]; then fail "${FUNCNAME[0]}" "curl was called"
  else pass "${FUNCNAME[0]}"; fi
}

test_leading_zeros_stripped() {
  STUB_CODE=200 STUB_BODY='{"issues":[{"id":7,"summary":"T","notes":[]}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 0007
  local a; a="$(args_flat)"
  [[ $rc -eq 0 && "$a" == *"https://mantis.test/api/rest/issues/7 "* ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "rc=$rc args: $a"
}

test_file_dest_outside_tmp_exits_2() {
  local before; before="$(ls -A "$ROOT/tests")"
  STUB_CODE=200 STUB_BODY='{"files":[{"id":3,"filename":"a.png","content":"aGVsbG8="}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 3 "$ROOT/tests"
  if [[ $rc -ne 2 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ -e "$STUB_DIR/args" ]]; then fail "${FUNCNAME[0]}" "curl was called"
  elif [[ "$(ls -A "$ROOT/tests")" != "$before" ]]; then fail "${FUNCNAME[0]}" "a file was written in $ROOT/tests"
  else pass "${FUNCNAME[0]}"; fi
}

test_file_dest_symlink_escape_exits_2() {
  local link="$STUB_DIR/escape"
  ln -s "$ROOT/tests" "$link"
  STUB_CODE=200 STUB_BODY='{"files":[{"id":3,"filename":"a.png","content":"aGVsbG8="}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 3 "$link"
  rm -f "$link"
  [[ $rc -eq 2 && ! -e "$STUB_DIR/args" ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "rc=$rc"
}

test_file_sanitises_name() {
  local d; d="$(new_dest_dir)"
  STUB_CODE=200 STUB_BODY='{"files":[{"id":3,"filename":"x$(id).png","content":"aGVsbG8="}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 3 "$d"
  local want="$d/3-x__id_.png"
  if [[ $rc -ne 0 ]]; then fail "${FUNCNAME[0]}" "rc=$rc err: $err"
  elif [[ "$out" != "Downloaded issue 7 file 3 -> $want" ]]; then fail "${FUNCNAME[0]}" "out: $out"
  elif [[ "$(cat "$want" 2>/dev/null)" != "hello" ]]; then fail "${FUNCNAME[0]}" "content mismatch"
  elif [[ "$(ls -A "$d" | wc -l | tr -d ' ')" != "1" ]]; then fail "${FUNCNAME[0]}" "unexpected files: $(ls -A "$d")"
  else pass "${FUNCNAME[0]}"; fi
}

test_file_strips_traversal() {
  local d; d="$(new_dest_dir)"
  STUB_CODE=200 STUB_BODY='{"files":[{"id":4,"filename":"../../evil.sh","content":"aGVsbG8="}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 4 "$d"
  local want="$d/4-__evil.sh"
  if [[ $rc -ne 0 ]]; then fail "${FUNCNAME[0]}" "rc=$rc err: $err"
  elif [[ "$out" != "Downloaded issue 7 file 4 -> $want" || ! -f "$want" ]]; then fail "${FUNCNAME[0]}" "out: $out"
  else pass "${FUNCNAME[0]}"; fi
}

test_file_empty_name_falls_back() {
  local d; d="$(new_dest_dir)"
  STUB_CODE=200 STUB_BODY='{"files":[{"id":5,"filename":"..","content":"aGVsbG8="}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 5 "$d"
  local want="$d/5.bin"
  [[ $rc -eq 0 && "$out" == "Downloaded issue 7 file 5 -> $want" && -f "$want" ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "rc=$rc out: $out"
}

test_file_refuses_overwrite() {
  local d; d="$(new_dest_dir)"
  printf 'old' > "$d/3-a.png"
  STUB_CODE=200 STUB_BODY='{"files":[{"id":3,"filename":"a.png","content":"aGVsbG8="}]}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 --file 3 "$d"
  if [[ $rc -ne 2 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ "$(cat "$d/3-a.png")" != "old" ]]; then fail "${FUNCNAME[0]}" "existing file overwritten"
  else pass "${FUNCNAME[0]}"; fi
}

test_digest_prints_summary
test_non_numeric_id_exits_2
test_leading_zeros_stripped
test_file_dest_outside_tmp_exits_2
test_file_dest_symlink_escape_exits_2
test_file_sanitises_name
test_file_strips_traversal
test_file_empty_name_falls_back
test_file_refuses_overwrite

exit $(( fails > 0 ? 1 : 0 ))
