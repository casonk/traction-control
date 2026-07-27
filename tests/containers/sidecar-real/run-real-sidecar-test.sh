#!/usr/bin/env bash
# Exercise the sidecar against real, isolated OpenSSH and Restic targets.

set -euo pipefail
umask 077

TEST_ROOT="/test"
KEY_ROOT="${TEST_ROOT}/keys"
CONTROL_ROOT="${TEST_ROOT}/control"
PORTFOLIO_ROOT="${TEST_ROOT}/portfolio"
CHECKOUT="${PORTFOLIO_ROOT}/public-sidecar"
SIDECAR="/opt/scripts/portfolio_sidecar.py"

HOSTED_HOST="${SIDECAR_HOSTED_HOST:?SIDECAR_HOSTED_HOST is required}"
MESH_1_ADDRESS="${SIDECAR_MESH_1_ADDRESS:?SIDECAR_MESH_1_ADDRESS is required}"
MESH_2_ADDRESS="${SIDECAR_MESH_2_ADDRESS:?SIDECAR_MESH_2_ADDRESS is required}"
MESH_3_ADDRESS="${SIDECAR_MESH_3_ADDRESS:?SIDECAR_MESH_3_ADDRESS is required}"

PRIVATE_PATH="${CONTROL_ROOT}/private.local.json"
PUBLIC_PATH="${CONTROL_ROOT}/public.local.json"
CATALOG_PATH="${CONTROL_ROOT}/portfolio.local.json"
POLICY_PATH="${CONTROL_ROOT}/policy.local.json"
TARGETS_PATH="${CONTROL_ROOT}/targets.local.json"
STATE_PATH="${CONTROL_ROOT}/state.local.json"
KNOWN_HOSTS="${CONTROL_ROOT}/known_hosts"

COMMON=(
  --private "${PRIVATE_PATH}"
  --public "${PUBLIC_PATH}"
  --catalog "${CATALOG_PATH}"
  --portfolio-root "${PORTFOLIO_ROOT}"
  --policy "${POLICY_PATH}"
  --targets "${TARGETS_PATH}"
  --state "${STATE_PATH}"
)

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf 'info: %s\n' "$*"
}

