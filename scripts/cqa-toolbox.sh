#!/usr/bin/env bash
# Launcher of the Docker toolbox of the checklist audit (plugin side of the contract v1).
#
#   cqa-toolbox.sh doctor
#   cqa-toolbox.sh pull
#   cqa-toolbox.sh run <run_dir> <tool> [--followup]
#   cqa-toolbox.sh all <run_dir>
#   cqa-toolbox.sh check <check_dir> <worktree> <repo_name>
#   cqa-toolbox.sh secrets <in_file> <out_dir>
#   cqa-toolbox.sh ci-json <repo_dir> <out_file>
#   cqa-toolbox.sh exec <worktree> <log_file> <image> <workdir> <on|off> <timeout_s> -- <command>
#   cqa-toolbox.sh pull-image <image>
#   cqa-toolbox.sh image-present <image>
#
# Exit codes: 0 success, 1 tool failure, 2 usage or environment; "run" passes through the
# exit code of the tool container. Secret values never appear on a command line: they are
# forwarded to containers by name only (--env NAME).
set -euo pipefail
# A set CDPATH makes "cd" print the directory, or pick another one, inside $(...).
unset CDPATH

S="$(cd "$(dirname "$0")" && pwd)"

DEFAULT_REGISTRY="registry.gitlab.gingerminds.fr/gm/tools/checklist-qualite-outillage-audit"
AGENCY_IMAGES="registry.gitlab.gingerminds.fr/gm/docker-images"
IMAGES=(agence nu testssl gitleaks redocly schemathesis nginx varnish)
ALL_TOOLS=(http gitleaks assets stylelint deps lighthouse lighthouse-desktop a11y viewport mobile nu yellowlab yellowlab-desktop testssl redocly schemathesis crawl)
NEEDS_1_2_REASON="boîte à outils 1.2 requise : lancez cqa-toolbox.sh pull"
NEEDS_1_4_REASON="boîte à outils 1.4 requise : lancez cqa-toolbox.sh pull"
PROD_RUN_REASON="run de prod : le test mobile ne s'exécute pas sur la prod"
# Version gates: "unset" until read, then 0 or 1 (once per launcher call).
AGENCE_1_2="unset"
AGENCE_1_4="unset"
# 1 when toolbox-env flags the run as a prod run (set by load_run, never read from the environment).
PROD_RUN=0
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
  say "  cqa-toolbox.sh check <dossier_de_controle> <worktree> <nom_du_repo>"
  say "  cqa-toolbox.sh secrets <fichier_d_entree> <dossier_de_sortie>"
  say "  cqa-toolbox.sh ci-json <dossier_du_repo> <fichier_de_sortie>"
  say "  cqa-toolbox.sh exec <worktree> <journal> <image> <dossier> <on|off> <delai_s> -- <commande>"
  say "  cqa-toolbox.sh pull-image <image>"
  say "  cqa-toolbox.sh image-present <image>"
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

  CRAWL_MINUTES=30
  CRAWL_MAX_URLS=2000
  PROD_RUN=0
  env_lines="$(python3 "$S/cqa_plan.py" toolbox-env "$RUN_DIR")" || exit 2
  while IFS='=' read -r key value; do
    case "$key" in
      GM_AUDIT_TARGET | GM_AUDIT_ROBOTS | GM_AUDIT_RATE | GM_AUDIT_USER_AGENT | GM_AUDIT_PROD_ORIGIN)
        export "$key=$value"
        ;;
      GM_CQA_CRAWL_MINUTES) CRAWL_MINUTES="$value" ;;
      GM_CQA_CRAWL_MAX_URLS) CRAWL_MAX_URLS="$value" ;;
      GM_CQA_PROD_RUN)
        [ "$value" = "1" ] || die "toolbox-env a renvoyé une valeur inattendue pour GM_CQA_PROD_RUN."
        PROD_RUN=1
        ;;
      "") ;;
      *) die "toolbox-env a renvoyé une variable inattendue : $key" ;;
    esac
  done <<<"$env_lines"

  settle_leftovers
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

# Start the output of a tool from nothing so that stale files never reach the summary, and
# create raw/ as the current user (a missing bind mount source would be created by Docker as
# root). The previous folder was set aside by run_tool (stash_tool): it stays whole until the new
# attempt has succeeded.
prepare_tool() {
  local dir="$RUN_DIR/tools/$1"
  rm -rf -- "$dir" || return 1
  mkdir -p -- "$dir/raw" || return 1
}

# The folder a launch of <tool> <0|1> writes in: the tool, or its follow-up.
tool_folder() {
  case "$1:$2" in
    http:1) printf '%s' http-followup ;;
    mobile:1) printf '%s' mobile-followup ;;
    *) printf '%s' "$1" ;;
  esac
}

# result_field <folder> <key>: a text key of the result.json of a tool folder, "" when unreadable.
result_field() {
  python3 -c 'import json, sys
try:
    value = json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2])
except (OSError, ValueError, AttributeError):
    value = None
print(" ".join(value.split()) if isinstance(value, str) else "")' "$1/result.json" "$2" </dev/null
}

# True when the result.json of a tool folder says ok or not_applicable: a result worth keeping.
is_good_result() {
  case "$(result_field "$1" status)" in
    ok | not_applicable) return 0 ;;
    *) return 1 ;;
  esac
}

