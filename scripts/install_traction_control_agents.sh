#!/usr/bin/env bash
# Download the allowlisted support repositories and render or activate a
# light, moderate, or heavy traction-control agent profile.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORTFOLIO_ROOT_DEFAULT="$(cd "${REPO_ROOT}/../.." && pwd)"
REPO_CONFIG_DEFAULT="${REPO_ROOT}/config/traction-control-agents/repos.conf"
JOB_CONFIG_DEFAULT="${REPO_ROOT}/config/traction-control-agents/jobs.conf"

TIER=""
PORTFOLIO_ROOT="${PORTFOLIO_ROOT:-${PORTFOLIO_ROOT_DEFAULT}}"
PROVIDER="auto"
MODEL=""
PLATFORM="auto"
CLONE_PROTOCOL="https"
CLONE_MISSING=1
ACTIVATE=0
DRY_RUN=0
NO_SCHEDULER=0
AUTONOMOUS_CI_REPAIR=0
LIST_TIERS=0
REPO_CONFIG="${REPO_CONFIG_DEFAULT}"
JOB_CONFIG="${JOB_CONFIG_DEFAULT}"
STATE_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/traction-control/bootstrap"
SYSTEMD_UNIT_DIR=""
LAUNCHD_DIR=""
CONFIG_HOME_VALUE="${XDG_CONFIG_HOME:-${HOME}/.config}"
LAUNCHD_RUNTIME_STATE_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/traction-control/launchd-scheduler"
LAUNCHD_POLL_SECONDS=300
LAUNCHD_CANDIDATE_DIR=""

usage() {
  cat <<'EOF'
Usage: install_traction_control_agents.sh --tier light|moderate|heavy [options]

Download missing allowlisted support repos and render a traction-control agent
profile. Scheduler definitions are left inactive unless --activate is passed.

Options:
  --tier NAME                     Profile: light, moderate, or heavy.
  --portfolio-root PATH           Portfolio checkout root.
  --provider NAME                 auto, codex, claude, or copilot (default: auto).
  --model MODEL                   Optional provider model override.
  --platform NAME                 auto, linux, or macos (default: auto).
  --clone-protocol NAME           https or ssh (default: https).
  --no-clone                      Do not clone missing repositories.
  --no-scheduler                  Download/validate repos without rendering jobs.
  --activate                      Enable rendered timers or LaunchAgents.
  --enable-autonomous-ci-repair   Heavy only: schedule broad CI repair instead
                                  of discovery-only CI monitoring.
  --dry-run                       Print the plan without writes, network, or service calls.
  --state-dir PATH                Override generated bootstrap state directory.
  --systemd-unit-dir PATH         Override rendered systemd user-unit directory.
  --launchd-dir PATH              Override rendered LaunchAgent directory.
  --repo-config PATH              Override the repository profile data file.
  --job-config PATH               Override the job profile data file.
  --list-tiers                    Describe the available profiles and exit.
  --help                          Show this help text.

Profiles select support repos and installed jobs. Discovery workloads apply
their own depth, exclusion, and eligibility rules beneath PORTFOLIO_ROOT;
profiles are not target repository allowlists.
EOF
}

list_tiers() {
  cat <<'EOF'
light
  Repos: traction-control, clockwork
  Jobs: portfolio audit, review-first bug sweep, read-only CI discovery

moderate
  Adds repos: archility, tachometer
  Adds jobs: architecture audit/render, template consolidation, REFS audit

heavy
  Adds repos: auto-pass, shock-relay
  Adds jobs: tachometer disk remediation and on-demand CI repair
  Autonomous CI repair remains opt-in with --enable-autonomous-ci-repair.
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

info() {
  printf 'info: %s\n' "$*"
}

plan() {
  printf 'plan: %s\n' "$*"
}

need_value() {
  [[ $# -ge 2 ]] || fail "$1 requires a value"
}

profile_contains() {
  local profiles="$1"
  local requested="$2"
  case ",${profiles}," in
    *",${requested},"*) return 0 ;;
    *) return 1 ;;
  esac
}

job_is_selected() {
  local profiles="$1"
  local job_name="$2"

  if (( AUTONOMOUS_CI_REPAIR == 1 )) && [[ "${job_name}" == "ci-repair-agentic-discovery" ]]; then
    return 1
  fi
  if profile_contains "${profiles}" "${TIER}"; then
    return 0
  fi
  if (( AUTONOMOUS_CI_REPAIR == 1 )) && profile_contains "${profiles}" 'autonomous-heavy'; then
    return 0
  fi
  return 1
}

selected_job_contains() {
  local job_name="$1"
  case "${SELECTED_JOB_KEY}" in
    *"|${job_name}|"*) return 0 ;;
    *) return 1 ;;
  esac
}