write_fixture_documents() {
  python3 - \
    "${CONTROL_ROOT}" \
    "${HOSTED_HOST}" \
    "${MESH_1_ADDRESS}" \
    "${MESH_2_ADDRESS}" \
    "${MESH_3_ADDRESS}" <<'PY'
import json
import shutil
import sys
from pathlib import Path

control = Path(sys.argv[1])
hosted_host, mesh_1, mesh_2, mesh_3 = sys.argv[2:]
registry_id = "sidecar-real-podman-synthetic-registry"
repository_id = "R_SIDECAR_REAL_PODMAN_SYNTHETIC"
repository_slug = "synthetic-owner/sidecar-real-podman"

control.mkdir(mode=0o700, parents=True, exist_ok=True)
control.chmod(0o700)
credentials = control / "credentials"
credentials.mkdir(mode=0o700, exist_ok=True)
credentials.chmod(0o700)

def write_json(name: str, payload: object) -> None:
    path = control / name
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

def write_secret(name: str, value: str) -> str:
    path = credentials / name
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return str(path)

write_json(
    "private.local.json",
    {
        "schema_version": 1,
        "registry_id": registry_id,
        "generation": 1,
        "visibility": "private",
        "repositories": [],
    },
)
write_json(
    "public.local.json",
    {
        "schema_version": 1,
        "registry_id": registry_id,
        "generation": 1,
        "visibility": "public",
        "repositories": [{"id": repository_id, "slug": repository_slug}],
    },
)
write_json(
    "portfolio.local.json",
    {
        "schema_version": 1,
        "registry_id": registry_id,
        "registry_generation": 1,
        "catalog_generation": 1,
        "repositories": [
            {
                "repository_id": repository_id,
                "relative_path": "public-sidecar",
                "lifecycle": "active",
                "sync_policy": "manual",
                "desired_presence": "checkout",
            }
        ],
    },
)
write_json(
    "policy.local.json",
    {
        "schema_version": 1,
        "registry_id": registry_id,
        "registry_generation": 1,
        "policy_generation": 1,
        "datasets": [
            {
                "dataset_id": "dataset-hosted-synthetic",
                "repository_id": repository_id,
                "selectors": ["ignored/hosted"],
                "tier": "hosted-encrypted",
                "adapter": "filesystem-static",
                "max_files": 20,
                "max_total_bytes": 1048576,
                "target_set_id": "targets-hosted-synthetic",
            },
            {
                "dataset_id": "dataset-mesh-synthetic",
                "repository_id": repository_id,
                "selectors": ["ignored/mesh"],
                "tier": "mesh-only",
                "adapter": "filesystem-static",
                "max_files": 20,
                "max_total_bytes": 1048576,
                "target_set_id": "targets-mesh-synthetic",
            },
        ],
    },
)

target_specs = (
    (
        "target-hosted-synthetic",
        hosted_host,
        None,
        "failure-domain-hosted-synthetic",
    ),
    (
        "target-mesh-1-synthetic",
        mesh_1,
        mesh_1,
        "failure-domain-mesh-1-synthetic",
    ),
    (
        "target-mesh-2-synthetic",
        mesh_2,
        mesh_2,
        "failure-domain-mesh-2-synthetic",
    ),
    (
        "target-mesh-3-synthetic",
        mesh_3,
        mesh_3,
        "failure-domain-mesh-3-synthetic",
    ),
)
targets = []
for target_id, host, mesh_address, failure_domain in target_specs:
    repository_file = write_secret(
        f"{target_id}.repository",
        f"sftp:sidecarbackup@{host}:/home/sidecarbackup/repository\n",
    )
    password_file = write_secret(
        f"{target_id}.password",
        f"restic-password-{target_id}\n",
    )
    identity_file = credentials / f"{target_id}.identity"
    shutil.copyfile("/test/keys/client_ed25519", identity_file)
    identity_file.chmod(0o600)
    targets.append(
        {
            "target_id": target_id,
            "repository_file": repository_file,
            "password_file": password_file,
            "identity_file": str(identity_file),
            "mesh_address": mesh_address,
            "failure_domain": failure_domain,
        }
    )

write_json(
    "targets.local.json",
    {
        "schema_version": 1,
        "registry_id": registry_id,
        "registry_generation": 1,
        "target_generation": 1,
        "target_sets": [
            {
                "target_set_id": "targets-hosted-synthetic",
                "tier": "hosted-encrypted",
                "required_acks": 1,
                "targets": [targets[0]],
            },
            {
                "target_set_id": "targets-mesh-synthetic",
                "tier": "mesh-only",
                "required_acks": 2,
                "targets": targets[1:],
            },
        ],
    },
)

known_hosts = control / "known_hosts"
host_entries = (
    (hosted_host, "/test/keys/hosted_host_ed25519.pub"),
    (mesh_1, "/test/keys/mesh_1_host_ed25519.pub"),
    (mesh_2, "/test/keys/mesh_2_host_ed25519.pub"),
    (mesh_3, "/test/keys/mesh_3_host_ed25519.pub"),
)
with known_hosts.open("w", encoding="utf-8") as stream:
    for host, public_key_path in host_entries:
        fields = Path(public_key_path).read_text(encoding="utf-8").split()
        stream.write(f"{host} {fields[0]} {fields[1]}\n")
known_hosts.chmod(0o600)
PY
}