# Set the folder of a tool aside (tools/<tool>.prev) before a new attempt, after settling what an
# interrupted launch left behind.
stash_tool() {
  local dir="$RUN_DIR/tools/$1"
  settle_tool "$1"
  if [ -e "$dir" ] || [ -L "$dir" ]; then
    mv -- "$dir" "$dir.prev" || return 1
  fi
}

# Decide which folder a tool keeps once its attempt is over. A good new result (ok or
# not_applicable) replaces the previous folder. Else a good previous result comes back, the new
# attempt being written next to it in result-last-attempt.json, and the message says so. Without a
# good previous result the new failure stays installed, visible. Safe to call with nothing set aside.
settle_tool() {
  local name="$1" dir="$RUN_DIR/tools/$1" status reason shown
  [ -d "$dir.prev" ] || return 0
  if is_good_result "$dir" || ! is_good_result "$dir.prev"; then
    rm -rf -- "$dir.prev"
    return 0
  fi
  status="$(result_field "$dir" status)"
  reason="$(result_field "$dir" reason)"
  if [ -n "$status" ]; then
    cp -- "$dir/result.json" "$dir.prev/result-last-attempt.json" || true
  else
    status="aucun résultat"
    printf '{"tool": "%s", "status": "failed", "reason": "result.json absent ou illisible"}\n' \
      "$name" >"$dir.prev/result-last-attempt.json" || true
  fi
  case "$status" in
    failed) shown="en échec" ;;
    skipped) shown="ignoré" ;;
    *) shown="$status" ;;
  esac
  if rm -rf -- "$dir" && mv -- "$dir.prev" "$dir"; then
    say "$name : le nouvel essai n'a pas abouti ($shown${reason:+ : $reason}), le résultat précédent est conservé (détail de l'essai : tools/$name/result-last-attempt.json)."
  else
    say "$name : le résultat précédent n'a pas pu être remis en place, il reste dans tools/$name.prev."
  fi
}

