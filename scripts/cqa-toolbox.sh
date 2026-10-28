#!/usr/bin/env bash
# Launcher of the Docker toolbox of the checklist audit (plugin side of the contract v1).
#
#   cqa-toolbox.sh doctor
#   cqa-toolbox.sh pull
#   cqa-toolbox.sh run <run_dir> <tool> [--followup]
#   cqa-toolbox.sh all <run_dir>
#
# Exit codes: 0 success, 1 tool failure, 2 usage or environment; "run" passes through the
# exit code of the tool container. Secret values never appear on a command line: they are
# forwarded to containers by name only (--env NAME).
set -euo pipefail
# A set CDPATH makes "cd" print the directory, or pick another one, inside $(...).
unset CDPATH

S="$(cd "$(dirname "$0")" && pwd)"

DEFAULT_REGISTRY="registry.gitlab.gingerminds.fr/gm/tools/checklist-qualite-outillage-audit"
IMAGES=(agence nu testssl gitleaks redocly schemathesis)
ALL_TOOLS=(http gitleaks assets stylelint deps lighthouse lighthouse-desktop a11y viewport nu yellowlab yellowlab-desktop testssl redocly schemathesis crawl)
NEEDS_1_2_REASON="boîte à outils 1.2 requise : lancez cqa-toolbox.sh pull"
# Version gate: "unset" until read, then 0 or 1 (once per launcher call).
AGENCE_1_2="unset"
# Schemathesis probes undeclared methods (TRACE, PUT, ...) despite --include-method, and
# --exclude-checks only hides their verdict: this filter is what stops the requests.
UNSAFE_METHODS_REGEX='^(?!(GET|HEAD|OPTIONS)$)'

RUN_DIR=""
RUN_ID=""
UID_GID=""
declare -a REPO_NAMES
declare -a REPO_PATHS
declare -a REPO_MOUNTS
REPO_NAMES=()
REPO_PATHS=()
REPO_MOUNTS=()
SPECS=""

say() {
  printf '%s\n' "$*" >&2
}

die() {
  say "$1"
  exit 2
}

usage() {
  say "Usage :"
  say "  cqa-toolbox.sh doctor"
  say "  cqa-toolbox.sh pull"
  say "  cqa-toolbox.sh run <dossier_du_run> <outil> [--followup]"
  say "  cqa-toolbox.sh all <dossier_du_run>"
  say "Outils : ${ALL_TOOLS[*]}"
  exit 2
}

image_ref() {
  local registry="${GM_CQA_REGISTRY:-$DEFAULT_REGISTRY}"
  printf '%s/%s:%s' "${registry%/}" "$1" "${GM_CQA_TOOLBOX_TAG:-1}"
}

require_docker() {
  command -v docker >/dev/null 2>&1 \
    || die "Docker est introuvable : installez Docker Desktop (macOS) ou Docker Engine (Linux)."
}

is_tool() {
  local tool
  for tool in "${ALL_TOOLS[@]}"; do
    if [ "$tool" = "$1" ]; then
      return 0
    fi
  done
  return 1
}

