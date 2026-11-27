#!/usr/bin/env bash
# Shared stub curl for the Mantis script tests. Source it AFTER creating $STUB_DIR.
# Records args to $STUB_DIR/args, copies `--data-binary @file` to $STUB_DIR/payload,
# writes $STUB_BODY to the -o file and prints $STUB_CODE (default 200).
mkdir -p "$STUB_DIR/bin"
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
export STUB_DIR
export PATH="$STUB_DIR/bin:$PATH"