initialize_checkout() {
  mkdir -p "${CHECKOUT}"
  git -C "${CHECKOUT}" init -q -b main
  git -C "${CHECKOUT}" config user.name "Synthetic Sidecar Test"
  git -C "${CHECKOUT}" config user.email "synthetic@example.invalid"
  git -C "${CHECKOUT}" remote add origin \
    https://github.com/synthetic-owner/sidecar-real-podman.git
  printf 'ignored/\n' > "${CHECKOUT}/.gitignore"
  printf '# Synthetic public checkout\n' > "${CHECKOUT}/README.md"
  git -C "${CHECKOUT}" add .gitignore README.md
  git -C "${CHECKOUT}" commit -qm "synthetic fixture"
  mkdir -p "${CHECKOUT}/ignored/hosted" "${CHECKOUT}/ignored/mesh/nested"
  printf 'hosted private fixture generation 1\n' \
    > "${CHECKOUT}/ignored/hosted/hosted.txt"
  printf 'mesh private fixture generation 1\n' \
    > "${CHECKOUT}/ignored/mesh/mesh.txt"
  printf 'nested mesh bytes\000\001\n' \
    > "${CHECKOUT}/ignored/mesh/nested/payload.bin"
}

assert_external_network_is_blocked() {
  python3 - <<'PY'
import socket

try:
    connection = socket.create_connection(("1.1.1.1", 443), timeout=1.0)
except OSError:
    pass
else:
    connection.close()
    raise SystemExit("isolated Podman network unexpectedly reached the internet")
PY
}

wait_for_target() {
  local host="$1"
  python3 - "${host}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
deadline = time.monotonic() + 20.0
while time.monotonic() < deadline:
    try:
        with socket.create_connection((host, 22), timeout=0.5):
            raise SystemExit(0)
    except OSError:
        time.sleep(0.2)
raise SystemExit(f"SFTP target {host} did not become ready")
PY
}

sftp_command() {
  local host="$1"
  local identity="$2"
  printf '%s' \
    "/usr/bin/ssh -F /dev/null" \
    " -o BatchMode=yes" \
    " -o StrictHostKeyChecking=yes" \
    " -o UserKnownHostsFile=${KNOWN_HOSTS}" \
    " -o GlobalKnownHostsFile=/dev/null" \
    " -o IdentitiesOnly=yes" \
    " -o IdentityAgent=none" \
    " -o IdentityFile=${identity}" \
    " -o PasswordAuthentication=no" \
    " -o KbdInteractiveAuthentication=no" \
    " -o PreferredAuthentications=publickey" \
    " -o ProxyCommand=none" \
    " -o ProxyJump=none" \
    " -o PermitLocalCommand=no" \
    " -o RemoteCommand=none" \
    " -o ClearAllForwardings=yes" \
    " -o RequestTTY=no" \
    " -l sidecarbackup ${host} -s sftp"
}

initialize_repositories() {
  local target_id host repository_file password_file identity_file command
  while IFS=$'\t' read -r \
    target_id host repository_file password_file identity_file; do
    command="$(sftp_command "${host}" "${identity_file}")"
    restic \
      --no-cache \
      --repository-file "${repository_file}" \
      --password-file "${password_file}" \
      -o "sftp.command=${command}" \
      init >/dev/null
    info "initialized real Restic repository for ${target_id}"
  done < <(
    python3 - "${TARGETS_PATH}" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
for target_set in document["target_sets"]:
    for target in target_set["targets"]:
        repository = open(target["repository_file"], encoding="utf-8").read().strip()
        host = repository.removeprefix("sftp:sidecarbackup@").split(":", 1)[0]
        print(
            target["target_id"],
            host,
            target["repository_file"],
            target["password_file"],
            target["identity_file"],
            sep="\t",
        )
PY
  )
}

