#!/usr/bin/env bash
# Self-contained tests for scripts/mantis-note.sh (stub curl, no network).
# Usage: bash tests/mantis-note.test.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/mantis-note.sh"
STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT
mkdir -p "$STUB_DIR/bin"

# Stub curl: record args, copy --data-binary @file, write body to -o, print code.
cat > "$STUB_DIR/bin/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$STUB_DIR/args"
out=""
while (( $# )); do
  case "$1" in
    -o) out="$2"; shift ;;
    --data-binary) f="${2#@}"; cat "$f" > "$STUB_DIR/payload"; shift ;;
  esac
  shift
done
[[ -n "$out" ]] && printf '%s' "${STUB_BODY:-}" > "$out"
printf '%s' "${STUB_CODE:-200}"
STUB
chmod +x "$STUB_DIR/bin/curl"

fails=0
export STUB_DIR
export PATH="$STUB_DIR/bin:$PATH"

pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; fails=$((fails + 1)); }

# run_script <args...>: sets rc, out, err. Clean env unless caller exports.
run_script() {
  rm -f "$STUB_DIR/args" "$STUB_DIR/payload"
  rc=0
  out="$("$SCRIPT" "$@" 2>"$STUB_DIR/err")" || rc=$?
  err="$(cat "$STUB_DIR/err")"
}

test_missing_args_exits_2() {
  run_script
  [[ $rc -eq 2 ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "rc=$rc"
}

test_empty_note_exits_2() {
  local f="$STUB_DIR/empty.txt"; : > "$f"
  MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 1 "$f"
  if [[ $rc -ne 2 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ -e "$STUB_DIR/args" ]]; then fail "${FUNCNAME[0]}" "curl was called"
  else pass "${FUNCNAME[0]}"; fi
}

test_missing_env_exits_2() {
  local f="$STUB_DIR/n.txt"; printf 'hello' > "$f"
  env -u MANTIS_URL -u MANTIS_TOKEN "$SCRIPT" 1 "$f" >/dev/null 2>&1; rc=$?
  [[ $rc -eq 2 ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "rc=$rc"
}

test_dry_run_prints_private_payload_without_env() {
  local f="$STUB_DIR/n.txt"; printf 'hello' > "$f"
  rm -f "$STUB_DIR/args"
  rc=0
  out="$(env -u MANTIS_URL -u MANTIS_TOKEN "$SCRIPT" 1 "$f" --dry-run 2>"$STUB_DIR/err")" || rc=$?
  err="$(cat "$STUB_DIR/err")"
  if [[ $rc -ne 0 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ "$(jq -r .view_state.name <<<"$out")" != "private" ]]; then fail "${FUNCNAME[0]}" "not private"
  elif [[ "$(jq -r .text <<<"$out")" != "hello" ]]; then fail "${FUNCNAME[0]}" "text mismatch"
  elif [[ "$err" != *"DRY-RUN: nothing sent to Mantis (issue 1)"* ]]; then fail "${FUNCNAME[0]}" "stderr: $err"
  elif [[ -e "$STUB_DIR/args" ]]; then fail "${FUNCNAME[0]}" "curl was called"
  else pass "${FUNCNAME[0]}"; fi
}

test_posts_private_note() {
  local f="$STUB_DIR/n.txt"; printf 'hello' > "$f"
  STUB_CODE=201 STUB_BODY='{"note":{"id":42,"view_state":{"name":"private"}}}' MANTIS_URL=https://mantis.test/ MANTIS_TOKEN=tok run_script 123 "$f"
  local args; args="$(cat "$STUB_DIR/args" 2>/dev/null)"
  if [[ $rc -ne 0 ]]; then fail "${FUNCNAME[0]}" "rc=$rc err=$err"
  elif [[ "$out" != "Posted private note 42 on issue 123" ]]; then fail "${FUNCNAME[0]}" "stdout: $out"
  elif [[ "$args" != *"https://mantis.test/api/rest/issues/123/notes"* ]]; then fail "${FUNCNAME[0]}" "url"
  elif [[ "$args" != *"Authorization: tok"* ]]; then fail "${FUNCNAME[0]}" "auth header"
  elif [[ "$args" != *"-X"* || "$args" != *"POST"* ]]; then fail "${FUNCNAME[0]}" "-X POST"
  elif [[ "$(jq -r .view_state.name "$STUB_DIR/payload")" != "private" ]]; then fail "${FUNCNAME[0]}" "payload not private"
  else pass "${FUNCNAME[0]}"; fi
}

test_too_many_args_exits_2() {
  local f="$STUB_DIR/n.txt"; printf 'hello' > "$f"
  MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 1 "$f" --dry-run extra
  if [[ $rc -ne 2 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ -e "$STUB_DIR/args" ]]; then fail "${FUNCNAME[0]}" "curl was called"
  else pass "${FUNCNAME[0]}"; fi
}

test_non_private_response_exits_3() {
  local f="$STUB_DIR/n.txt"; printf 'hello' > "$f"
  STUB_CODE=201 STUB_BODY='{"note":{"id":1,"view_state":{"name":"public"}}}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 7 "$f"
  if [[ $rc -ne 3 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ "$err" != *"NOT stored as private"* ]]; then fail "${FUNCNAME[0]}" "stderr: $err"
  elif [[ "$err" != *"https://mantis.test/view.php?id=7#c1"* ]]; then fail "${FUNCNAME[0]}" "url missing: $err"
  else pass "${FUNCNAME[0]}"; fi
}

test_api_error_exits_3() {
  local f="$STUB_DIR/n.txt"; printf 'hello' > "$f"
  STUB_CODE=403 STUB_BODY='{"message":"Access denied"}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 1 "$f"
  if [[ $rc -ne 3 ]]; then fail "${FUNCNAME[0]}" "rc=$rc"
  elif [[ "$err" != *"HTTP 403"* ]]; then fail "${FUNCNAME[0]}" "stderr: $err"
  elif [[ "$err" != *"Access denied"* ]]; then fail "${FUNCNAME[0]}" "message missing: $err"
  else pass "${FUNCNAME[0]}"; fi
}

test_special_chars_preserved() {
  local f="$STUB_DIR/special.txt"
  printf '%s\n%s\n%s' 'Il a dit "ok" — à vérifier' '$HOME' 'C:\chemin' > "$f"
  STUB_CODE=201 STUB_BODY='{"note":{"id":1,"view_state":{"name":"private"}}}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 1 "$f"
  local got want; got="$(jq -j .text "$STUB_DIR/payload"; echo x)"; want="$(cat "$f"; echo x)"
  [[ $rc -eq 0 && "$got" == "$want" ]] && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "rc=$rc payload text differs"
}

test_leading_zeros_stripped() {
  local f="$STUB_DIR/n.txt"; printf 'hello' > "$f"
  STUB_CODE=201 STUB_BODY='{"note":{"id":1,"view_state":{"name":"private"}}}' MANTIS_URL=https://mantis.test MANTIS_TOKEN=tok run_script 0051234 "$f"
  grep -q '/issues/51234/notes' "$STUB_DIR/args" 2>/dev/null && pass "${FUNCNAME[0]}" || fail "${FUNCNAME[0]}" "id not stripped"
}

test_missing_args_exits_2
test_too_many_args_exits_2
test_non_private_response_exits_3
test_empty_note_exits_2
test_missing_env_exits_2
test_dry_run_prints_private_payload_without_env
test_posts_private_note
test_api_error_exits_3
test_special_chars_preserved
test_leading_zeros_stripped

exit $(( fails > 0 ? 1 : 0 ))
