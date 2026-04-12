#!/usr/bin/env bash
# check_github_push_ci.sh — summarize GitHub Actions push CI for a batch of commits
#
# Input is a tab-separated file with these columns:
#   repo_rel    repo_slug    branch    sha
#
# Blank lines and lines beginning with "#" are ignored.
# A header row matching the column names above is also ignored.
#
# Example:
#   repo_rel	repo_slug	branch	sha
#   util-repos/traction-control	casonk/traction-control	main	e272b52
#
# Output is TSV with one line per row:
#   success|failure|NO_CI|timeout|invalid    repo_rel    run_id|sha    sha    url|reason

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  bash scripts/check_github_push_ci.sh --input pushes.tsv [options]

Options:
  --input PATH                 TSV input file, or "-" to read from stdin
  --list-attempts N            How many times to look for a matching run (default: 10)
  --list-poll-seconds N        Seconds between run-list polls (default: 8)
  --run-poll-attempts N        How many times to poll an in-progress run (default: 60)
  --run-poll-seconds N         Seconds between run-view polls (default: 8)
  --fail-on-no-ci              Treat rows with no matching push run as failures
  -h, --help                   Show this help text

Input columns:
  repo_rel    repo_slug    branch    sha

Examples:
  bash scripts/check_github_push_ci.sh --input /tmp/pushes.tsv
  printf 'repo_rel\trepo_slug\tbranch\tsha\nutil-repos/traction-control\tcasonk/traction-control\tmain\te272b52\n' \
    | bash scripts/check_github_push_ci.sh --input -
EOF
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 2
}

require_integer() {
    local value="$1"
    local name="$2"
    [[ "$value" =~ ^[0-9]+$ ]] || die "${name} must be a non-negative integer"
}

trim_cr() {
    printf '%s' "${1%$'\r'}"
}