# Settle every folder an interrupted launch set aside, whatever the tool.
settle_leftovers() {
  local path
  for path in "$RUN_DIR"/tools/*.prev; do
    if [ -d "$path" ]; then
      settle_tool "$(basename "${path%.prev}")"
    fi
  done
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
    web) names=(GM_AUDIT_TARGET GM_AUDIT_RATE GM_AUDIT_USER_AGENT GM_AUDIT_ROBOTS GM_AUDIT_AUTH_USER GM_AUDIT_AUTH_PASS GM_AUDIT_MOBILE_BUDGET_S) ;;
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

# True when the local agence image is toolbox 1.4.0 or later (keep in sync with agence_1_4 in doctor).
agence_supports_1_4() {
  local label
  if [ "$AGENCE_1_4" = "unset" ]; then
    AGENCE_1_4=0
    if label="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$(image_ref agence)" 2>/dev/null </dev/null)"; then
      if printf '%s' "$label" | grep -Eq '^([2-9]|[1-9][0-9]+)\.[0-9]+\.[0-9]+$|^1\.([4-9]|[1-9][0-9]+)\.[0-9]+$'; then
        AGENCE_1_4=1
      fi
    fi
  fi
  [ "$AGENCE_1_4" = "1" ]
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

# tool_mobile <0|1>: the mobile sweep (0) or its follow-up (1), in that order of checks: prod run,
# toolbox version, input file of the follow-up, launch. A skip is written in the folder of the
# tool that was asked for.
tool_mobile() {
  local name=mobile
  if [ "$1" -eq 1 ]; then
    name=mobile-followup
  fi
  if [ "$PROD_RUN" = "1" ]; then
    skip_tool "$name" "$PROD_RUN_REASON"
    return
  fi
  if ! agence_supports_1_4; then
    skip_tool "$name" "$NEEDS_1_4_REASON"
    return
  fi
  if [ "$1" -eq 1 ]; then
    if [ ! -f "$RUN_DIR/scenarios-followup.json" ]; then
      say "mobile : scenarios-followup.json est introuvable, lancez d'abord cqa_plan.py scenarios --followup."
      return 2
    fi
    prepare_tool mobile-followup || return 1
    docker_tool agence web -- mobile --scenarios /in/scenarios-followup.json --name mobile-followup
  else
    prepare_tool mobile || return 1
    docker_tool agence web -- mobile --pages /in/context.json
  fi
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
  tolerate crawl docker_tool agence crawl -- crawl --max-minutes "$CRAWL_MINUTES" --max-urls "$CRAWL_MAX_URLS"
}

# run_tool <tool> <0|1>: one attempt, the previous folder of the tool being kept until it succeeds.
run_tool() {
  local folder rc=0
  folder="$(tool_folder "$1" "$2")"
  stash_tool "$folder" || return 1
  run_tool_body "$1" "$2" || rc=$?
  settle_tool "$folder"
  return "$rc"
}

run_tool_body() {
  case "$1" in
    http) tool_http "$2" ;;
    lighthouse | a11y | yellowlab) tool_pages "$1" ;;
    lighthouse-desktop) tool_desktop lighthouse-desktop lighthouse --form-factor desktop ;;
    yellowlab-desktop) tool_desktop yellowlab-desktop yellowlab --device desktop ;;
    viewport) tool_viewport ;;
    mobile) tool_mobile "$2" ;;
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
  if [ "$followup" -eq 1 ] && [ "$2" != "http" ] && [ "$2" != "mobile" ]; then
    die "--followup ne s'applique qu'aux outils http et mobile."
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
    # Keep in sync with agence_supports_1_4.
    "agence_1_4": bool(match) and (int(match[1]), int(match[2])) >= (1, 4),
    "arch": arch,
}
print(json.dumps(report, indent=2, ensure_ascii=False))
PY
}

# Syntax checks of the files listed in /in/files.txt, run by sh inside the container.
# Prints one line per file: ok|ko|skip, check, path, message (tabs and newlines flattened).
IFS= read -r -d '' SYNTAX_SCRIPT <<'SCRIPT' || true
repo="$1"
have() { command -v "$1" >/dev/null 2>&1; }
emit() {
  msg="$(printf '%s' "$4" | tr '\t\r\n' '   ' | sed "s#/repos/$repo/##g" | cut -c1-300)"
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$msg"
}
first_error() {
  printf '%s\n' "$1" | grep -m1 -i 'error' || printf '%s\n' "$1" | head -n 1
}
JSON_JS='try{JSON.parse(require("fs").readFileSync(process.argv[1],"utf8").replace(/^﻿/,""))}catch(e){console.error(e.message);process.exit(1)}'
YAML_JS='const {createRequire}=require("module");const r=createRequire("/opt/gm-audit/package.json");let y;try{y=r("yaml")}catch(e){try{y=r("js-yaml")}catch(f){process.exit(3)}}try{const t=require("fs").readFileSync(process.argv[1],"utf8");if(y.parseAllDocuments){for(const d of y.parseAllDocuments(t)){if(d.errors.length){throw d.errors[0]}}}else{y.loadAll(t)}}catch(e){console.error(e.message);process.exit(1)}'
SCSS_JS='const {createRequire}=require("module");const r=createRequire("/opt/gm-audit/package.json");let s;try{s=r("postcss-scss")}catch(e){process.exit(3)}try{s.parse(require("fs").readFileSync(process.argv[1],"utf8"))}catch(e){console.error(e.line?e.line+":"+e.column+" "+(e.reason||e.message):e.message);process.exit(1)}'
while IFS= read -r f || [ -n "$f" ]; do
  [ -n "$f" ] || continue
  p="/repos/$repo/$f"
  case "$f" in
    *.php)
      if have php; then
        if out="$(php -l "$p" 2>&1)"; then emit ok "php -l" "$f" ""; else emit ko "php -l" "$f" "$(first_error "$out")"; fi
      else
        emit skip "php -l" "$f" "php absent de l'image"
      fi
      ;;
    *.js | *.mjs | *.cjs)
      if have node; then
        if out="$(node --check "$p" 2>&1)"; then emit ok "node --check" "$f" ""; else emit ko "node --check" "$f" "$(first_error "$out")"; fi
      else
        emit skip "node --check" "$f" "node absent de l'image"
      fi
      ;;
    tsconfig.json | */tsconfig.json | jsconfig.json | */jsconfig.json | .eslintrc.json | */.eslintrc.json | devcontainer.json | */devcontainer.json | .devcontainer.json | */.devcontainer.json | .vscode/*.json | */.vscode/*.json)
      emit skip json "$f" "JSON avec commentaires possible, non contrôlé"
      ;;
    *.json)
      if have node; then
        if out="$(node -e "$JSON_JS" "$p" 2>&1)"; then emit ok json "$f" ""; else emit ko json "$f" "$(first_error "$out")"; fi
      else
        emit skip json "$f" "node absent de l'image"
      fi
      ;;
    *.yml | *.yaml)
      rc=0
      if have node; then
        out="$(node -e "$YAML_JS" "$p" 2>&1)" || rc=$?
      else
        rc=3
      fi
      case "$rc" in
        0) emit ok yaml "$f" "" ;;
        3) emit skip yaml "$f" "module YAML absent de l'image" ;;
        *) emit ko yaml "$f" "$(first_error "$out")" ;;
      esac
      ;;
    *.scss)
      rc=0
      if have node; then
        out="$(node -e "$SCSS_JS" "$p" 2>&1)" || rc=$?
      else
        rc=3
      fi
      case "$rc" in
        0) emit ok scss "$f" "" ;;
        3) emit skip scss "$f" "module postcss-scss absent de l'image" ;;
        *) emit ko scss "$f" "$(first_error "$out")" ;;
      esac
      ;;
  esac
done </in/files.txt
SCRIPT

# nginx -t of the nginx files of /in/config-plan.tsv (kind, folder, path), run by sh in the
# official nginx image, on the worktree (head) and on /in/base (the folders at the start of the
# pass). Each nginx folder (a lone nginx.conf: that file only) is copied over a copy of
# /etc/nginx, built aside and renamed when complete, its /etc/nginx/ paths pointed at the copy
# and its *.template rendered with dummy values; a file without events or http block is included
# by a minimal main file, in a server then a location block when its directives need them.
# Prints one line per file and side: side, ok|ko|skip, check, path, message.
IFS= read -r -d '' NGINX_SCRIPT <<'SCRIPT' || true
repo="$1"
plan=/in/config-plan.tsv
head_root="/repos/$repo"
base_root=/in/base
etc=/etc/nginx
work=/tmp/cqa
tab="$(printf '\t')"
swap() {
  awk -v a="$1" -v b="$2" 'BEGIN { n = length(a) } { o = ""; s = $0; while (n > 0 && (i = index(s, a)) > 0) { o = o substr(s, 1, i - 1) b; s = substr(s, i + n) } print o s }'
}
emit() {
  msg="$(printf '%s' "$4" | tr '\t\r\n' '   ' | cut -c1-300)"
  printf '%s\t%s\tnginx -t\t%s\t%s\n' "$1" "$2" "$3" "$msg"
}
first_emerg() {
  line="$(printf '%s\n' "$1" | grep -m1 -E '\[(emerg|alert|crit)\]')" || line="$(printf '%s\n' "$1" | grep -m1 .)"
  printf '%s\n' "$line" | sed -E 's/^.*\[(emerg|alert|crit)\] ([0-9]+#[0-9]+: )?//'
}
# prepare <work dir> <root> <folder> <file>: the copy is built in <work dir>.cqa-part and renamed
# only when complete, so that a copy that failed half-way is never used.
prepare() {
  [ -d "$1" ] && return 0
  part="$1.cqa-part"
  rm -rf "$part"
  if ! build "$part" "$1" "$2" "$3" "$4" || ! mv "$part" "$1"; then
    rm -rf "$part"
    return 1
  fi
}
build() {
  mkdir -p "$1" && cp -R "$etc/." "$1/" || return 1
  case "/$4" in
    */nginx) cp -R "$3/$4/." "$1/" || return 1 ;;
    *) cp "$3/$5" "$1/" || return 1 ;;
  esac
  chmod -R u+w "$1" || return 1
  grep -rlI -F /etc/nginx/ "$1" 2>/dev/null | while IFS= read -r f; do
    swap /etc/nginx/ "$2/" <"$f" >"$f.cqa-tmp" && mv "$f.cqa-tmp" "$f"
  done
  find "$1" -type f -name '*.template' | while IFS= read -r t; do
    sed -e 's/\${[A-Z_][A-Z0-9_]*_PORT}/1/g' -e 's/\${[A-Z_][A-Z0-9_]*}/example.test/g' "$t" >"${t%.template}"
  done
}
wrap() {
  case "$1" in
    1) printf 'events {}\nhttp {\ninclude "%s";\n}\n' "$2" ;;
    2) printf 'events {}\nhttp {\nserver {\ninclude "%s";\n}\n}\n' "$2" ;;
    *) printf 'events {}\nhttp {\nserver {\nlocation / {\ninclude "%s";\n}\n}\n}\n' "$2" ;;
  esac
}
# check_one <side> <work dir> <path in the work dir> <path in the repo>
check_one() {
  w="$2"
  target="$w/${3%.template}"
  levels="1 2 3"
  if grep -Eq '^[[:space:]]*(events|http)[[:space:]]*([{]|$)' "$target" 2>/dev/null; then
    levels=0
  elif grep -Eq '^[[:space:]]*server[[:space:]]*([{]|$)' "$target" 2>/dev/null; then
    levels=1
  fi
  for level in $levels; do
    conf="$target"
    if [ "$level" != 0 ]; then
      conf="$w/cqa-wrapper.conf"
      wrap "$level" "$target" >"$conf"
    fi
    if out="$(nginx -t -q -e stderr -c "$conf" 2>&1)"; then
      emit "$1" ok "$4" ""
      return 0
    fi
    case "$out" in
      *"is not allowed here"*) continue ;;
    esac
    break
  done
  emit "$1" ko "$4" "$(first_emerg "$out" | swap "$w/cqa-wrapper.conf" "(fichier englobant du contrôle)" \
    | swap "$work/$1/.cqa-root/" "" | swap "$work/$1/" "")"
}
while IFS="$tab" read -r kind folder file || [ -n "$kind" ]; do
  [ "$kind" = nginx ] || continue
  if [ "$folder" = . ]; then
    sub=.cqa-root
    rel="$file"
  else
    sub="$folder"
    rel="${file#"$folder"/}"
  fi
  for side in head base; do
    if [ "$side" = head ]; then root="$head_root"; else root="$base_root"; fi
    [ -f "$root/$file" ] || continue
    if prepare "$work/$side/$sub" "$root" "$folder" "$file"; then
      check_one "$side" "$work/$side/$sub" "$rel" "$file"
    else
      emit "$side" skip "$file" "copie du dossier nginx impossible"
    fi
  done