preflight_portable_backup() {
  local target_id="target-hosted-synthetic"
  local repository_file="${CONTROL_ROOT}/credentials/${target_id}.repository"
  local password_file="${CONTROL_ROOT}/credentials/${target_id}.password"
  local identity_file="${CONTROL_ROOT}/credentials/${target_id}.identity"
  local command output
  command="$(sftp_command "${HOSTED_HOST}" "${identity_file}")"
  mkdir -p "${TEST_ROOT}/preflight/.portfolio-sidecar/payload"
  printf '{"fixture":"portable-preflight"}\n' \
    > "${TEST_ROOT}/preflight/.portfolio-sidecar/manifest.json"
  printf 'synthetic preflight payload\n' \
    > "${TEST_ROOT}/preflight/.portfolio-sidecar/payload/value.txt"
  output="$(
    cd "${TEST_ROOT}/preflight"
    { printf '.portfolio-sidecar\000'; } | restic \
      --no-cache \
      --repository-file "${repository_file}" \
      --password-file "${password_file}" \
      -o "sftp.command=${command}" \
      backup \
      --json \
      --no-scan \
      --tag sidecar-format=portable-files-v1 \
      --tag sidecar-dataset=preflight-synthetic \
      --tag sidecar-repository=preflight-synthetic \
      --files-from-raw -
  )"
  printf '%s\n' "${output}"
  grep -Eq '"snapshot_id":"[0-9a-f]{64}"' <<<"${output}" \
    || fail "real Restic portable-root preflight returned no full snapshot ID"
  rm -rf "${TEST_ROOT}/preflight"
  info "real Restic portable-root backup preflight passed"
}

run_backup() {
  python3 "${SIDECAR}" backup "${COMMON[@]}" \
    --restic /usr/bin/restic \
    --ssh /usr/bin/ssh \
    --known-hosts "${KNOWN_HOSTS}"
}

run_drill() {
  local evidence="$1"
  python3 "${SIDECAR}" drill "${COMMON[@]}" \
    --restic /usr/bin/restic \
    --ssh /usr/bin/ssh \
    --known-hosts "${KNOWN_HOSTS}" \
    --evidence "${evidence}"
}

assert_state() {
  local sequence="$1"
  local mesh_replicas="$2"
  python3 - "${STATE_PATH}" "${sequence}" "${mesh_replicas}" <<'PY'
import json
import re
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
expected_sequence = int(sys.argv[2])
expected_mesh_replicas = int(sys.argv[3])
datasets = {dataset["dataset_id"]: dataset for dataset in state["datasets"]}
assert state["state_generation"] == expected_sequence
assert set(datasets) == {
    "dataset-hosted-synthetic",
    "dataset-mesh-synthetic",
}
hosted = datasets["dataset-hosted-synthetic"]
mesh = datasets["dataset-mesh-synthetic"]
assert hosted["sequence"] == expected_sequence
assert mesh["sequence"] == expected_sequence
assert len(hosted["replicas"]) == 1
assert len(mesh["replicas"]) == expected_mesh_replicas
for dataset in (hosted, mesh):
    assert re.fullmatch(r"[0-9a-f]{64}", dataset["manifest_sha256"])
    for replica in dataset["replicas"]:
        assert re.fullmatch(r"[0-9a-f]{64}", replica["snapshot_id"])
PY
}