validate_relative_repo_path() {
  local relative_path="$1"
  [[ -n "${relative_path}" ]] || fail "repository config contains an empty relative path"
  case "${relative_path}" in
    /*|.|..|../*|*/../*|*/..|./*|*/./*)
      fail "unsafe repository relative path: ${relative_path}"
      ;;
  esac
}

canonicalize_output_directory() {
  local label="$1"
  local directory_path="$2"
  local existing_path=""
  local missing_suffix=""
  local path_component=""
  local resolved_path=""
  [[ -n "${directory_path}" ]] || fail "${label} cannot be empty"
  case "${directory_path}" in
    /*) ;;
    *) fail "${label} must be an absolute path: ${directory_path}" ;;
  esac
  while [[ "${directory_path}" != "/" && "${directory_path}" == */ ]]; do
    directory_path="${directory_path%/}"
  done
  [[ "${directory_path}" != "/" ]] || fail "${label} cannot be the filesystem root"
  case "/${directory_path#/}/" in
    */./*|*/../*) fail "${label} cannot contain . or .. path segments: ${directory_path}" ;;
  esac
  if [[ -e "${directory_path}" && ! -d "${directory_path}" ]]; then
    fail "${label} exists but is not a directory: ${directory_path}"
  fi
  [[ ! -L "${directory_path}" || -d "${directory_path}" ]] \
    || fail "${label} cannot be a dangling symlink: ${directory_path}"

  existing_path="${directory_path}"
  while [[ ! -d "${existing_path}" ]]; do
    path_component="$(basename "${existing_path}")"
    missing_suffix="/${path_component}${missing_suffix}"
    existing_path="$(dirname "${existing_path}")"
  done
  resolved_path="$(cd "${existing_path}" && pwd -P)"
  printf '%s%s\n' "${resolved_path}" "${missing_suffix}"
}

validate_target_containment() {
  local target_path="$1"
  local existing_path="${target_path}"
  local resolved_path=""

  while [[ ! -e "${existing_path}" && "${existing_path}" != "${PORTFOLIO_ROOT}" ]]; do
    existing_path="$(dirname "${existing_path}")"
  done
  [[ -d "${existing_path}" ]] || fail "repository parent is not a directory: ${existing_path}"
  resolved_path="$(cd "${existing_path}" && pwd -P)"
  case "${resolved_path}" in
    "${PORTFOLIO_ROOT}"|"${PORTFOLIO_ROOT}"/*) ;;
    *) fail "repository target escapes the portfolio root through a symlink: ${target_path}" ;;
  esac
}

validate_repo_config() {
  local profiles=""
  local repo_name=""
  local github_slug=""
  local relative_path=""
  local purpose=""
  local match_count=0

  while IFS='|' read -r profiles repo_name github_slug relative_path purpose; do
    [[ -n "${profiles}" ]] || continue
    case "${profiles}" in \#*) continue ;; esac
    profile_contains "${profiles}" "${TIER}" || continue
    match_count=$(( match_count + 1 ))
    [[ -n "${repo_name}" ]] || fail "repository config contains an empty name"
    [[ "${github_slug}" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] \
      || fail "invalid GitHub slug for ${repo_name}: ${github_slug}"
    validate_relative_repo_path "${relative_path}"
    [[ -n "${purpose}" ]] || fail "repository config contains no purpose for ${repo_name}"
  done < "${REPO_CONFIG}"

  (( match_count > 0 )) || fail "no repositories matched tier ${TIER}"
}

validate_job_record() {
  local job_name="$1"
  local command_rel="$2"
  local arguments_csv="$3"
  local linux_installer="$4"
  local installer_kind="$5"
  local provider_env="$6"
  local model_env="$7"
  local env_slug="$8"
  local schedule_kind="$9"
  shift 9
  local interval_seconds="$1"
  local weekdays="$2"
  local hour="$3"
  local minute="$4"
  local startup_delay="$5"
  local jitter="$6"
  local launchd_network_host="$7"
  local activation_kind="$8"

  [[ "${job_name}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "invalid job name: ${job_name}"
  validate_relative_repo_path "${command_rel}"
  validate_relative_repo_path "${linux_installer}"
  case "${installer_kind}" in basic|agentic|archility|service) ;; *) fail "invalid installer kind for ${job_name}: ${installer_kind}" ;; esac
  case "${schedule_kind}" in interval|calendar|none) ;; *) fail "invalid schedule kind for ${job_name}: ${schedule_kind}" ;; esac
  case "${activation_kind}" in timer|service) ;; *) fail "invalid activation kind for ${job_name}: ${activation_kind}" ;; esac
  if [[ "${activation_kind}" == "service" && "${schedule_kind}" != "none" ]]; then
    fail "on-demand service ${job_name} cannot have a schedule"
  fi
  if [[ "${activation_kind}" == "timer" && "${schedule_kind}" == "none" ]]; then
    fail "timer ${job_name} requires a schedule"
  fi
  if [[ "${env_slug}" != "-" ]]; then
    [[ "${env_slug}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "invalid environment slug for ${job_name}: ${env_slug}"
  fi
  if [[ "${provider_env}" == "-" ]]; then
    [[ "${model_env}" == "-" ]] || fail "model environment without provider environment for ${job_name}"
  else
    [[ "${provider_env}" =~ ^[A-Z][A-Z0-9_]*$ ]] || fail "invalid provider environment name for ${job_name}: ${provider_env}"
    [[ "${model_env}" =~ ^[A-Z][A-Z0-9_]*$ ]] || fail "invalid model environment name for ${job_name}: ${model_env}"
  fi
  case "${installer_kind}" in
    agentic|service) [[ "${provider_env}" != "-" ]] || fail "agentic job ${job_name} requires provider environment names" ;;
    basic|archility) [[ "${provider_env}" == "-" ]] || fail "non-agentic job ${job_name} cannot define provider environment names" ;;
  esac
  [[ -n "${arguments_csv}" ]] || fail "missing arguments field for ${job_name}; use - for none"
  if [[ "${arguments_csv}" != "-" ]]; then
    [[ "${arguments_csv}" != *'|'* ]] || fail "invalid arguments for ${job_name}"
  fi
  case "${schedule_kind}" in
    interval)
      [[ "${interval_seconds}" =~ ^[1-9][0-9]*$ ]] || fail "invalid interval for ${job_name}: ${interval_seconds}"
      ;;
    calendar)
      [[ "${hour}" =~ ^(0|[1-9][0-9]*)$ ]] && (( hour >= 0 && hour <= 23 )) || fail "invalid hour for ${job_name}: ${hour}"
      [[ "${minute}" =~ ^(0|[1-9][0-9]*)$ ]] && (( minute >= 0 && minute <= 59 )) || fail "invalid minute for ${job_name}: ${minute}"
      if [[ "${weekdays}" != "-" ]]; then
        [[ "${weekdays}" =~ ^[0-6](,[0-6])*$ ]] || fail "invalid weekdays for ${job_name}: ${weekdays}"
      fi
      ;;
    none) ;;
  esac
  [[ "${startup_delay}" =~ ^[0-9]+$ ]] || fail "invalid startup delay for ${job_name}: ${startup_delay}"
  [[ "${jitter}" =~ ^[0-9]+$ ]] || fail "invalid jitter for ${job_name}: ${jitter}"
  if [[ "${schedule_kind}" != "interval" && "${startup_delay}" != "0" ]]; then
    fail "startup delay requires the stateful interval adapter for ${job_name}"
  fi
  if [[ "${schedule_kind}" == "none" && "${jitter}" != "0" ]]; then
    fail "on-demand job ${job_name} cannot request scheduler jitter"
  fi
  if [[ "${launchd_network_host}" != "-" ]]; then
    [[ "${launchd_network_host}" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}[A-Za-z0-9]$ ]] \
      || fail "invalid launchd network host for ${job_name}: ${launchd_network_host}"
  fi
}

normalize_github_slug() {
  local value="$1"
  value="${value#https://github.com/}"
  value="${value#http://github.com/}"
  value="${value#ssh://git@github.com/}"
  value="${value#git@github.com:}"
  value="${value%.git}"
  value="${value%/}"
  printf '%s\n' "${value}"
}

xml_escape() {
  printf '%s' "$1" | sed \
    -e 's/&/\&amp;/g' \
    -e 's/</\&lt;/g' \
    -e 's/>/\&gt;/g' \
    -e 's/"/\&quot;/g' \
    -e "s/'/\&apos;/g"
}

plist_string() {
  local indent="$1"
  local value="$2"
  printf '%s<string>%s</string>\n' "${indent}" "$(xml_escape "${value}")"
}

shell_single_quote() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

build_launchd_path() {
  local path_source="${PATH}:/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/npm-global/bin:${HOME}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  local old_ifs="${IFS}"
  local path_entry=""
  local path_result=""
  local path_entries=()

  IFS=':'
  read -r -a path_entries <<< "${path_source}"
  IFS="${old_ifs}"
  for path_entry in "${path_entries[@]}"; do
    case "${path_entry}" in /*) ;; *) continue ;; esac
    if [[ "${path_entry}" != "/" ]]; then
      path_entry="${path_entry%/}"
    fi
    case ":${path_result}:" in *":${path_entry}:"*) continue ;; esac
    if [[ -n "${path_result}" ]]; then
      path_result="${path_result}:${path_entry}"
    else
      path_result="${path_entry}"
    fi
  done
  printf '%s\n' "${path_result}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier)
      need_value "$@"
      TIER="$2"
      shift 2
      ;;
    --portfolio-root)
      need_value "$@"
      PORTFOLIO_ROOT="$2"
      shift 2
      ;;
    --provider)
      need_value "$@"
      PROVIDER="$2"
      shift 2
      ;;
    --model)
      need_value "$@"
      MODEL="$2"
      shift 2
      ;;
    --platform)
      need_value "$@"
      PLATFORM="$2"
      shift 2
      ;;
    --clone-protocol)
      need_value "$@"
      CLONE_PROTOCOL="$2"
      shift 2
      ;;
    --no-clone)
      CLONE_MISSING=0
      shift
      ;;
    --no-scheduler)
      NO_SCHEDULER=1
      shift
      ;;
    --activate)
      ACTIVATE=1
      shift
      ;;
    --enable-autonomous-ci-repair)
      AUTONOMOUS_CI_REPAIR=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --state-dir)
      need_value "$@"
      STATE_DIR="$2"
      shift 2
      ;;
    --systemd-unit-dir)
      need_value "$@"
      SYSTEMD_UNIT_DIR="$2"
      shift 2
      ;;
    --launchd-dir)
      need_value "$@"
      LAUNCHD_DIR="$2"
      shift 2
      ;;
    --repo-config)
      need_value "$@"
      REPO_CONFIG="$2"
      shift 2
      ;;
    --job-config)
      need_value "$@"
      JOB_CONFIG="$2"
      shift 2
      ;;
    --list-tiers)
      LIST_TIERS=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

if (( LIST_TIERS == 1 )); then
  list_tiers
  exit 0
fi

case "${TIER}" in
  light|moderate|heavy) ;;
  "") fail "--tier is required" ;;
  *) fail "unsupported tier: ${TIER}" ;;
esac

case "${PROVIDER}" in
  auto|codex|claude|copilot) ;;
  *) fail "unsupported provider: ${PROVIDER}" ;;
esac

case "${CLONE_PROTOCOL}" in
  https|ssh) ;;
  *) fail "unsupported clone protocol: ${CLONE_PROTOCOL}" ;;
esac

if (( AUTONOMOUS_CI_REPAIR == 1 )) && [[ "${TIER}" != "heavy" ]]; then
  fail "--enable-autonomous-ci-repair requires --tier heavy"
fi

[[ -f "${REPO_CONFIG}" ]] || fail "repository config not found: ${REPO_CONFIG}"
if (( NO_SCHEDULER == 0 )); then
  [[ -f "${JOB_CONFIG}" ]] || fail "job config not found: ${JOB_CONFIG}"
fi
[[ -d "${PORTFOLIO_ROOT}" ]] || fail "portfolio root not found: ${PORTFOLIO_ROOT}"
PORTFOLIO_ROOT="$(cd "${PORTFOLIO_ROOT}" && pwd -P)"
[[ "${PORTFOLIO_ROOT}" != "/" ]] || fail "portfolio root cannot be the filesystem root"

case "${PLATFORM}" in
  auto|linux|macos) ;;
  *) fail "unsupported platform: ${PLATFORM}" ;;
esac

if [[ "${PLATFORM}" == "auto" ]]; then
  case "$(uname -s)" in
    Darwin) PLATFORM="macos" ;;
    Linux) PLATFORM="linux" ;;
    *)
      if (( NO_SCHEDULER == 1 )); then
        PLATFORM="download-only"
      else
        fail "unsupported host platform; pass --no-scheduler for download-only setup"
      fi
      ;;
  esac
fi

case "${PLATFORM}" in
  linux|macos) ;;
  download-only)
    (( NO_SCHEDULER == 1 )) || fail "download-only platform requires --no-scheduler"
    ;;
  *) fail "unsupported platform: ${PLATFORM}" ;;
esac

if (( ACTIVATE == 1 && NO_SCHEDULER == 1 )); then
  fail "--activate cannot be combined with --no-scheduler"
fi

if [[ -z "${SYSTEMD_UNIT_DIR}" ]]; then
  if (( ACTIVATE == 1 )); then
    SYSTEMD_UNIT_DIR="${CONFIG_HOME_VALUE}/systemd/user"
  else
    SYSTEMD_UNIT_DIR="${STATE_DIR}/rendered/systemd"
  fi
fi

if [[ -z "${LAUNCHD_DIR}" ]]; then
  if (( ACTIVATE == 1 )); then
    LAUNCHD_DIR="${HOME}/Library/LaunchAgents"
  else
    LAUNCHD_DIR="${STATE_DIR}/rendered/launchd"
  fi
fi

STATE_DIR="$(canonicalize_output_directory "state directory" "${STATE_DIR}")"
SYSTEMD_UNIT_DIR="$(canonicalize_output_directory "systemd unit directory" "${SYSTEMD_UNIT_DIR}")"
LAUNCHD_DIR="$(canonicalize_output_directory "LaunchAgent directory" "${LAUNCHD_DIR}")"
LAUNCHD_RUNTIME_STATE_DIR="$(canonicalize_output_directory "launchd runtime state directory" "${LAUNCHD_RUNTIME_STATE_DIR}")"
LIVE_SYSTEMD_UNIT_DIR="$(canonicalize_output_directory "live systemd unit directory" "${CONFIG_HOME_VALUE}/systemd/user")"
LIVE_LAUNCHD_DIR="$(canonicalize_output_directory "live LaunchAgent directory" "${HOME}/Library/LaunchAgents")"
LAUNCHD_PATH_VALUE="$(build_launchd_path)"
if (( ACTIVATE == 1 )); then
  if [[ "${PLATFORM}" == "linux" && "${SYSTEMD_UNIT_DIR}" != "${LIVE_SYSTEMD_UNIT_DIR}" ]]; then
    fail "Linux activation requires the user unit directory ${LIVE_SYSTEMD_UNIT_DIR}"
  fi
  if [[ "${PLATFORM}" == "macos" && "${LAUNCHD_DIR}" != "${LIVE_LAUNCHD_DIR}" ]]; then
    fail "macOS activation requires the persistent LaunchAgent directory ${LIVE_LAUNCHD_DIR}"
  fi
elif (( DRY_RUN == 0 )); then
  if [[ "${PLATFORM}" == "linux" && "${SYSTEMD_UNIT_DIR}" == "${LIVE_SYSTEMD_UNIT_DIR}" ]]; then
    fail "refusing render-only output in the live systemd user directory; pass --activate"
  fi
  if [[ "${PLATFORM}" == "macos" && "${LAUNCHD_DIR}" == "${LIVE_LAUNCHD_DIR}" ]]; then
    fail "refusing render-only output in the live LaunchAgents directory; pass --activate"
  fi
fi

info "tier            : ${TIER}"
info "portfolio root  : ${PORTFOLIO_ROOT}"
info "provider/model  : ${PROVIDER}/${MODEL:-<default>}"
info "platform        : ${PLATFORM}"
info "activation      : $([[ ${ACTIVATE} -eq 1 ]] && printf enabled || printf render-only)"

command -v git >/dev/null 2>&1 || fail "git not found"
validate_repo_config

missing_repo_count=0
selected_repo_count=0
CLOCKWORK_REPO=""
ARCHILITY_REPO=""

while IFS='|' read -r profiles repo_name github_slug relative_path purpose; do
  [[ -n "${profiles}" ]] || continue
  case "${profiles}" in \#*) continue ;; esac
  profile_contains "${profiles}" "${TIER}" || continue
  selected_repo_count=$(( selected_repo_count + 1 ))
  validate_relative_repo_path "${relative_path}"
  target_path="${PORTFOLIO_ROOT}/${relative_path}"
  validate_target_containment "${target_path}"

  case "$(normalize_github_slug "${github_slug}")" in
    casonk/clockwork) CLOCKWORK_REPO="${target_path}" ;;
    casonk/archility) ARCHILITY_REPO="${target_path}" ;;
  esac

  if [[ -d "${target_path}" ]] && [[ "$(git -C "${target_path}" rev-parse --is-inside-work-tree 2>/dev/null || true)" == "true" ]]; then
    target_root="$(git -C "${target_path}" rev-parse --show-toplevel 2>/dev/null || true)"
    [[ -n "${target_root}" ]] || fail "cannot resolve git worktree root: ${target_path}"
    target_root="$(cd "${target_root}" && pwd -P)"
    resolved_target_path="$(cd "${target_path}" && pwd -P)"
    [[ "${target_root}" == "${resolved_target_path}" ]] \
      || fail "repository target is only a subdirectory of another checkout: ${target_path}"
    origin_url="$(git -C "${target_path}" remote get-url origin 2>/dev/null || true)"
    if [[ -z "${origin_url}" ]]; then
      fail "existing repo has no origin remote: ${target_path}"
    fi
    actual_slug="$(normalize_github_slug "${origin_url}")"
    expected_slug="$(normalize_github_slug "${github_slug}")"
    [[ "${actual_slug}" == "${expected_slug}" ]] \
      || fail "origin mismatch for ${target_path}: expected ${expected_slug}, found ${actual_slug}"
    if (( DRY_RUN == 1 )); then
      plan "keep verified repo ${repo_name} at ${target_path}"
    else
      info "repo present     : ${repo_name} — ${purpose}"
    fi
    continue
  fi

  if [[ -e "${target_path}" ]]; then
    fail "repository target exists but is not a git checkout: ${target_path}"
  fi

  if (( CLONE_MISSING == 0 )); then
    warn "missing required ${TIER} repo with --no-clone: ${target_path}"
    missing_repo_count=$(( missing_repo_count + 1 ))
    continue
  fi

  if [[ "${CLONE_PROTOCOL}" == "ssh" ]]; then
    clone_url="git@github.com:${github_slug}.git"
  else
    clone_url="https://github.com/${github_slug}.git"
  fi

  if (( DRY_RUN == 1 )); then
    plan "git clone ${clone_url} ${target_path}"
    continue
  fi

  mkdir -p "$(dirname "${target_path}")"
  info "cloning repo    : ${repo_name} — ${purpose}"
  git clone --origin origin "${clone_url}" "${target_path}"
done < "${REPO_CONFIG}"

(( selected_repo_count > 0 )) || fail "no repositories matched tier ${TIER}"
(( missing_repo_count == 0 )) || fail "${missing_repo_count} required repositories are missing"

if (( NO_SCHEDULER == 1 )); then
  info "scheduler       : skipped"
  exit 0
fi

SELECTED_JOB_NAMES=()
SELECTED_JOB_ACTIVATION=()
MANAGED_JOB_NAMES=()
MANAGED_JOB_ACTIVATION=()
NEEDS_AGENTIC_PROVIDER=0
NEEDS_ARCHILITY=0
NEEDS_GITHUB_CLI=0
NEEDS_CLOCKWORK=0
SELECTED_JOB_KEY='|'
seen_managed_job_names='|'

while IFS='|' read -r profiles job_name command_rel arguments_csv linux_installer installer_kind provider_env model_env env_slug schedule_kind interval_seconds weekdays hour minute startup_delay jitter launchd_network_host activation_kind; do
  [[ -n "${profiles}" ]] || continue
  case "${profiles}" in \#*) continue ;; esac

  validate_job_record \
    "${job_name}" "${command_rel}" "${arguments_csv}" "${linux_installer}" \
    "${installer_kind}" "${provider_env}" "${model_env}" "${env_slug}" \
    "${schedule_kind}" "${interval_seconds}" \
    "${weekdays}" "${hour}" "${minute}" "${startup_delay}" "${jitter}" \
    "${launchd_network_host}" "${activation_kind}"
  case "${seen_managed_job_names}" in
    *"|${job_name}|"*) fail "duplicate managed job: ${job_name}" ;;
  esac
  seen_managed_job_names="${seen_managed_job_names}${job_name}|"
  [[ -f "${REPO_ROOT}/${command_rel}" ]] || fail "missing workload for ${job_name}: ${REPO_ROOT}/${command_rel}"
  [[ -f "${REPO_ROOT}/${linux_installer}" ]] || fail "missing Linux installer for ${job_name}: ${REPO_ROOT}/${linux_installer}"

  MANAGED_JOB_NAMES+=("${job_name}")
  MANAGED_JOB_ACTIVATION+=("${activation_kind}")
  job_is_selected "${profiles}" "${job_name}" || continue

  SELECTED_JOB_NAMES+=("${job_name}")
  SELECTED_JOB_ACTIVATION+=("${activation_kind}")
  SELECTED_JOB_KEY="${SELECTED_JOB_KEY}${job_name}|"
  [[ "${provider_env}" != "-" ]] && NEEDS_AGENTIC_PROVIDER=1
  [[ "${installer_kind}" == "archility" ]] && NEEDS_ARCHILITY=1
  [[ "${installer_kind}" != "service" ]] && NEEDS_CLOCKWORK=1
  case "${job_name}" in ci-repair-agentic*) NEEDS_GITHUB_CLI=1 ;; esac
done < "${JOB_CONFIG}"

(( ${#SELECTED_JOB_NAMES[@]} > 0 )) || fail "no jobs matched tier ${TIER}"
if (( NEEDS_CLOCKWORK == 1 )); then
  [[ -n "${CLOCKWORK_REPO}" ]] || fail "selected jobs require the clockwork support repo"
fi
if (( NEEDS_ARCHILITY == 1 )); then
  [[ -n "${ARCHILITY_REPO}" ]] || fail "selected jobs require the archility support repo"
fi

has_provider=0
case "${PROVIDER}" in
  auto)
    for provider_candidate in codex claude copilot; do
      if command -v "${provider_candidate}" >/dev/null 2>&1; then
        has_provider=1
        break
      fi
    done
    ;;
  *)
    command -v "${PROVIDER}" >/dev/null 2>&1 && has_provider=1
    ;;
esac

if (( NEEDS_AGENTIC_PROVIDER == 1 && has_provider == 0 )); then
  if (( ACTIVATE == 1 )); then
    fail "no usable ${PROVIDER} provider CLI is installed"
  fi
  warn "no usable ${PROVIDER} provider CLI found; rendered agentic jobs will not run yet"
fi

if ! command -v python3 >/dev/null 2>&1; then
  if (( ACTIVATE == 1 )) || [[ "${PLATFORM}" == "linux" ]]; then
    fail "python3 not found"
  fi
  warn "python3 not found; portfolio and architecture jobs will not run yet"
fi

if (( NEEDS_GITHUB_CLI == 1 )); then
  for runtime_command in gh jq; do
    if ! command -v "${runtime_command}" >/dev/null 2>&1; then
      if (( ACTIVATE == 1 )); then
        fail "${runtime_command} not found; it is required by selected CI jobs"
      fi
      warn "${runtime_command} not found; selected CI jobs will not run yet"
    fi
  done
fi

if (( DRY_RUN == 0 )); then
  if [[ "${PLATFORM}" == "linux" ]]; then
    if (( ACTIVATE == 1 )); then
      command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"
      systemctl --user show-environment >/dev/null 2>&1 \
        || fail "systemd user manager is unavailable"
      for managed_job_name in "${MANAGED_JOB_NAMES[@]}"; do
        managed_load_state="$(
          systemctl --user show --property=LoadState --value \
            "${managed_job_name}.service" 2>/dev/null
        )" || fail "cannot inspect systemd job ${managed_job_name}.service"
        [[ -n "${managed_load_state}" ]] \
          || fail "systemd returned no load state for ${managed_job_name}.service"
        if [[ "${managed_load_state}" == "not-found" ]]; then
          continue
        fi
        managed_service_state="$(
          systemctl --user show --property=ActiveState --value \
            "${managed_job_name}.service" 2>/dev/null
        )" || fail "cannot inspect active state for ${managed_job_name}.service"
        [[ -n "${managed_service_state}" ]] \
          || fail "systemd returned no active state for ${managed_job_name}.service"
        case "${managed_service_state}" in
          inactive|failed) ;;
          *)
            fail "refusing to reconcile ${managed_service_state} systemd job ${managed_job_name}.service; stop it and rerun"
            ;;
        esac
      done
    fi
  else
    command -v plutil >/dev/null 2>&1 || fail "plutil not found"
    if (( ACTIVATE == 1 )); then
      command -v launchctl >/dev/null 2>&1 || fail "launchctl not found"
      launch_domain="gui/$(id -u)"
      launchctl print "${launch_domain}" >/dev/null 2>&1 \
        || fail "launchd user domain is unavailable: ${launch_domain}"
      for managed_job_name in "${MANAGED_JOB_NAMES[@]}"; do
        managed_label="io.github.casonk.traction-control.${managed_job_name}"
        if launchctl print "${launch_domain}/${managed_label}" 2>/dev/null | grep -q 'state = running'; then
          fail "refusing to reconcile running LaunchAgent ${managed_label}; stop it and rerun"
        fi
      done
    fi
  fi
fi

ARCHILITY_SHIM="${STATE_DIR}/bin/archility"

create_archility_shim() {
  if (( DRY_RUN == 1 )); then
    plan "write archility source-tree shim ${ARCHILITY_SHIM}"
    return
  fi
  [[ -d "${ARCHILITY_REPO}/src/archility" ]] || fail "archility source not found: ${ARCHILITY_REPO}"
  mkdir -p "$(dirname "${ARCHILITY_SHIM}")"
  {
    printf '%s\n' '#!/bin/sh'
    printf 'ARCHILITY_SOURCE_ROOT=%s\n' "$(shell_single_quote "${ARCHILITY_REPO}")"
    printf '%s\n' 'export PYTHONPATH="${ARCHILITY_SOURCE_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"'
    printf '%s\n' 'exec python3 -m archility "$@"'
  } > "${ARCHILITY_SHIM}"
  chmod 0755 "${ARCHILITY_SHIM}"
}

render_systemd_job() {
  local job_name="$1"
  local installer_rel="$2"
  local installer_kind="$3"
  local installer_path="${REPO_ROOT}/${installer_rel}"
  local args=(
    --render-only
    --unit-dir "${SYSTEMD_UNIT_DIR}"
    --portfolio-root "${PORTFOLIO_ROOT}"
  )

  [[ -f "${installer_path}" ]] || fail "missing Linux installer for ${job_name}: ${installer_path}"

  case "${installer_kind}" in
    basic)
      args+=(--clockwork-repo "${CLOCKWORK_REPO}")
      ;;
    agentic)
      args+=(--provider "${PROVIDER}" --model "${MODEL}" --clockwork-repo "${CLOCKWORK_REPO}")
      ;;
    archility)
      args+=(--archility-cmd "${ARCHILITY_SHIM}" --clockwork-repo "${CLOCKWORK_REPO}")
      ;;
    service)
      args+=(--provider "${PROVIDER}" --model "${MODEL}")
      ;;
    *) fail "unsupported installer kind for ${job_name}: ${installer_kind}" ;;
  esac

  if (( DRY_RUN == 1 )); then
    plan "render systemd job ${job_name} into ${SYSTEMD_UNIT_DIR}"
    return
  fi
  bash "${installer_path}" "${args[@]}"
}

archive_launchd_plist() {
  local plist_path="$1"
  local label="$2"
  local archive_path=""

  [[ -f "${plist_path}" ]] || return 0
  mkdir -p "${STATE_DIR}/backups/launchd-stale"
  archive_path="${STATE_DIR}/backups/launchd-stale/${label}.$(date +%Y%m%d-%H%M%S).${RANDOM}.plist"
  mv "${plist_path}" "${archive_path}"
  info "archived unselected LaunchAgent: ${archive_path}"
}

archive_systemd_artifacts() {
  local job_name="$1"
  local requested_suffix="${2:-all}"
  local suffix=""
  local unit_path=""
  local archive_path=""

  for suffix in service timer; do
    if [[ "${requested_suffix}" != "all" && "${requested_suffix}" != "${suffix}" ]]; then
      continue
    fi
    unit_path="${SYSTEMD_UNIT_DIR}/${job_name}.${suffix}"
    [[ -f "${unit_path}" ]] || continue
    mkdir -p "${STATE_DIR}/backups/systemd-stale"
    archive_path="${STATE_DIR}/backups/systemd-stale/${job_name}.$(date +%Y%m%d-%H%M%S).${RANDOM}.${suffix}"
    mv "${unit_path}" "${archive_path}"
    info "archived unselected systemd unit: ${archive_path}"
  done
}

stop_disable_systemd_timer() {
  local job_name="$1"
  local timer_requires_stop=0

  (( ACTIVATE == 1 )) || return 0
  if systemctl --user is-active "${job_name}.timer" >/dev/null 2>&1; then
    timer_requires_stop=1
  fi
  if systemctl --user is-enabled "${job_name}.timer" >/dev/null 2>&1; then
    timer_requires_stop=1
  fi
  if (( timer_requires_stop == 1 )); then
    systemctl --user disable --now "${job_name}.timer" \
      || fail "failed to disable timer ${job_name}.timer"
  fi
}

reconcile_unselected_jobs() {
  local managed_index=0
  local job_name=""
  local activation_kind=""
  local label=""
  local plist_path=""
  local launch_domain=""

  if (( DRY_RUN == 1 )); then
    while (( managed_index < ${#MANAGED_JOB_NAMES[@]} )); do
      job_name="${MANAGED_JOB_NAMES[$managed_index]}"
      managed_index=$(( managed_index + 1 ))
      if ! selected_job_contains "${job_name}"; then
        plan "deactivate and archive managed ${PLATFORM} job when present: ${job_name}"
      fi
    done
    managed_index=0
    while (( managed_index < ${#SELECTED_JOB_NAMES[@]} )); do
      job_name="${SELECTED_JOB_NAMES[$managed_index]}"
      activation_kind="${SELECTED_JOB_ACTIVATION[$managed_index]}"
      managed_index=$(( managed_index + 1 ))
      if [[ "${activation_kind}" == "service" ]]; then
        plan "deactivate and archive stale timer when present: ${job_name}.timer"
      fi
    done
    return
  fi

  if [[ "${PLATFORM}" == "macos" ]] && (( ACTIVATE == 1 )); then
    launch_domain="gui/$(id -u)"
  fi

  while (( managed_index < ${#MANAGED_JOB_NAMES[@]} )); do
    job_name="${MANAGED_JOB_NAMES[$managed_index]}"
    activation_kind="${MANAGED_JOB_ACTIVATION[$managed_index]}"
    managed_index=$(( managed_index + 1 ))
    selected_job_contains "${job_name}" && continue

    if [[ "${PLATFORM}" == "linux" ]]; then
      stop_disable_systemd_timer "${job_name}"
      archive_systemd_artifacts "${job_name}"
    else
      label="io.github.casonk.traction-control.${job_name}"
      plist_path="${LAUNCHD_DIR}/${label}.plist"
      if (( ACTIVATE == 1 )) \
        && launchctl print "${launch_domain}/${label}" >/dev/null 2>&1; then
        launchctl bootout "${launch_domain}/${label}" \
          || fail "failed to unload unselected LaunchAgent ${label}"
      fi
      archive_launchd_plist "${plist_path}" "${label}"
    fi
  done

  if [[ "${PLATFORM}" == "linux" ]]; then
    managed_index=0
    while (( managed_index < ${#SELECTED_JOB_NAMES[@]} )); do
      job_name="${SELECTED_JOB_NAMES[$managed_index]}"
      activation_kind="${SELECTED_JOB_ACTIVATION[$managed_index]}"
      managed_index=$(( managed_index + 1 ))
      if [[ "${activation_kind}" == "service" ]]; then
        stop_disable_systemd_timer "${job_name}"
        archive_systemd_artifacts "${job_name}" timer
      fi
    done
  fi

  if [[ "${PLATFORM}" == "linux" ]] && (( ACTIVATE == 1 )); then
    systemctl --user daemon-reload
  fi
}

toml_string() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  printf '"%s"' "${value}"
}

clockwork_install_launchd() {
  local manifest_path="$1"
  local output_dir="$2"
  if [[ -d "${CLOCKWORK_REPO}/src/clockwork" ]]; then
    PYTHONPATH="${CLOCKWORK_REPO}/src${PYTHONPATH:+:${PYTHONPATH}}" \
      python3 -m clockwork install \
      --manifest "${manifest_path}" --target launchd-user --unit-dir "${output_dir}"
  elif command -v clockwork >/dev/null 2>&1; then
    clockwork install \
      --manifest "${manifest_path}" --target launchd-user --unit-dir "${output_dir}"
  else
    fail "Clockwork launchd renderer is unavailable"
  fi
}

launchd_calendar_expression() {
  local weekdays="$1"
  local hour="$2"
  local minute="$3"
  local old_ifs="${IFS}"
  local weekday_value=""
  local weekday_name=""
  local weekday_names=""
  local weekday_values=()

  if [[ "${weekdays}" == "-" ]]; then
    printf '*-*-* %02d:%02d:00' "${hour}" "${minute}"
    return
  fi
  IFS=','
  read -r -a weekday_values <<< "${weekdays}"
  IFS="${old_ifs}"
  for weekday_value in "${weekday_values[@]}"; do
    case "${weekday_value}" in
      0) weekday_name="Sun" ;;
      1) weekday_name="Mon" ;;
      2) weekday_name="Tue" ;;
      3) weekday_name="Wed" ;;
      4) weekday_name="Thu" ;;
      5) weekday_name="Fri" ;;
      6) weekday_name="Sat" ;;
      *) fail "invalid launchd weekday: ${weekday_value}" ;;
    esac
    weekday_names="${weekday_names}${weekday_names:+,}${weekday_name}"
  done
  printf '%s *-*-* %02d:%02d:00' "${weekday_names}" "${hour}" "${minute}"
}

render_launchd_job() {
  local job_name="$1"
  local command_rel="$2"
  local arguments_csv="$3"
  local installer_kind="$4"
  local provider_env="$5"
  local model_env="$6"
  local env_slug="$7"
  local schedule_kind="$8"
  local interval_seconds="$9"
  shift 9
  local weekdays="$1"
  local hour="$2"
  local minute="$3"
  local startup_delay="$4"
  local jitter="$5"
  local launchd_network_host="$6"
  local label="io.github.casonk.traction-control.${job_name}"
  local plist_path="${LAUNCHD_DIR}/${label}.plist"
  local command_path="${REPO_ROOT}/${command_rel}"
  local runner_path="${REPO_ROOT}/scripts/run_traction_control_job.sh"
  local env_file=""
  local manifest_dir="${STATE_DIR}/rendered/clockwork-launchd"
  local manifest_path="${manifest_dir}/${job_name}.toml"
  local manifest_tmp=""
  local render_dir="${LAUNCHD_DIR}"
  local rendered_plist=""
  local raw_arg=""
  local expanded_arg=""
  local command_line=""
  local calendar_expression=""
  local launch_arg=""
  local launch_args=(/bin/bash "${runner_path}" --job "${job_name}" --schedule-kind "${schedule_kind}")
  local configured_args=()

  [[ -f "${command_path}" ]] || fail "missing workload for ${job_name}: ${command_path}"

  if [[ "${env_slug}" != "-" ]]; then
    env_file="${CONFIG_HOME_VALUE}/traction-control/${env_slug}.env"
  fi
  if [[ "${schedule_kind}" == "interval" ]]; then
    launch_args+=(
      --interval-seconds "${interval_seconds}"
      --retry-seconds "${LAUNCHD_POLL_SECONDS}"
      --startup-delay-seconds "${startup_delay}"
      --state-dir "${LAUNCHD_RUNTIME_STATE_DIR}"
    )
  fi
  launch_args+=(--jitter-seconds "${jitter}")
  if [[ "${launchd_network_host}" != "-" ]]; then
    launch_args+=(--network-host "${launchd_network_host}" --network-wait-seconds 300)
  fi
  launch_args+=(-- "${command_path}")

  if [[ "${arguments_csv}" != "-" ]]; then
    old_ifs="${IFS}"
    IFS=','
    read -r -a configured_args <<< "${arguments_csv}"
    IFS="${old_ifs}"
    for raw_arg in "${configured_args[@]}"; do
      expanded_arg="${raw_arg//__HOME__/${HOME}}"
      launch_args+=("${expanded_arg}")
    done
  fi
  for launch_arg in "${launch_args[@]}"; do
    command_line="${command_line}${command_line:+ }$(shell_single_quote "${launch_arg}")"
  done

  if (( DRY_RUN == 1 )); then
    plan "render launchd job ${job_name} through Clockwork into ${plist_path}"
    return
  fi

  mkdir -p "${manifest_dir}"
  chmod 0700 "${manifest_dir}"
  manifest_tmp="$(mktemp "${manifest_dir}/.${job_name}.XXXXXX")"
  {
    printf '%s\n' '# Generated by traction-control for Clockwork launchd rendering.'
    printf '%s\n' '# Stateful interval due/catch-up, jitter, and network readiness remain explicit runner arguments.'
    printf '%s\n' '[[jobs]]'
    printf 'name = '; toml_string "${job_name}"; printf '\n'
    printf 'launchd_label = '; toml_string "${label}"; printf '\n'
    printf 'description = '; toml_string "Traction Control agent job: ${job_name}"; printf '\n'
    printf '%s\n' 'scope = "user"'
    printf '%s\n' 'service_type = "oneshot"'
    printf 'working_directory = '; toml_string "${REPO_ROOT}"; printf '\n'
    printf 'exec_start = '; toml_string "${command_line}"; printf '\n'
    if [[ -n "${env_file}" ]]; then
      printf 'environment_files = ['; toml_string "-${env_file}"; printf ']\n'
    fi
    if [[ "${schedule_kind}" == "interval" ]]; then
      printf '%s\n' 'launchd_run_at_load = true'
    else
      printf '%s\n' 'launchd_run_at_load = false'
    fi
    printf '%s\n' '' '[jobs.environment]'
    printf 'PATH = '; toml_string "${LAUNCHD_PATH_VALUE}"; printf '\n'
    printf 'PORTFOLIO_ROOT = '; toml_string "${PORTFOLIO_ROOT}"; printf '\n'
    if [[ "${provider_env}" != "-" ]]; then
      printf '%s = ' "${provider_env}"; toml_string "${PROVIDER}"; printf '\n'
      printf '%s = ' "${model_env}"; toml_string "${MODEL}"; printf '\n'
    fi
    if [[ "${installer_kind}" == "archility" ]]; then
      printf 'ARCHILITY_CMD = '; toml_string "${ARCHILITY_SHIM}"; printf '\n'
    fi
    case "${schedule_kind}" in
      interval)
        printf '%s\n' '' '[jobs.timer]' 'kind = "interval"'
        printf 'on_unit_active_sec = "%ss"\n' "${LAUNCHD_POLL_SECONDS}"
        ;;
      calendar)
        calendar_expression="$(launchd_calendar_expression "${weekdays}" "${hour}" "${minute}")"
        printf '%s\n' '' '[jobs.timer]' 'kind = "calendar"'
        printf 'on_calendar = '; toml_string "${calendar_expression}"; printf '\n'
        ;;
      none) ;;
      *) fail "unsupported launchd schedule for ${job_name}: ${schedule_kind}" ;;
    esac
  } > "${manifest_tmp}"
  chmod 0600 "${manifest_tmp}"
  mv "${manifest_tmp}" "${manifest_path}"

  if (( ACTIVATE == 1 )); then
    render_dir="${LAUNCHD_CANDIDATE_DIR}"
  fi
  mkdir -p "${render_dir}"
  clockwork_install_launchd "${manifest_path}" "${render_dir}"
  rendered_plist="${render_dir}/${label}.plist"
  [[ -f "${rendered_plist}" ]] \
    || fail "Clockwork did not render the expected LaunchAgent: ${rendered_plist}"
  plutil -lint "${rendered_plist}" >/dev/null \
    || fail "Clockwork generated an invalid LaunchAgent: ${label}"

  return
}

TXN_LABELS=()
TXN_ORIGINAL_EXISTED=()
TXN_WAS_LOADED=()
TXN_ORIGINAL_OVERRIDE_STATE=()
TXN_ATTEMPTED_LABELS=()
TXN_MUTATED_OVERRIDE_LABELS=()
TXN_DIR=""
TXN_LAUNCH_DOMAIN=""

cleanup_launchd_transaction_dir() {
  local artifact=""
  [[ -n "${TXN_DIR}" && -d "${TXN_DIR}" ]] || return 0
  for artifact in "${TXN_DIR}"/original-*.plist; do
    [[ -e "${artifact}" ]] || continue
    rm -f "${artifact}"
  done
  rmdir "${TXN_DIR}" 2>/dev/null || true
}

launchd_override_state_from_snapshot() {
  local snapshot="$1"
  local label="$2"
  local line=""
  local normalized=""
  local found_state=""

  while IFS= read -r line; do
    case "${line}" in
      *"\"${label}\""*)
        [[ -z "${found_state}" ]] || return 1
        normalized="$(printf '%s' "${line}" | tr -d '[:space:]')"
        case "${normalized}" in
          "\"${label}\"=>true"|"\"${label}\"=>true,") found_state="disabled" ;;
          "\"${label}\"=>false"|"\"${label}\"=>false,") found_state="enabled" ;;
          *) return 1 ;;
        esac
        ;;
    esac
  done <<< "${snapshot}"

  printf '%s\n' "${found_state:-absent}"
}

transaction_override_state_for_label() {
  local wanted_label="$1"
  local index=0

  while (( index < ${#TXN_LABELS[@]} )); do
    if [[ "${TXN_LABELS[$index]}" == "${wanted_label}" ]]; then
      printf '%s\n' "${TXN_ORIGINAL_OVERRIDE_STATE[$index]}"
      return 0
    fi
    index=$(( index + 1 ))
  done
  return 1
}

verify_launchd_override_snapshot() {
  local disabled_state=""
  local actual_state=""
  local index=0

  disabled_state="$(launchctl print-disabled "${TXN_LAUNCH_DOMAIN}")" || return 1
  while (( index < ${#TXN_LABELS[@]} )); do
    actual_state="$(
      launchd_override_state_from_snapshot "${disabled_state}" "${TXN_LABELS[$index]}"
    )" || return 1
    [[ "${actual_state}" == "${TXN_ORIGINAL_OVERRIDE_STATE[$index]}" ]] || return 1
    index=$(( index + 1 ))
  done
}

rollback_launchd_transaction() {
  local index=0
  local label=""
  local plist_path=""
  local original_path=""
  local rollback_failed=0

  index=0
  while (( index < ${#TXN_ATTEMPTED_LABELS[@]} )); do
    label="${TXN_ATTEMPTED_LABELS[$index]}"
    launchctl bootout "${TXN_LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1 || true
    index=$(( index + 1 ))
  done

  index=0
  while (( index < ${#TXN_LABELS[@]} )); do
    label="${TXN_LABELS[$index]}"
    plist_path="${LAUNCHD_DIR}/${label}.plist"
    original_path="${TXN_DIR}/original-${index}.plist"
    rm -f "${plist_path}"
    if [[ "${TXN_ORIGINAL_EXISTED[$index]}" == "1" ]]; then
      cp -p "${original_path}" "${plist_path}" || rollback_failed=1
    fi
    index=$(( index + 1 ))
  done

  # A job can be loaded while carrying a persistent disabled override. Keep
  # every override that this transaction changed temporarily enabled until its
  # original loaded state has been restored, then put the explicit true entry
  # back. This also handles an `enable` command that failed after taking effect.
  index=0
  while (( index < ${#TXN_MUTATED_OVERRIDE_LABELS[@]} )); do
    label="${TXN_MUTATED_OVERRIDE_LABELS[$index]}"
    launchctl enable "${TXN_LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1 \
      || rollback_failed=1
    index=$(( index + 1 ))
  done

  index=0
  while (( index < ${#TXN_LABELS[@]} )); do
    if [[ "${TXN_WAS_LOADED[$index]}" == "1" ]]; then
      label="${TXN_LABELS[$index]}"
      plist_path="${LAUNCHD_DIR}/${label}.plist"
      if ! launchctl print "${TXN_LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1; then
        launchctl bootstrap "${TXN_LAUNCH_DOMAIN}" "${plist_path}" >/dev/null 2>&1 \
          || rollback_failed=1
      fi
    fi
    index=$(( index + 1 ))
  done
  index=0
  while (( index < ${#TXN_MUTATED_OVERRIDE_LABELS[@]} )); do
    label="${TXN_MUTATED_OVERRIDE_LABELS[$index]}"
    launchctl disable "${TXN_LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1 \
      || rollback_failed=1
    index=$(( index + 1 ))
  done
  if ! verify_launchd_override_snapshot; then
    warn "launchd rollback could not verify the exact disabled-override map"
    rollback_failed=1
  fi
  return "${rollback_failed}"
}

activate_launchd_profile() {
  local disabled_state=""
  local index=0
  local label=""
  local job_name=""
  local plist_path=""
  local candidate_path=""
  local candidate_label=""
  local original_path=""
  local backup_dir=""
  local backup_path=""
  local archive_messages=()
  local install_tmp=""
  local override_state=""
  local rollback_message=""

  TXN_LAUNCH_DOMAIN="gui/$(id -u)"

  index=0
  while (( index < ${#SELECTED_JOB_NAMES[@]} )); do
    job_name="${SELECTED_JOB_NAMES[$index]}"
    label="io.github.casonk.traction-control.${job_name}"
    candidate_path="${LAUNCHD_CANDIDATE_DIR}/${label}.plist"
    [[ -f "${candidate_path}" && ! -L "${candidate_path}" ]] \
      || fail "missing regular pre-rendered LaunchAgent candidate: ${candidate_path}"
    plutil -lint "${candidate_path}" >/dev/null \
      || fail "pre-rendered LaunchAgent candidate is invalid: ${label}"
    candidate_label="$(plutil -extract Label raw -o - "${candidate_path}" 2>/dev/null)" \
      || fail "cannot read Label from pre-rendered LaunchAgent candidate: ${candidate_path}"
    [[ "${candidate_label}" == "${label}" ]] \
      || fail "pre-rendered LaunchAgent Label mismatch: expected ${label}, found ${candidate_label}"
    index=$(( index + 1 ))
  done

  disabled_state="$(launchctl print-disabled "${TXN_LAUNCH_DOMAIN}")" \
    || fail "cannot inspect launchd disabled overrides for ${TXN_LAUNCH_DOMAIN}"
  mkdir -p "${STATE_DIR}/backups"
  TXN_DIR="$(mktemp -d "${STATE_DIR}/backups/.launchd-transaction.XXXXXX")"
  chmod 0700 "${TXN_DIR}"
  TXN_LABELS=()
  TXN_ORIGINAL_EXISTED=()
  TXN_WAS_LOADED=()
  TXN_ORIGINAL_OVERRIDE_STATE=()
  TXN_ATTEMPTED_LABELS=()
  TXN_MUTATED_OVERRIDE_LABELS=()

  index=0
  while (( index < ${#MANAGED_JOB_NAMES[@]} )); do
    job_name="${MANAGED_JOB_NAMES[$index]}"
    label="io.github.casonk.traction-control.${job_name}"
    plist_path="${LAUNCHD_DIR}/${label}.plist"
    original_path="${TXN_DIR}/original-${index}.plist"
    TXN_LABELS+=("${label}")
    if [[ -L "${plist_path}" ]]; then
      cleanup_launchd_transaction_dir
      fail "refusing managed LaunchAgent symlink: ${plist_path}"
    fi
    if [[ -f "${plist_path}" ]]; then
      TXN_ORIGINAL_EXISTED+=(1)
      cp -p "${plist_path}" "${original_path}" \
        || { cleanup_launchd_transaction_dir; fail "cannot snapshot LaunchAgent ${label}"; }
    elif [[ -e "${plist_path}" ]]; then
      cleanup_launchd_transaction_dir
      fail "managed LaunchAgent path is not a regular file: ${plist_path}"
    else
      TXN_ORIGINAL_EXISTED+=(0)
    fi
    if launchctl print "${TXN_LAUNCH_DOMAIN}/${label}" 2>/dev/null \
      | grep -q 'state = running'; then
      cleanup_launchd_transaction_dir
      fail "refusing to reconcile running LaunchAgent ${label}; stop it and rerun"
    fi
    if launchctl print "${TXN_LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1; then
      TXN_WAS_LOADED+=(1)
      if [[ "${TXN_ORIGINAL_EXISTED[$index]}" != "1" ]]; then
        cleanup_launchd_transaction_dir
        fail "cannot safely roll back loaded LaunchAgent without its plist: ${label}"
      fi
    else
      TXN_WAS_LOADED+=(0)
    fi
    override_state="$(launchd_override_state_from_snapshot "${disabled_state}" "${label}")" \
      || { cleanup_launchd_transaction_dir; fail "cannot safely parse launchd disabled override for ${label}"; }
    TXN_ORIGINAL_OVERRIDE_STATE+=("${override_state}")
    index=$(( index + 1 ))
  done

  mkdir -p "${STATE_DIR}/backups/launchd" "${STATE_DIR}/backups/launchd-stale"
  index=0
  while (( index < ${#TXN_LABELS[@]} )); do
    if [[ "${TXN_ORIGINAL_EXISTED[$index]}" == "1" ]]; then
      label="${TXN_LABELS[$index]}"
      job_name="${label##*.}"
      if selected_job_contains "${job_name}"; then
        backup_dir="${STATE_DIR}/backups/launchd"
      else
        backup_dir="${STATE_DIR}/backups/launchd-stale"
      fi
      backup_path="${backup_dir}/${label}.$(date +%Y%m%d-%H%M%S).${RANDOM}.plist"
      cp -p "${TXN_DIR}/original-${index}.plist" "${backup_path}" \
        || { cleanup_launchd_transaction_dir; fail "cannot preserve LaunchAgent backup ${label}"; }
      if ! selected_job_contains "${job_name}"; then
        archive_messages+=("${backup_path}")
      fi
    fi
    index=$(( index + 1 ))
  done

  index=0
  while (( index < ${#TXN_LABELS[@]} )); do
    if [[ "${TXN_WAS_LOADED[$index]}" == "1" ]]; then
      label="${TXN_LABELS[$index]}"
      if ! launchctl bootout "${TXN_LAUNCH_DOMAIN}/${label}"; then
        rollback_message="failed to unload existing LaunchAgent ${label}"
        rollback_launchd_transaction || rollback_message="${rollback_message}; rollback was incomplete"
        cleanup_launchd_transaction_dir
        fail "${rollback_message}"
      fi
    fi
    index=$(( index + 1 ))
  done

  mkdir -p "${LAUNCHD_DIR}" "${HOME}/Library/Logs/Clockwork"
  chmod 0700 "${HOME}/Library/Logs/Clockwork"
  index=0
  while (( index < ${#TXN_LABELS[@]} )); do
    label="${TXN_LABELS[$index]}"
    plist_path="${LAUNCHD_DIR}/${label}.plist"
    rm -f "${plist_path}"
    if selected_job_contains "${label##*.}"; then
      candidate_path="${LAUNCHD_CANDIDATE_DIR}/${label}.plist"
      install_tmp="$(mktemp "${LAUNCHD_DIR}/.${label}.XXXXXX")"
      if ! cp -p "${candidate_path}" "${install_tmp}" \
        || ! mv "${install_tmp}" "${plist_path}"; then
        rm -f "${install_tmp}"
        rollback_message="failed to install LaunchAgent candidate ${label}"
        rollback_launchd_transaction || rollback_message="${rollback_message}; rollback was incomplete"
        cleanup_launchd_transaction_dir
        fail "${rollback_message}"
      fi
    fi
    index=$(( index + 1 ))
  done

  index=0
  while (( index < ${#SELECTED_JOB_NAMES[@]} )); do
    job_name="${SELECTED_JOB_NAMES[$index]}"
    label="io.github.casonk.traction-control.${job_name}"
    plist_path="${LAUNCHD_DIR}/${label}.plist"
    TXN_ATTEMPTED_LABELS+=("${label}")
    override_state="$(transaction_override_state_for_label "${label}")" \
      || { rollback_message="missing launchd override snapshot for ${label}"; rollback_launchd_transaction || rollback_message="${rollback_message}; rollback was incomplete"; cleanup_launchd_transaction_dir; fail "${rollback_message}"; }
    if [[ "${override_state}" == "disabled" ]]; then
      TXN_MUTATED_OVERRIDE_LABELS+=("${label}")
      if ! launchctl enable "${TXN_LAUNCH_DOMAIN}/${label}"; then
        rollback_message="failed to enable LaunchAgent ${label}"
        rollback_launchd_transaction || rollback_message="${rollback_message}; rollback was incomplete"
        cleanup_launchd_transaction_dir
        fail "${rollback_message}"
      fi
    fi
    if ! launchctl bootstrap "${TXN_LAUNCH_DOMAIN}" "${plist_path}"; then
      rollback_message="failed to activate LaunchAgent ${label}"
      rollback_launchd_transaction || rollback_message="${rollback_message}; rollback was incomplete"
      cleanup_launchd_transaction_dir
      fail "${rollback_message}"
    fi
    index=$(( index + 1 ))
  done

  index=0
  while (( index < ${#archive_messages[@]} )); do
    backup_path="${archive_messages[$index]}"
    info "archived unselected LaunchAgent: ${backup_path}"
    index=$(( index + 1 ))
  done
  cleanup_launchd_transaction_dir
}

if (( NEEDS_ARCHILITY == 1 )); then
  create_archility_shim
fi

if (( DRY_RUN == 0 )); then
  if [[ "${PLATFORM}" == "linux" ]]; then
    mkdir -p "${SYSTEMD_UNIT_DIR}"
  elif (( ACTIVATE == 1 )); then
    mkdir -p "${STATE_DIR}/rendered"
    LAUNCHD_CANDIDATE_DIR="$(mktemp -d "${STATE_DIR}/rendered/.launchd-candidates.XXXXXX")"
    chmod 0700 "${LAUNCHD_CANDIDATE_DIR}"
  fi
fi

if [[ "${PLATFORM}" == "linux" ]] || (( DRY_RUN == 1 )); then
  reconcile_unselected_jobs
fi

while IFS='|' read -r profiles job_name command_rel arguments_csv linux_installer installer_kind provider_env model_env env_slug schedule_kind interval_seconds weekdays hour minute startup_delay jitter launchd_network_host activation_kind; do
  [[ -n "${profiles}" ]] || continue
  case "${profiles}" in \#*) continue ;; esac
  job_is_selected "${profiles}" "${job_name}" || continue

  if [[ "${PLATFORM}" == "linux" ]]; then
    render_systemd_job "${job_name}" "${linux_installer}" "${installer_kind}"
  else
    render_launchd_job \
      "${job_name}" "${command_rel}" "${arguments_csv}" "${installer_kind}" \
      "${provider_env}" "${model_env}" "${env_slug}" "${schedule_kind}" \
      "${interval_seconds}" "${weekdays}" "${hour}" "${minute}" \
      "${startup_delay}" "${jitter}" "${launchd_network_host}"
  fi
done < "${JOB_CONFIG}"

if [[ "${PLATFORM}" == "macos" ]] && (( DRY_RUN == 0 )); then
  if (( ACTIVATE == 1 )); then
    activate_launchd_profile
  else
    reconcile_unselected_jobs
  fi
fi

if [[ "${PLATFORM}" == "linux" ]] && (( ACTIVATE == 1 && DRY_RUN == 0 )); then
  systemctl --user daemon-reload
  job_index=0
  while (( job_index < ${#SELECTED_JOB_NAMES[@]} )); do
    job_name="${SELECTED_JOB_NAMES[$job_index]}"
    activation_kind="${SELECTED_JOB_ACTIVATION[$job_index]}"
    if [[ "${activation_kind}" == "timer" ]]; then
      systemctl --user enable --now "${job_name}.timer"
      systemctl --user restart "${job_name}.timer"
    else
      info "installed on-demand service: ${job_name}.service"
    fi
    job_index=$(( job_index + 1 ))
  done
fi

if (( DRY_RUN == 1 )); then
  info "dry-run complete; no files, repositories, or services were changed"
elif (( ACTIVATE == 1 )); then
  info "${TIER} profile rendered and activated (${#SELECTED_JOB_NAMES[@]} jobs)"
else
  info "${TIER} profile rendered but left inactive (${#SELECTED_JOB_NAMES[@]} jobs)"
  if [[ "${PLATFORM}" == "linux" ]]; then
    info "rendered units: ${SYSTEMD_UNIT_DIR}"
  else
    info "rendered LaunchAgents: ${LAUNCHD_DIR}"
  fi
fi