done <"$plan"
SCRIPT

# varnishd -C of the VCL files of /in/config-plan.tsv, run by sh in the official varnish image,
# on the worktree (head) and on /in/base, includes searched in the file's folder first. Prints
# one line per file and side, as NGINX_SCRIPT does.
IFS= read -r -d '' VARNISH_SCRIPT <<'SCRIPT' || true
repo="$1"
plan=/in/config-plan.tsv
head_root="/repos/$repo"
base_root=/in/base
state=/var/lib/varnish
err=/tmp/cqa-vcc.err
tab="$(printf '\t')"
n=0
swap() {
  awk -v a="$1" -v b="$2" 'BEGIN { n = length(a) } { o = ""; s = $0; while (n > 0 && (i = index(s, a)) > 0) { o = o substr(s, 1, i - 1) b; s = substr(s, i + n) } print o s }'
}
emit() {
  msg="$(printf '%s' "$4" | tr '\t\r\n' '   ' | cut -c1-300)"
  printf '%s\t%s\tvarnishd -C\t%s\t%s\n' "$1" "$2" "$3" "$msg"
}
while IFS="$tab" read -r kind folder file || [ -n "$kind" ]; do
  [ "$kind" = varnish ] || continue
  for side in head base; do
    if [ "$side" = head ]; then root="$head_root"; else root="$base_root"; fi
    [ -f "$root/$file" ] || continue
    n=$((n + 1))
    dir="$(dirname "$root/$file")"
    if varnishd -C -n "$state/cqa-$n" -p "vcl_path=$dir:/etc/varnish:/usr/share/varnish/vcl" -f "$root/$file" >/dev/null 2>"$err"; then
      emit "$side" ok "$file" ""
    else
      msg="$(sed -n '/^Message from VCC-compiler:/{n;p;q;}' "$err")"
      [ -n "$msg" ] || msg="$(grep -v -x 'Error:' "$err" | grep -m1 .)"
      at="$(grep -m1 -E "^\('.*' Line [0-9]+ Pos [0-9]+\)" "$err")"
      [ -z "$at" ] || msg="$msg $at"
      emit "$side" ko "$file" "$(printf '%s\n' "$msg" | swap "$root/" "")"
    fi
  done
