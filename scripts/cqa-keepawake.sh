#!/usr/bin/env bash
# Best-effort sleep inhibitor for the duration of the checklist audit.
#
#   cqa-keepawake.sh start <run_dir>
#   cqa-keepawake.sh stop <run_dir>
#
# "start" runs systemd-inhibit (Linux) or caffeinate (macOS) in its own process group and
# records "<pid> <inhibitor>" in <run_dir>/keepawake.pid. "stop" kills that group, only if
# the pid still is the inhibitor that "start" launched.
# Exit codes: 0 success (also without any inhibitor: only a warning), 2 usage.
set -uo pipefail
unset CDPATH

say() {
  printf '%s\n' "$*" >&2
}

usage() {
  say "Usage : cqa-keepawake.sh start|stop <run_dir>"
  exit 2
}

# Prints "pid tool" when the pid file names a live inhibitor that is still the leader of its
# own process group; prints nothing otherwise.
live_inhibitor() {
  local file="$1" pid tool command pgid
  [ -f "$file" ] || return 0
  read -r pid tool < "$file" || true
  case "$pid" in
    '' | *[!0-9]*) return 0 ;;
  esac
  [ -n "$tool" ] || return 0
  command="$(ps -o command= -p "$pid" 2>/dev/null)" || return 0
  case "$command" in
    *"$tool"*) ;;
    *) return 0 ;;
  esac
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null)" || return 0
  pgid="${pgid//[[:space:]]/}"
  [ "$pgid" = "$pid" ] || return 0
  printf '%s %s\n' "$pid" "$tool"
}

launch() {
  if command -v setsid > /dev/null 2>&1; then
    setsid "$@" < /dev/null > /dev/null 2>&1 &
  else
    set -m
    "$@" < /dev/null > /dev/null 2>&1 &
    set +m
  fi
  LAUNCHED_PID=$!
}

warn_unprotected() {
  say "Avertissement : la mise en veille n'est pas empêchée (aucun outil disponible ou outil inopérant sur ce poste : systemd-inhibit, caffeinate). Gardez le poste éveillé vous-même pendant l'audit."
}

# True when the pid is a running process (a zombie counts as dead).
is_running() {
  local state
  state="$(ps -o stat= -p "$1" 2>/dev/null)" || return 1
  state="${state//[[:space:]]/}"
  case "$state" in
    '' | Z*) return 1 ;;
  esac
  return 0
}

start() {
  local run_dir="$1" file tool
  file="$run_dir/keepawake.pid"
  if [ -n "$(live_inhibitor "$file")" ]; then
    return 0
  fi
  rm -f "$file"
  if command -v systemd-inhibit > /dev/null 2>&1; then
    tool="systemd-inhibit"
    launch systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block \
      --who=gm:cqa "--why=Audit qualité en cours : le poste ne doit pas se mettre en veille" \
      sleep infinity
  elif command -v caffeinate > /dev/null 2>&1; then
    tool="caffeinate"
    launch caffeinate -dims
  else
    warn_unprotected
    return 0
  fi
  sleep 0.3
  if ! is_running "$LAUNCHED_PID"; then
    warn_unprotected
    return 0
  fi
  printf '%s %s\n' "$LAUNCHED_PID" "$tool" > "$file"
}

stop() {
  local file="$1/keepawake.pid" found pid
  found="$(live_inhibitor "$file")"
  if [ -n "$found" ]; then
    pid="${found%% *}"
    kill -TERM -- "-$pid" 2>/dev/null || true
    sleep 0.2
    kill -KILL -- "-$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

main() {
  [ "$#" -eq 2 ] || usage
  case "$1" in
    start) start "$2" ;;
    stop) stop "$2" ;;
    *) usage ;;
  esac
}

main "$@"