INPUT_PATH=""
LIST_ATTEMPTS=10
LIST_POLL_SECONDS=8
RUN_POLL_ATTEMPTS=60
RUN_POLL_SECONDS=8
FAIL_ON_NO_CI=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input)
            [[ $# -ge 2 ]] || die "--input requires a path"
            INPUT_PATH="$2"
            shift 2
            ;;
        --list-attempts)
            [[ $# -ge 2 ]] || die "--list-attempts requires a value"
            LIST_ATTEMPTS="$2"
            shift 2
            ;;
        --list-poll-seconds)
            [[ $# -ge 2 ]] || die "--list-poll-seconds requires a value"
            LIST_POLL_SECONDS="$2"
            shift 2
            ;;
        --run-poll-attempts)
            [[ $# -ge 2 ]] || die "--run-poll-attempts requires a value"
            RUN_POLL_ATTEMPTS="$2"
            shift 2
            ;;
        --run-poll-seconds)
            [[ $# -ge 2 ]] || die "--run-poll-seconds requires a value"
            RUN_POLL_SECONDS="$2"
            shift 2
            ;;
        --fail-on-no-ci)
            FAIL_ON_NO_CI=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n "$INPUT_PATH" ]] || die "--input is required"
require_integer "$LIST_ATTEMPTS" "--list-attempts"
require_integer "$LIST_POLL_SECONDS" "--list-poll-seconds"
require_integer "$RUN_POLL_ATTEMPTS" "--run-poll-attempts"
require_integer "$RUN_POLL_SECONDS" "--run-poll-seconds"

command -v gh >/dev/null 2>&1 || die "gh must be installed and authenticated"

check_one() {
    local repo_rel="$1"
    local repo_slug="$2"
    local branch="$3"
    local sha="$4"
    local run_id=""
    local status=""
    local conclusion=""
    local url=""
    local attempt=""

    if [[ ! "$sha" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
        printf 'invalid\t%s\t%s\t%s\tinvalid sha\n' "$repo_rel" "$repo_slug" "$sha"
        return 1
    fi

    for attempt in $(seq 1 "$LIST_ATTEMPTS"); do
        run_id="$(gh run list \
            --repo "$repo_slug" \
            --branch "$branch" \
            --event push \
            --limit 20 \
            --json databaseId,headSha \
            --jq ".[] | select(.headSha | startswith(\"$sha\")) | .databaseId" \
            | head -n 1)"
        if [[ -n "$run_id" ]]; then
            break
        fi
        sleep "$LIST_POLL_SECONDS"
    done

    if [[ -z "$run_id" ]]; then
        printf 'NO_CI\t%s\t%s\t%s\t-\n' "$repo_rel" "$repo_slug" "$sha"
        return "$FAIL_ON_NO_CI"
    fi

    for attempt in $(seq 1 "$RUN_POLL_ATTEMPTS"); do
        status="$(gh run view "$run_id" --repo "$repo_slug" --json status --jq '.status')"
        conclusion="$(gh run view "$run_id" --repo "$repo_slug" --json conclusion --jq '.conclusion // ""')"
        if [[ "$status" == "completed" ]]; then
            url="$(gh run view "$run_id" --repo "$repo_slug" --json url --jq '.url')"
            if [[ "$conclusion" == "success" ]]; then
                printf 'success\t%s\t%s\t%s\t%s\n' "$repo_rel" "$run_id" "$sha" "$url"
                return 0
            fi
            printf 'failure\t%s\t%s\t%s\t%s\n' "$repo_rel" "$run_id" "$sha" "$url"
            return 1
        fi
        sleep "$RUN_POLL_SECONDS"
    done

    printf 'timeout\t%s\t%s\t%s\t-\n' "$repo_rel" "$run_id" "$sha"
    return 1
}

if [[ "$INPUT_PATH" == "-" ]]; then
    INPUT_FILE="/dev/stdin"
else
    [[ -f "$INPUT_PATH" ]] || die "input file not found: $INPUT_PATH"
    INPUT_FILE="$INPUT_PATH"
fi

success_count=0
failure_count=0
no_ci_count=0
timeout_count=0
invalid_count=0
line_number=0

while IFS=$'\t' read -r raw_repo_rel raw_repo_slug raw_branch raw_sha extra || [[ -n "${raw_repo_rel:-}" || -n "${raw_repo_slug:-}" || -n "${raw_branch:-}" || -n "${raw_sha:-}" || -n "${extra:-}" ]]; do
    line_number=$(( line_number + 1 ))

    repo_rel="$(trim_cr "${raw_repo_rel:-}")"
    repo_slug="$(trim_cr "${raw_repo_slug:-}")"
    branch="$(trim_cr "${raw_branch:-}")"
    sha="$(trim_cr "${raw_sha:-}")"
    extra_field="$(trim_cr "${extra:-}")"

    [[ -z "$repo_rel" && -z "$repo_slug" && -z "$branch" && -z "$sha" && -z "$extra_field" ]] && continue
    [[ "$repo_rel" == \#* ]] && continue
    if [[ "$repo_rel" == "repo_rel" && "$repo_slug" == "repo_slug" && "$branch" == "branch" && "$sha" == "sha" ]]; then
        continue
    fi

    if [[ -n "$extra_field" || -z "$repo_rel" || -z "$repo_slug" || -z "$branch" || -z "$sha" ]]; then
        printf 'invalid\tline-%s\t-\t-\texpected 4 tab-separated columns\n' "$line_number"
        invalid_count=$(( invalid_count + 1 ))
        continue
    fi

    if result_line="$(check_one "$repo_rel" "$repo_slug" "$branch" "$sha")"; then
        :
    else
        :
    fi
    printf '%s\n' "$result_line"

    case "$result_line" in
        success$'\t'*)
            success_count=$(( success_count + 1 ))
            ;;
        failure$'\t'*)
            failure_count=$(( failure_count + 1 ))
            ;;
        NO_CI$'\t'*)
            no_ci_count=$(( no_ci_count + 1 ))
            ;;
        timeout$'\t'*)
            timeout_count=$(( timeout_count + 1 ))
            ;;
        invalid$'\t'*)
            invalid_count=$(( invalid_count + 1 ))
            ;;
    esac
done <"$INPUT_FILE"

printf 'summary\t-\t-\tsuccess=%s failure=%s no_ci=%s timeout=%s invalid=%s\n' \
    "$success_count" \
    "$failure_count" \
    "$no_ci_count" \
    "$timeout_count" \
    "$invalid_count"

if (( failure_count > 0 || timeout_count > 0 || invalid_count > 0 )); then
    exit 1
fi

if (( FAIL_ON_NO_CI == 1 && no_ci_count > 0 )); then
    exit 1
fi

exit 0