done <"$plan"
SCRIPT

# Run by python3 -I -c: writes <check_dir>/config-plan.tsv, prints the host names to map.
IFS= read -r -d '' CONFIG_PLAN_PY <<'PY' || true
import sys
sys.path.insert(0, sys.argv[1])
from cqa import fix_checks
for kind, host in fix_checks.write_config_plan(sys.argv[2], sys.argv[3]):
    print(kind + "\t" + host)
PY

# config_skip <check_dir> <kind> <check> <reason>: a "skip" line for each file of the kind.
config_skip() {
  local kind folder file
  while IFS=$'\t' read -r kind folder file; do
    if [ "$kind" = "$2" ]; then
      printf 'head\tskip\t%s\t%s\t%s\n' "$3" "$file" "$4" >>"$1/config.tsv"
    fi
  done <"$1/config-plan.tsv"
}

# config_run <image> <check> <script> <check_dir> <worktree> <repo> <uid:gid> [docker options ...]
config_run() {
  local image="$1" check="$2" script="$3" check_dir="$4" worktree="$5" repo="$6" uid_gid="$7"
  local ref part rc=0
  shift 7
  ref="$(image_ref "$image")"
  if ! docker image inspect "$ref" >/dev/null 2>&1 </dev/null; then
    config_skip "$check_dir" "$image" "$check" "image $image absente de la boîte à outils : lancez cqa-toolbox.sh pull"
    return 0
  fi
  part="$check_dir/config-$image.part"
  docker run --rm --pull never --network none --cap-drop ALL --security-opt no-new-privileges \
    --user "$uid_gid" --env HOME=/tmp --tmpfs /tmp \
    -v "$check_dir:/in:ro" -v "$worktree:/repos/$repo:ro" "$@" \
    --entrypoint sh "$ref" -c "$script" sh "$repo" >"$part" </dev/null || rc=$?
  if [ "$rc" -eq 0 ]; then
    cat -- "$part" >>"$check_dir/config.tsv"
  else
    say "check : le contrôle $image a échoué (code $rc)."
    config_skip "$check_dir" "$image" "$check" "contrôle $image impossible dans la boîte à outils (code $rc)"
  fi
  rm -f -- "$part"
}

# nginx and Varnish checks of the files of <check_dir>/files.txt, on the worktree and on
# <check_dir>/base (their folders at the start of the pass, written by the caller): official
# images, offline, sources read-only, the host names the files resolve mapped to the loopback.
# Writes <check_dir>/config-plan.tsv and config.tsv (side, status, check, path, message); never
# fails: a missing image or a failed run gives "skip" lines.
config_checks() {
  local check_dir="$1" worktree="$2" repo="$3" uid_gid="$4" lines kind name
  local -a nginx_hosts varnish_hosts
  nginx_hosts=()
  varnish_hosts=()
  grep -Eq '(^|/)nginx/.*\.conf(\.template)?$|(^|/)nginx\.conf$|\.vcl$' "$check_dir/files.txt" || return 0
  : >"$check_dir/config.tsv"
  if ! lines="$(python3 -I -c "$CONFIG_PLAN_PY" "$S" "$check_dir" "$worktree" </dev/null)"; then
    say "check : la liste des fichiers nginx et Varnish n'a pas pu être établie."
    return 0
  fi
  while IFS=$'\t' read -r kind name; do
    case "$kind" in
      nginx) nginx_hosts+=(--add-host "$name:127.0.0.1") ;;
      varnish) varnish_hosts+=(--add-host "$name:127.0.0.1") ;;
    esac
  done <<<"$lines"
  if grep -q $'^nginx\t' "$check_dir/config-plan.tsv"; then
    config_run nginx "nginx -t" "$NGINX_SCRIPT" "$check_dir" "$worktree" "$repo" "$uid_gid" \
      --tmpfs /var/cache/nginx:mode=1777 --tmpfs /var/log/nginx:mode=1777 --tmpfs /run:mode=1777 \
      ${nginx_hosts[@]+"${nginx_hosts[@]}"}
  fi
  if grep -q $'^varnish\t' "$check_dir/config-plan.tsv"; then
    config_run varnish "varnishd -C" "$VARNISH_SCRIPT" "$check_dir" "$worktree" "$repo" "$uid_gid" \
      --tmpfs /var/lib/varnish:exec,mode=1777 \
      ${varnish_hosts[@]+"${varnish_hosts[@]}"}
  fi
}