assert_evidence() {
  local evidence="$1"
  local state_generation="$2"
  local mesh_status="$3"
  local mesh_verified="$4"
  python3 - \
    "${evidence}" \
    "${state_generation}" \
    "${mesh_status}" \
    "${mesh_verified}" <<'PY'
import hashlib
import json
import os
import re
import stat
import sys

path = sys.argv[1]
state_generation = int(sys.argv[2])
mesh_status = sys.argv[3]
mesh_verified = int(sys.argv[4])
metadata = os.lstat(path)
assert stat.S_ISREG(metadata.st_mode)
assert stat.S_IMODE(metadata.st_mode) == 0o600
assert metadata.st_nlink == 1
evidence = json.load(open(path, encoding="utf-8"))
state = json.load(open("/test/control/state.local.json", encoding="utf-8"))
state_sha256 = hashlib.sha256(
    json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
assert evidence["schema_version"] == 1
assert evidence["evidence_type"] == "portfolio-sidecar-restore-drill"
assert evidence["manifest_format"] == "portable-files-v1"
assert evidence["registry_id"] == "sidecar-real-podman-synthetic-registry"
assert evidence["registry_generation"] == 1
assert evidence["policy_generation"] == 1
assert evidence["target_generation"] == 1
assert evidence["state_generation"] == state_generation
assert evidence["state_sha256"] == state_sha256
assert evidence["policy_sha256"] == state["policy_sha256"]
assert evidence["target_sha256"] == state["target_sha256"]
assert re.fullmatch(r"[0-9a-f]{64}", evidence["policy_sha256"])
assert re.fullmatch(r"[0-9a-f]{64}", evidence["target_sha256"])
datasets = {dataset["dataset_id"]: dataset for dataset in evidence["datasets"]}
assert set(datasets) == {
    "dataset-hosted-synthetic",
    "dataset-mesh-synthetic",
}
hosted = datasets["dataset-hosted-synthetic"]
mesh = datasets["dataset-mesh-synthetic"]

def assert_dataset(
    dataset,
    *,
    dataset_id,
    status,
    verified,
    configured,
    required,
    target_ids,
):
    assert set(dataset) == {
        "dataset_id",
        "sequence",
        "status",
        "verified_replicas",
        "recorded_replicas",
        "configured_replicas",
        "required_acks",
        "replicas",
    }
    assert dataset["dataset_id"] == dataset_id
    assert dataset["sequence"] == state_generation
    assert dataset["status"] == status
    assert dataset["verified_replicas"] == verified
    assert dataset["recorded_replicas"] == verified
    assert dataset["configured_replicas"] == configured
    assert dataset["required_acks"] == required
    replicas = dataset["replicas"]
    assert [replica["target_id"] for replica in replicas] == target_ids
    assert len(replicas) == configured
    for index, replica in enumerate(replicas):
        assert set(replica) == {"target_id", "snapshot_id", "status"}
        if index < verified:
            assert replica["status"] == "verified"
            assert re.fullmatch(r"[0-9a-f]{64}", replica["snapshot_id"])
        else:
            assert replica["status"] == "unrecorded"
            assert replica["snapshot_id"] is None

assert_dataset(
    hosted,
    dataset_id="dataset-hosted-synthetic",
    status="verified",
    verified=1,
    configured=1,
    required=1,
    target_ids=["target-hosted-synthetic"],
)
assert_dataset(
    mesh,
    dataset_id="dataset-mesh-synthetic",
    status=mesh_status,
    verified=mesh_verified,
    configured=3,
    required=2,
    target_ids=[
        "target-mesh-1-synthetic",
        "target-mesh-2-synthetic",
        "target-mesh-3-synthetic",
    ],
)
PY
}

generate_keys() {
  [[ ! -e "${KEY_ROOT}" ]] || fail "key fixture root already exists"
  mkdir -p "${KEY_ROOT}"
  chmod 0700 "${KEY_ROOT}"
  ssh-keygen -q -t ed25519 -N '' -C sidecar-synthetic-client \
    -f "${KEY_ROOT}/client_ed25519"
  local target
  for target in hosted mesh_1 mesh_2 mesh_3; do
    ssh-keygen -q -t ed25519 -N '' -C "sidecar-synthetic-${target}" \
      -f "${KEY_ROOT}/${target}_host_ed25519"
  done
  find "${KEY_ROOT}" -type f -name '*_ed25519' -exec chmod 0600 {} +
  find "${KEY_ROOT}" -type f -name '*.pub' -exec chmod 0644 {} +
  info "generated isolated synthetic client and SSH host keys"
}

baseline() {
  assert_external_network_is_blocked
  for target in \
    "${HOSTED_HOST}" \
    "${MESH_1_ADDRESS}" \
    "${MESH_2_ADDRESS}" \
    "${MESH_3_ADDRESS}"; do
    wait_for_target "${target}"
  done
  [[ ! -e "${CONTROL_ROOT}" ]] || fail "control fixture already exists"
  [[ ! -e "${PORTFOLIO_ROOT}" ]] || fail "portfolio fixture already exists"
  initialize_checkout
  write_fixture_documents
  initialize_repositories
  preflight_portable_backup
  python3 "${SIDECAR}" init-state "${COMMON[@]}"
  local output status
  set +e
  output="$(run_backup 2>&1)"
  status=$?
  set -e
  printf '%s\n' "${output}"
  [[ "${status}" -eq 0 ]] \
    || fail "full-replica sidecar backup returned ${status}, expected success"
  grep -Fq $'committed\tdataset-hosted-synthetic\tacknowledgements=1/1' \
    <<<"${output}" || fail "hosted backup did not fully commit"
  grep -Fq $'committed\tdataset-mesh-synthetic\tacknowledgements=3/3' \
    <<<"${output}" || fail "mesh backup did not fully commit"
  assert_state 1 3
  output="$(run_drill "${CONTROL_ROOT}/drill-baseline.local.json")"
  printf '%s\n' "${output}"
  grep -Fq $'verified\tdataset-hosted-synthetic\tverified=1/1\trequired=1' \
    <<<"${output}" || fail "hosted restore drill did not verify its replica"
  grep -Fq $'verified\tdataset-mesh-synthetic\tverified=3/3\trequired=2' \
    <<<"${output}" || fail "mesh restore drill did not verify all replicas"
  grep -Fq 'sidecar restore drill complete' <<<"${output}" \
    || fail "restore drill did not report completion"
  assert_evidence \
    "${CONTROL_ROOT}/drill-baseline.local.json" 1 verified 3
  info "L2 and three-replica L3 real backup/drill completed"
}

outage() {
  assert_external_network_is_blocked
  wait_for_target "${HOSTED_HOST}"
  wait_for_target "${MESH_1_ADDRESS}"
  wait_for_target "${MESH_2_ADDRESS}"
  printf 'hosted private fixture generation 2\n' \
    > "${CHECKOUT}/ignored/hosted/hosted.txt"
  printf 'mesh private fixture generation 2\n' \
    > "${CHECKOUT}/ignored/mesh/mesh.txt"
  local output status
  set +e
  output="$(run_backup 2>&1)"
  status=$?
  set -e
  printf '%s\n' "${output}"
  [[ "${status}" -eq 3 ]] \
    || fail "one-node outage backup returned ${status}, expected degraded status 3"
  grep -Fq $'committed\tdataset-hosted-synthetic\tacknowledgements=1/1' \
    <<<"${output}" || fail "hosted backup did not commit during mesh outage"
  grep -Fq $'committed-degraded\tdataset-mesh-synthetic\tacknowledgements=2/3' \
    <<<"${output}" || fail "mesh strict-majority backup did not commit degraded"
  assert_state 2 2
  set +e
  output="$(run_drill "${CONTROL_ROOT}/drill-outage.local.json" 2>&1)"
  status=$?
  set -e
  printf '%s\n' "${output}"
  [[ "${status}" -eq 3 ]] \
    || fail "one-node outage drill returned ${status}, expected degraded status 3"
  grep -Fq $'verified\tdataset-hosted-synthetic\tverified=1/1\trequired=1' \
    <<<"${output}" || fail "hosted restore drill failed during mesh outage"
  grep -Fq $'verified-degraded\tdataset-mesh-synthetic\tverified=2/3\trequired=2' \
    <<<"${output}" || fail "mesh restore drill did not retain strict-majority proof"
  grep -Fq 'restore drill was degraded' <<<"${output}" \
    || fail "restore drill did not report degraded status"
  assert_evidence \
    "${CONTROL_ROOT}/drill-outage.local.json" 2 verified-degraded 2
  info "L3 outage retained strict-majority backup and drill semantics"
}

case "${1:-}" in
  generate-keys)
    generate_keys
    ;;
  baseline)
    baseline
    ;;
  outage)
    outage
    ;;
  *)
    fail "usage: run-real-sidecar-test {generate-keys|baseline|outage}"
    ;;
esac