load_run() {
  local dir="$1" env_lines repos_tsv key value name path
  [ -d "$dir" ] || die "Le dossier du run est introuvable : $dir"
  RUN_DIR="$(cd "$dir" && pwd)"
  RUN_ID="$(basename "$RUN_DIR")"
  case "$RUN_ID" in
    "" | *[!A-Za-z0-9_.-]*) die "Le nom du dossier du run « $RUN_ID » ne convient pas à Docker (lettres, chiffres, point, tiret, souligné)." ;;
  esac
  case "$RUN_DIR" in
    *:*) die "Le chemin du run ne doit pas contenir « : » (montage Docker) : $RUN_DIR" ;;
  esac
  [ -f "$RUN_DIR/context.json" ] || die "context.json est introuvable dans $RUN_DIR."
  UID_GID="$(id -u):$(id -g)"

  env_lines="$(python3 "$S/cqa_plan.py" toolbox-env "$RUN_DIR")" || exit 2
  while IFS='=' read -r key value; do
    case "$key" in
      GM_AUDIT_TARGET | GM_AUDIT_ROBOTS | GM_AUDIT_RATE | GM_AUDIT_USER_AGENT | GM_AUDIT_PROD_ORIGIN)
        export "$key=$value"
        ;;
      "") ;;
      *) die "toolbox-env a renvoyé une variable inattendue : $key" ;;
    esac
  done <<<"$env_lines"

  repos_tsv="$(python3 "$S/cqa_plan.py" repos-tsv "$RUN_DIR")" || exit 2
  REPO_NAMES=()
  REPO_PATHS=()
  REPO_MOUNTS=()
  while IFS=$'\t' read -r name path; do
    [ -n "$name" ] || continue
    case "$path" in
      /*) ;;
      *) die "Le chemin du repo « $name » doit être absolu : $path" ;;
    esac
    case "$path" in
      *:*) die "Le chemin du repo « $name » ne doit pas contenir « : » (montage Docker) : $path" ;;
    esac
    [ -d "$path" ] || die "Le repo « $name » est introuvable : $path"
    REPO_NAMES+=("$name")
    REPO_PATHS+=("$path")
    REPO_MOUNTS+=(-v "$path:/repos/$name:ro")
  done <<<"$repos_tsv"
}

# Wipe the previous output of a tool so that stale files never reach the summary, and create
# raw/ as the current user (a missing bind mount source would be created by Docker as root).
prepare_tool() {
  local dir="$RUN_DIR/tools/$1"
  rm -rf -- "$dir" || return 1
  mkdir -p -- "$dir/raw" || return 1
}

skip_tool() {
  python3 "$S/cqa_plan.py" skip "$RUN_DIR" "$1" "$2" >/dev/null || return 1
  say "$1 : ignoré ($2)."
}

na_tool() {
  python3 "$S/cqa_plan.py" skip "$RUN_DIR" "$1" "$2" --not-applicable >/dev/null || return 1
  say "$1 : non applicable ($2)."
}

# docker_tool <image> <env set> [docker options ...] -- [image arguments ...]
docker_tool() {
  local image="$1" envset="$2" env_name
  local -a opts names envargs
  shift 2
  opts=()
  envargs=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    opts+=("$1")
    shift
  done
  if [ "$#" -gt 0 ]; then
    shift
  fi
  case "$envset" in
    web) names=(GM_AUDIT_TARGET GM_AUDIT_RATE GM_AUDIT_USER_AGENT GM_AUDIT_ROBOTS GM_AUDIT_AUTH_USER GM_AUDIT_AUTH_PASS) ;;
    crawl) names=(GM_AUDIT_TARGET GM_AUDIT_ROBOTS GM_AUDIT_RATE GM_AUDIT_USER_AGENT GM_AUDIT_AUTH_USER GM_AUDIT_AUTH_PASS GM_AUDIT_PROD_ORIGIN) ;;
    *) names=() ;;
  esac
  for env_name in ${names[@]+"${names[@]}"}; do
    if [ -n "${!env_name:-}" ]; then
      envargs+=(--env "$env_name")
    fi
  done
  docker run --rm --user "$UID_GID" --env HOME=/tmp --tmpfs /tmp \
    -v "$RUN_DIR:/in:ro" -v "$RUN_DIR/tools:/out" \
    ${REPO_MOUNTS[@]+"${REPO_MOUNTS[@]}"} \
    ${envargs[@]+"${envargs[@]}"} \
    ${opts[@]+"${opts[@]}"} \
    "$(image_ref "$image")" "$@" </dev/null
}

summarize() {
  docker_tool agence none -- summarize "$1" --raw "/out/$1/raw"
}

# Run a command whose exit code only reports findings (or is not a reliable failure signal):
# the summary of the raw output decides whether the tool failed.
tolerate() {
  local label="$1" rc=0
  shift
  "$@" || rc=$?
  if [ "$rc" -ne 0 ]; then
    say "$label : code de sortie $rc, la synthèse des résultats bruts tranche."
  fi
  return 0
}

list_specs() {
  SPECS="$(python3 "$S/cqa_plan.py" openapi-specs "$RUN_DIR")" || return 1
}

tool_http() {
  if [ "$1" -eq 1 ]; then
    prepare_tool http-followup || return 1
    if [ ! -f "$RUN_DIR/probes-followup.json" ]; then
      say "http : probes-followup.json est introuvable, lancez d'abord cqa_plan.py probes --followup."
      return 2
    fi
    docker_tool agence web -- http --probes /in/probes-followup.json --name http-followup
  else
    prepare_tool http || return 1
    if [ ! -f "$RUN_DIR/probes.json" ]; then
      say "http : probes.json est introuvable, lancez d'abord cqa_plan.py probes."
      return 2
    fi
    docker_tool agence web -- http --probes /in/probes.json
  fi
}

tool_pages() {
  prepare_tool "$1" || return 1
  docker_tool agence web -- "$1" --pages /in/context.json
}

# True when the local agence image is toolbox 1.2.0 or later (keep in sync with agence_1_2 in doctor).
agence_supports_1_2() {
  local label
  if [ "$AGENCE_1_2" = "unset" ]; then
    AGENCE_1_2=0
    if label="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$(image_ref agence)" 2>/dev/null </dev/null)"; then
      if printf '%s' "$label" | grep -Eq '^([2-9]|[1-9][0-9]+)\.[0-9]+\.[0-9]+$|^1\.([2-9]|[1-9][0-9]+)\.[0-9]+$'; then
        AGENCE_1_2=1
      fi
    fi
  fi
  [ "$AGENCE_1_2" = "1" ]
}

# tool_desktop <name> <subcommand> <flag> <value>
tool_desktop() {
  if ! agence_supports_1_2; then
    skip_tool "$1" "$NEEDS_1_2_REASON"
    return
  fi
  prepare_tool "$1" || return 1
  docker_tool agence web -- "$2" --pages /in/context.json "$3" "$4" --name "$1"
}

tool_viewport() {
  if ! agence_supports_1_2; then
    skip_tool viewport "$NEEDS_1_2_REASON"
    return
  fi
  prepare_tool viewport || return 1
  docker_tool agence web -- viewport --pages /in/context.json
}

tool_repos_1_2() {
  if ! agence_supports_1_2; then
    skip_tool "$1" "$NEEDS_1_2_REASON"
    return
  fi
  prepare_tool "$1" || return 1
  docker_tool agence none -- "$1" --repos /repos --context /in/context.json
}

tool_assets() {
  prepare_tool assets || return 1
  docker_tool agence none -- assets --repos /repos
}

tool_nu() {
  prepare_tool nu || return 1
  (
    # Unique names: the EXIT trap may fire while another function's locals are in scope.
    NU_NETWORK="cqa-$RUN_ID"
    NU_CONTAINER="cqa-nu-$RUN_ID"
    trap 'docker rm -f "$NU_CONTAINER" >/dev/null 2>&1 || true; docker network rm "$NU_NETWORK" >/dev/null 2>&1 || true' EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    # Leftovers of a killed run would make "network create" fail on a resume.
    docker rm -f "$NU_CONTAINER" >/dev/null 2>&1 </dev/null || true
    docker network rm "$NU_NETWORK" >/dev/null 2>&1 </dev/null || true
    docker network create "$NU_NETWORK" >/dev/null </dev/null \
      || { say "nu : impossible de créer le réseau Docker $NU_NETWORK."; exit 1; }
    docker run -d --rm --name "$NU_CONTAINER" --network "$NU_NETWORK" "$(image_ref nu)" >/dev/null </dev/null \
      || { say "nu : impossible de démarrer le service de validation HTML."; exit 1; }
    docker_tool agence web --network "$NU_NETWORK" --env "GM_AUDIT_NU_URL=http://$NU_CONTAINER:8888/" \
      -- nu --pages /in/context.json
  )
}

tool_testssl() {
  local url="${GM_AUDIT_TARGET:-}" host
  prepare_tool testssl || return 1
  case "$url" in
    https://*) ;;
    http://*)
      skip_tool testssl "cible en HTTP : aucun TLS à tester"
      return
      ;;
    *)
      say "testssl : URL de la cible absente ou invalide."
      return 1
      ;;
  esac
  host="${url#https://}"
  host="${host%%/*}"
  case "$host" in
    "" | -*)
      say "testssl : hôte de la cible invalide."
      return 1
      ;;
  esac
  tolerate testssl docker_tool testssl none -- \
    --quiet --color 0 --jsonfile /out/testssl/raw/testssl.json "$host"
  summarize testssl
}

tool_gitleaks() {
  local name mode i=0 failed=0
  prepare_tool gitleaks || return 1
  for name in ${REPO_NAMES[@]+"${REPO_NAMES[@]}"}; do
    # "gitleaks git" reports a clean [] (exit 0) on a folder without .git.
    if [ -e "${REPO_PATHS[$i]}/.git" ]; then
      mode=git
      say "gitleaks : repo « $name » analysé avec son historique git."
    else
      mode=dir
      say "gitleaks : repo « $name » sans .git, analyse des fichiers du dossier (pas de l'historique)."
    fi
    i=$((i + 1))
    if ! docker_tool gitleaks none -- "$mode" "/repos/$name" --redact=100 \
      --report-format json --report-path "/out/gitleaks/raw/$name.json" --exit-code 0; then
      say "gitleaks : l'analyse du repo « $name » a échoué."
      failed=1
    fi
  done
  if [ "$failed" -ne 0 ]; then
    return 1
  fi
  summarize gitleaks
}

tool_redocly() {
  local repo spec n=0
  prepare_tool redocly || return 1
  list_specs || return 1
  if [ -z "$SPECS" ]; then
    na_tool redocly "aucune spec OpenAPI trouvée dans les repos"
    return
  fi
  while IFS=$'\t' read -r repo spec; do
    [ -n "$repo" ] || continue
    n=$((n + 1))
    tolerate redocly docker_tool redocly none -- lint "/repos/$repo/$spec" --format json \
      --max-problems 10000 >"$RUN_DIR/tools/redocly/raw/$repo-$n.json"
  done <<<"$SPECS"
  summarize redocly
}

tool_schemathesis() {
  local repo spec
  prepare_tool schemathesis || return 1
  list_specs || return 1
  if [ -z "$SPECS" ]; then
    na_tool schemathesis "aucune spec OpenAPI trouvée dans les repos"
    return
  fi
  if [ -n "${GM_AUDIT_AUTH_USER:-}" ]; then
    skip_tool schemathesis "authentification requise"
    return
  fi
  while IFS=$'\t' read -r repo spec; do
    [ -n "$repo" ] || continue
    tolerate schemathesis docker_tool schemathesis none -- \
      run "/repos/$repo/$spec" --url "${GM_AUDIT_TARGET:-}" --include-method GET \
      --exclude-method-regex "$UNSAFE_METHODS_REGEX" --exclude-checks unsupported_method \
      --max-examples 20 --rate-limit 2/s --report junit --report-dir /out/schemathesis/raw
  done <<<"$SPECS"
  summarize schemathesis
}

tool_crawl() {
  prepare_tool crawl || return 1
  tolerate crawl docker_tool agence crawl -- crawl
}

run_tool() {
  case "$1" in
    http) tool_http "$2" ;;
    lighthouse | a11y | yellowlab) tool_pages "$1" ;;
    lighthouse-desktop) tool_desktop lighthouse-desktop lighthouse --form-factor desktop ;;
    yellowlab-desktop) tool_desktop yellowlab-desktop yellowlab --device desktop ;;
    viewport) tool_viewport ;;
    stylelint | deps) tool_repos_1_2 "$1" ;;
    assets) tool_assets ;;
    nu) tool_nu ;;
    testssl) tool_testssl ;;
    gitleaks) tool_gitleaks ;;
    redocly) tool_redocly ;;
    schemathesis) tool_schemathesis ;;
    crawl) tool_crawl ;;
    *) return 2 ;;
  esac
}

cmd_run() {
  local followup=0
  if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    usage
  fi
  if [ "$#" -eq 3 ]; then
    [ "$3" = "--followup" ] || usage
    followup=1
  fi
  is_tool "$2" || die "Outil inconnu : $2 (outils : ${ALL_TOOLS[*]})."
  if [ "$followup" -eq 1 ] && [ "$2" != "http" ]; then
    die "--followup ne s'applique qu'à l'outil http."
  fi
  require_docker
  load_run "$1"
  local rc=0 manifest_rc=0
  run_tool "$2" "$followup" || rc=$?
  python3 "$S/cqa_plan.py" manifest "$RUN_DIR" >/dev/null || manifest_rc=$?
  if [ "$manifest_rc" -ne 0 ]; then
    say "Le manifeste des résultats n'a pas pu être écrit."
  fi
  if [ "$rc" -ne 0 ]; then
    return "$rc"
  fi
  return "$manifest_rc"
}

cmd_all() {
  local tool rc failed="" manifest_rc=0
  [ "$#" -eq 1 ] || usage
  require_docker
  load_run "$1"
  for tool in "${ALL_TOOLS[@]}"; do
    say "[$tool] démarrage"
    rc=0
    run_tool "$tool" 0 >&2 || rc=$?
    if [ "$rc" -eq 0 ]; then
      say "[$tool] terminé"
    else
      say "[$tool] en échec (code $rc)"
      failed="$failed $tool"
    fi
  done
  python3 "$S/cqa_plan.py" manifest "$RUN_DIR" || manifest_rc=$?
  if [ -n "$failed" ]; then
    say "Outils en échec :$failed"
  fi
  if [ "$manifest_rc" -ne 0 ]; then
    say "Le manifeste des résultats n'a pas pu être écrit."
  fi
  if [ -n "$failed" ] || [ "$manifest_rc" -ne 0 ]; then
    return 1
  fi
}

cmd_pull() {
  local name ref failed=0
  require_docker
  for name in "${IMAGES[@]}"; do
    ref="$(image_ref "$name")"
    say "Téléchargement de $ref"
    if ! docker pull "$ref" </dev/null; then
      say "Échec du téléchargement de $ref"
      failed=1
    fi
  done
  if [ "$failed" -ne 0 ]; then
    say "Au moins une image n'a pas été téléchargée. Connexion au registre requise ?"
    say "Lancez : docker login ${GM_CQA_REGISTRY:-$DEFAULT_REGISTRY}"
    return 1
  fi
}

cmd_doctor() {
  local present=0 docker_ok=0 login=0 version label name ref arch
  local -a facts
  facts=()
  version=""
  if command -v docker >/dev/null 2>&1; then
    docker_ok=1
    version="$(docker version --format '{{.Server.Version}}' 2>/dev/null </dev/null)" || version=""
    if docker manifest inspect "$(image_ref agence)" >/dev/null 2>&1 </dev/null; then
      login=1
    fi
    for name in "${IMAGES[@]}"; do
      ref="$(image_ref "$name")"
      present=0
      label=""
      if label="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$ref" 2>/dev/null </dev/null)"; then
        present=1
      fi
      facts+=("$name" "$present" "$label")
    done
  else
    for name in "${IMAGES[@]}"; do
      facts+=("$name" 0 "")
    done
  fi
  case "$(uname -m)" in
    x86_64 | amd64) arch="amd64" ;;
    arm64 | aarch64) arch="arm64" ;;
    *) arch="$(uname -m)" ;;
  esac
  python3 - "$docker_ok" "$version" "$login" "$arch" "${facts[@]}" <<'PY'
import json
import re
import sys

docker_ok, version, login, arch, *facts = sys.argv[1:]
images = {}
for index in range(0, len(facts), 3):
    name, present, label = facts[index : index + 3]
    if present != "1":
        images[name] = None
    else:
        images[name] = label if label and label != "<no value>" else "unknown"
agence = images.get("agence")
match = re.fullmatch(r"(\d+)\.(\d+)\.\d+", agence or "")
report = {
    "docker": docker_ok == "1",
    "docker_version": version or None,
    "registry_login": login == "1",
    "images": images,
    "agence_crawl": bool(match) and (int(match[1]), int(match[2])) >= (1, 1),
    # Keep in sync with agence_supports_1_2.
    "agence_1_2": bool(match) and (int(match[1]), int(match[2])) >= (1, 2),
    "arch": arch,
}
print(json.dumps(report, indent=2, ensure_ascii=False))
PY
}

main() {
  [ "$#" -ge 1 ] || usage
  local sub="$1"
  shift
  case "$sub" in
    doctor)
      [ "$#" -eq 0 ] || usage
      cmd_doctor
      ;;
    pull)
      [ "$#" -eq 0 ] || usage
      cmd_pull
      ;;
    run) cmd_run "$@" ;;
    all) cmd_all "$@" ;;
    *) usage ;;
  esac
}

main "$@"