# check <check_dir> <worktree> <repo_name>: syntax, stylelint, nginx and Varnish checks of the files
# listed in <check_dir>/files.txt, on a read-only copy of the worktree, without network and without
# credentials. Writes only under <check_dir> (syntax.tsv, config-plan.tsv, config.tsv, tools/stylelint/).
cmd_check() {
  local check_dir worktree repo rc=0 uid_gid
  [ "$#" -eq 3 ] || usage
  require_docker
  [ -d "$1" ] || die "Le dossier de contrôle est introuvable : $1"
  [ -d "$2" ] || die "Le worktree est introuvable : $2"
  check_dir="$(cd "$1" && pwd)"
  worktree="$(cd "$2" && pwd)"
  repo="$3"
  case "$repo" in
    "" | *[!A-Za-z0-9_.-]* | -* | .*) die "Le nom du repo « $repo » ne convient pas (lettres, chiffres, point, tiret, souligné)." ;;
  esac
  case "$check_dir$worktree" in
    *:*) die "Les chemins du contrôle ne doivent pas contenir « : » (montage Docker)." ;;
  esac
  [ -f "$check_dir/files.txt" ] || die "files.txt est introuvable dans $check_dir."
  [ -f "$check_dir/context.json" ] || die "context.json est introuvable dans $check_dir."
  uid_gid="$(id -u):$(id -g)"
  rm -rf -- "$check_dir/tools" "$check_dir/syntax.tsv" "$check_dir/config.tsv" \
    "$check_dir/config-plan.tsv" || return 1
  mkdir -p -- "$check_dir/tools/stylelint/raw" || return 1
  config_checks "$check_dir" "$worktree" "$repo" "$uid_gid"

  docker run --rm --pull never --network none --cap-drop ALL --security-opt no-new-privileges \
    --user "$uid_gid" --env HOME=/tmp --tmpfs /tmp \
    -v "$check_dir:/in:ro" -v "$worktree:/repos/$repo:ro" \
    --entrypoint sh "$(image_ref agence)" -c "$SYNTAX_SCRIPT" sh "$repo" \
    >"$check_dir/syntax.tsv" </dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    rm -f -- "$check_dir/syntax.tsv"
    say "check : les contrôles de syntaxe ont échoué (code $rc)."
    return 1
  fi
  if grep -Eq '\.(css|scss|sass|less|vue)$' "$check_dir/files.txt"; then
    docker run --rm --pull never --network none --cap-drop ALL --security-opt no-new-privileges \
      --user "$uid_gid" --env HOME=/tmp --tmpfs /tmp \
      -v "$check_dir:/in:ro" -v "$check_dir/tools:/out" -v "$worktree:/repos/$repo:ro" \
      "$(image_ref agence)" stylelint --repos /repos --context /in/context.json </dev/null || {
      say "check : stylelint a échoué."
      return 1
    }
  fi
}

# secrets <in_file> <out_dir>: gitleaks (default rules) on the content of <in_file>, for the
# guard of the fix mode: the input mounted read-only, no network, no credentials, no download.
# Inline "gitleaks:allow" comments are ignored (a fixer never waives a finding) and the values
# are redacted from the report (RuleID, StartLine and EndLine stay). Writes
# <out_dir>/gitleaks.json (a JSON array of findings) and nothing else; exit 2 without Docker
# or the gitleaks image, 1 when gitleaks fails.
cmd_secrets() {
  local in_file out_dir uid_gid ref
  [ "$#" -eq 2 ] || usage
  require_docker
  [ -f "$1" ] || die "Le fichier d'entrée est introuvable : $1"
  [ -d "$2" ] || die "Le dossier de sortie est introuvable : $2"
  in_file="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
  out_dir="$(cd "$2" && pwd)"
  case "$in_file$out_dir" in
    *:*) die "Les chemins du contrôle ne doivent pas contenir « : » (montage Docker)." ;;
  esac
  ref="$(image_ref gitleaks)"
  docker image inspect "$ref" >/dev/null 2>&1 </dev/null \
    || die "L'image gitleaks de la boîte à outils est absente ($ref) : lancez cqa-toolbox.sh pull."
  uid_gid="$(id -u):$(id -g)"
  rm -f -- "$out_dir/gitleaks.json" || return 1
  docker run --rm --pull never --network none --user "$uid_gid" --env HOME=/tmp --tmpfs /tmp \
    -v "$in_file:/in/input:ro" -v "$out_dir:/out" \
    --entrypoint sh "$ref" -c 'exec gitleaks stdin --report-format json --report-path /out/gitleaks.json --exit-code 0 --no-banner -l error --ignore-gitleaks-allow --redact=100 </in/input' \
    </dev/null || {
    say "secrets : gitleaks a échoué."
    return 1
  }
  if [ ! -f "$out_dir/gitleaks.json" ]; then
    say "secrets : gitleaks n'a pas écrit de rapport."
    return 1
  fi
}

# .gitlab-ci.yml converted to JSON by the yaml module of the agence image (merge keys resolved).
# js-yaml 3 is read with safeLoad (its load accepts !!js/function); js-yaml 4 has a safe load.
# Exits 3 when no module is found, 1 on invalid YAML or when the file is not a mapping.
IFS= read -r -d '' CI_JSON_JS <<'SCRIPT' || true
const {createRequire}=require("module");
const r=createRequire("/opt/gm-audit/package.json");
let y,legacy=false;
try{y=r("yaml")}catch(e){try{y=r("js-yaml");legacy=true}catch(f){process.exit(3)}}
try{
  const t=require("fs").readFileSync(process.argv[1],"utf8");
  const d=legacy?(y.DEFAULT_SAFE_SCHEMA&&y.safeLoad?y.safeLoad(t):y.load(t)):y.parse(t,{merge:true,logLevel:"error"});
  if(d===null||typeof d!=="object"||Array.isArray(d)){throw new Error("le fichier n'est pas un mapping YAML")}
  process.stdout.write(JSON.stringify(d))
}catch(e){console.error(e.message);process.exit(1)}
SCRIPT

# ci-json <repo_dir> <out_file>: .gitlab-ci.yml of <repo_dir> converted to JSON in <out_file>,
# offline, with the yaml module of the agence image; only that file is mounted, read-only. Exit 0
# and the file written, 3 without a CI file, 2 without Docker or the agence image or for a CI file
# that is a symbolic link, 1 on invalid YAML (message on stderr). A stale <out_file> is always
# removed first.
cmd_ci_json() {
  local repo out_file out_dir uid_gid ref err rc=0
  [ "$#" -eq 2 ] || usage
  [ -d "$1" ] || die "Le dossier du repo est introuvable : $1"
  out_dir="$(dirname "$2")"
  [ -d "$out_dir" ] || die "Le dossier de sortie est introuvable : $out_dir"
  repo="$(cd "$1" && pwd)"
  out_file="$(cd "$out_dir" && pwd)/$(basename "$2")"
  case "$repo$out_file" in
    *:*) die "Les chemins de la conversion ne doivent pas contenir « : » (montage Docker)." ;;
  esac
  rm -f -- "$out_file" || return 1
  if [ -L "$repo/.gitlab-ci.yml" ]; then
    die "ci-json : .gitlab-ci.yml est un lien symbolique dans $repo : fichier non lu."
  fi
  if [ ! -f "$repo/.gitlab-ci.yml" ]; then
    say "ci-json : .gitlab-ci.yml est introuvable dans $repo."
    return 3
  fi
  require_docker
  ref="$(image_ref agence)"
  docker image inspect "$ref" >/dev/null 2>&1 </dev/null \
    || die "L'image agence de la boîte à outils est absente ($ref) : lancez cqa-toolbox.sh pull."
  uid_gid="$(id -u):$(id -g)"
  err="$(docker run --rm --pull never --network none --cap-drop ALL --security-opt no-new-privileges \
    --user "$uid_gid" --env HOME=/tmp --tmpfs /tmp \
    -v "$repo/.gitlab-ci.yml:/repo/.gitlab-ci.yml:ro" \
    --entrypoint node "$ref" -e "$CI_JSON_JS" /repo/.gitlab-ci.yml 2>&1 >"$out_file" </dev/null)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  rm -f -- "$out_file"
  case "$rc" in
    1)
      say "ci-json : .gitlab-ci.yml est invalide : $(printf '%s\n' "$err" | head -n 1 | cut -c1-300)"
      return 1
      ;;
    3) die "ci-json : module YAML absent de l'image agence : lancez cqa-toolbox.sh pull." ;;
    *) die "ci-json : le conteneur a échoué (code $rc)." ;;
  esac
}

# Container of "exec" in flight, stopped when the launcher is interrupted.
EXEC_NAME=""

exec_abort() {
  if [ -n "$EXEC_NAME" ]; then
    docker kill "$EXEC_NAME" >/dev/null 2>&1 </dev/null || true
  fi
  exit "$1"
}

# A reference that cannot be read as a docker option or as several words.
valid_image_ref() {
  case "$1" in
    "" | -* | *..* | *[!A-Za-z0-9._/:@-]*) return 1 ;;
  esac
  return 0
}

# exec <worktree> <log_file> <image> <workdir> <on|off> <timeout_s> -- <command>: one project
# command run by sh in a disposable container that sees only the worktree (read-write, as /work),
# as the current user, without capabilities, without the Docker socket and without any variable
# of the workstation. The .git entry of the worktree is mounted read-only over it: the host's git
# reads it after the command. The network is open only for "on". The whole output goes to
# <log_file>. Exit: the code of the command, 124 when <timeout_s> is exceeded (the container is
# killed), 2 for a refused argument (a .git missing or a symbolic link included), without Docker
# or without the image (it is never pulled here).
cmd_exec() {
  local worktree log_file log_dir image workdir network timeout_s command dir uid_gid name pid
  local rc=0 timed_out=0 start
  local -a net
  [ "$#" -eq 8 ] && [ "$7" = "--" ] || usage
  [ -d "$1" ] || die "Le worktree est introuvable : $1"
  log_dir="$(dirname "$2")"
  [ -d "$log_dir" ] || die "Le dossier du journal est introuvable : $log_dir"
  worktree="$(cd "$1" && pwd)"
  log_file="$(cd "$log_dir" && pwd)/$(basename "$2")"
  image="$3"
  workdir="$4"
  network="$5"
  timeout_s="$6"
  command="$8"
  case "$worktree$log_file" in
    *:*) die "Les chemins de l'exécution ne doivent pas contenir « : » (montage Docker)." ;;
  esac
  if [ -L "$worktree/.git" ] || { [ ! -f "$worktree/.git" ] && [ ! -d "$worktree/.git" ]; }; then
    die "Le .git du worktree est absent ou est un lien symbolique : $worktree/.git (commande non lancée)."
  fi
  valid_image_ref "$image" || die "Le nom d'image « $image » ne convient pas."
  case "$workdir" in
    /* | .. | ../* | */.. | */../*) die "Le dossier de travail doit rester dans le worktree : $workdir" ;;
  esac
  while [ "${workdir#./}" != "$workdir" ]; do
    workdir="${workdir#./}"
  done
  while [ "${workdir%/}" != "$workdir" ]; do
    workdir="${workdir%/}"
  done
  case "$workdir" in
    "" | .) dir=/work ;;
    *)
      [ -d "$worktree/$workdir" ] || die "Le dossier de travail est absent du worktree : $workdir"
      dir="/work/$workdir"
      ;;
  esac
  case "$network" in
    on) net=() ;;
    off) net=(--network none) ;;
    *) die "Le réseau doit valoir « on » ou « off » : $network" ;;
  esac
  case "$timeout_s" in
    "" | *[!0-9]*) die "Le délai doit être un nombre entier de secondes : $timeout_s" ;;
  esac
  if [ "${#timeout_s}" -gt 6 ] || [ "$((10#$timeout_s))" -le 0 ]; then
    die "Le délai doit être compris entre 1 et 999999 secondes : $timeout_s"
  fi
  timeout_s="$((10#$timeout_s))"
  [ -n "$command" ] || die "La commande à exécuter est vide."
  require_docker
  if ! docker image inspect "$image" >/dev/null 2>&1 </dev/null; then
    if docker version >/dev/null 2>&1 </dev/null; then
      die "image absente : $image"
    fi
    die "Docker ne répond pas : démarrez Docker Desktop ou le service Docker."
  fi
  uid_gid="$(id -u):$(id -g)"
  name="cqa-exec-$$-$RANDOM$RANDOM"
  : >"$log_file" || die "Le journal n'est pas inscriptible : $log_file"
  docker run --rm --pull never --name "$name" ${net[@]+"${net[@]}"} \
    --cap-drop ALL --security-opt no-new-privileges \
    --user "$uid_gid" --env HOME=/tmp --tmpfs /tmp:rw,exec,nosuid,size=2g \
    -v "$worktree:/work" -v "$worktree/.git:/work/.git:ro" -w "$dir" \
    --entrypoint sh "$image" -c "$command" >"$log_file" 2>&1 </dev/null &
  pid=$!
  EXEC_NAME="$name"
  trap 'exec_abort 130' INT
  trap 'exec_abort 143' TERM
  start=$SECONDS
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$((SECONDS - start))" -ge "$timeout_s" ]; then
      timed_out=1
      docker kill "$name" >/dev/null 2>&1 </dev/null || true
      break
    fi
    sleep 0.2 2>/dev/null || sleep 1
  done
  wait "$pid" || rc=$?
  EXEC_NAME=""
  trap - INT TERM
  if [ "$timed_out" -eq 1 ]; then
    say "exec : délai de $timeout_s s dépassé, le conteneur $name a été arrêté."
    return 124
  fi
  return "$rc"
}

# Docker Hub, the toolbox registry and the agence docker-images only.
image_source_allowed() {
  local ref="$1" toolbox first
  valid_image_ref "$ref" || return 1
  toolbox="${GM_CQA_REGISTRY:-$DEFAULT_REGISTRY}"
  toolbox="${toolbox%/}"
  case "$ref" in
    "$toolbox"/?* | "$AGENCY_IMAGES"/?*) return 0 ;;
    */*) first="${ref%%/*}" ;;
    *) return 0 ;;
  esac
  case "$first" in
    docker.io | index.docker.io) return 0 ;;
    *.* | *:* | localhost) return 1 ;;
  esac
  return 0
}

# pull-image <image>: docker pull of an image of Docker Hub, of the toolbox registry or of
# registry.gitlab.gingerminds.fr/gm/docker-images/; any other registry is refused (exit 2).
cmd_pull_image() {
  local image toolbox
  [ "$#" -eq 1 ] || usage
  image="$1"
  image_source_allowed "$image" \
    || die "Image refusée : « $image ». Seules les images de Docker Hub, de la boîte à outils et de $AGENCY_IMAGES/ sont téléchargées."
  require_docker
  say "Téléchargement de $image"
  if ! docker pull "$image" </dev/null; then
    say "Échec du téléchargement de $image"
    toolbox="${GM_CQA_REGISTRY:-$DEFAULT_REGISTRY}"
    case "$image" in
      "${toolbox%/}"/?* | "$AGENCY_IMAGES"/?*) say "Connexion au registre requise ? Lancez : docker login ${image%%/*}" ;;
    esac
    return 1
  fi
}

# image-present <image>: exit 0 when the image is in the local Docker store, 1 when it is absent,
# 2 for a refused name or when Docker does not answer. Nothing is pulled.
cmd_image_present() {
  [ "$#" -eq 1 ] || usage
  valid_image_ref "$1" || die "Le nom d'image « $1 » ne convient pas."
  require_docker
  if docker image inspect "$1" >/dev/null 2>&1 </dev/null; then
    return 0
  fi
  docker version >/dev/null 2>&1 </dev/null \
    || die "Docker ne répond pas : démarrez Docker Desktop ou le service Docker."
  return 1
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
    check) cmd_check "$@" ;;
    secrets) cmd_secrets "$@" ;;
    ci-json) cmd_ci_json "$@" ;;
    exec) cmd_exec "$@" ;;
    pull-image) cmd_pull_image "$@" ;;
    image-present) cmd_image_present "$@" ;;
    *) usage ;;
  esac
}

main "$@"
