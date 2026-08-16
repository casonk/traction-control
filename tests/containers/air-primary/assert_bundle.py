#!/usr/bin/env python3
"""Assertions for the real Air-primary coordinator Podman regression."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
from pathlib import Path

AIR_ADDRESS = "10.44.0.254"
MINI_ADDRESS = "10.44.0.241"
PRO_ADDRESS = "10.44.0.242"
WIREGUARD_INTERFACE = "utun7"
CLOCKWORK_PLIST = "io.github.casonk.clockwork.clockwork-web-macos.plist"
WIRING_PLIST = "dev.user.wiring-harness.macos-private-edge.plist"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_mode(path: Path, expected: int) -> None:
    actual = stat.S_IMODE(path.stat().st_mode)
    require(
        actual == expected, f"expected mode {expected:04o}, got {actual:04o}: {path}"
    )


def artifact_files(*roots: Path) -> list[Path]:
    return sorted(path for root in roots for path in root.rglob("*") if path.is_file())


def snapshot_paths(
    traction_generation: Path, snowbridge_generation: Path
) -> dict[str, str]:
    return {
        os.fspath(path): sha256(path)
        for path in artifact_files(traction_generation, snowbridge_generation)
    }


def assert_private_artifacts(
    traction_generation: Path, snowbridge_generation: Path
) -> None:
    exact_private_directories = (
        traction_generation,
        traction_generation / "inputs",
        traction_generation / "inputs/wiring",
        traction_generation / "logs",
        traction_generation / "outputs",
        traction_generation / "outputs/clockwork",
        traction_generation / "outputs/wiring",
        traction_generation / "outputs/wiring/logs",
        traction_generation / "runtime-home",
        snowbridge_generation,
    )
    for directory in exact_private_directories:
        require(
            directory.is_dir() and not directory.is_symlink(),
            f"missing private directory: {directory}",
        )
        require_mode(directory, 0o700)
    for root in (traction_generation, snowbridge_generation):
        for directory in (path for path in root.rglob("*") if path.is_dir()):
            require(
                not directory.is_symlink(),
                f"artifact directory must not be a symlink: {directory}",
            )
            require_mode(directory, 0o700)
    for path in artifact_files(traction_generation, snowbridge_generation):
        require(not path.is_symlink(), f"artifact must not be a symlink: {path}")
        require_mode(path, 0o600)


def assert_repository_evidence(
    manifest: dict, *, native_smb_enabled: bool = True
) -> None:
    repositories = manifest.get("repositories")
    require(
        isinstance(repositories, dict), "aggregate manifest lacks repository evidence"
    )
    require(
        set(repositories) == {"clockwork", "snowbridge", "wiring-harness"},
        "aggregate manifest has an unexpected sibling repository set",
    )
    for name, evidence in repositories.items():
        require(isinstance(evidence, dict), f"invalid repository evidence for {name}")
        head = evidence.get("head")
        require(
            isinstance(head, str)
            and re.fullmatch(r"[0-9a-f]{40,64}", head) is not None,
            f"invalid synthetic HEAD for {name}",
        )
        require(
            evidence.get("dirty") is False,
            f"synthetic {name} worktree is unexpectedly dirty",
        )
        repo = Path(evidence["path"])
        sources = evidence.get("renderer_sha256")
        if name == "snowbridge" and not native_smb_enabled:
            require(
                sources == {},
                "disabled native SMB unexpectedly recorded renderer hashes",
            )
            continue
        require(
            isinstance(sources, dict) and sources, f"missing renderer hashes for {name}"
        )
        for relative, expected_digest in sources.items():
            source = repo / relative
            require(
                source.is_file() and not source.is_symlink(),
                f"missing staged renderer: {source}",
            )
            require(
                sha256(source) == expected_digest, f"renderer digest mismatch: {source}"
            )


def assert_positive(util_repos_root: Path, private_root: Path) -> None:
    traction = util_repos_root / "traction-control"
    snowbridge = util_repos_root / "snowbridge"
    generation = traction / "artifacts/air-primary/generation-1"
    snow_generation = snowbridge / "artifacts/traction-control-air-primary/generation-1"
    manifest_path = generation / "manifest.json"
    preflight_path = generation / "preflight.json"

    manifest = load_json(manifest_path)
    preflight = load_json(preflight_path)
    require(manifest.get("schema_version") == 1, "unexpected aggregate schema")
    require(manifest.get("generation") == 1, "unexpected aggregate generation")
    require(
        manifest.get("activation") == "render-only", "aggregate activation mode widened"
    )
    require(
        manifest.get("activation_supported") is False,
        "aggregate claims activation support",
    )
    require(
        manifest.get("snowbridge_web_backend_8080_enabled") is True,
        "Snowbridge web role was not enabled",
    )
    require(
        manifest.get("network")
        == {
            "wireguard_interface": WIREGUARD_INTERFACE,
            "air_address": f"{AIR_ADDRESS}/32",
            "allowed_clients": [f"{MINI_ADDRESS}/32", f"{PRO_ADDRESS}/32"],
        },
        "aggregate network evidence changed",
    )
    require(preflight.get("ok") is True, "preflight did not report ready")
    require(
        preflight.get("activation") == "render-only",
        "preflight activation mode widened",
    )
    require(
        preflight.get("snowbridge_inventory") == "fixture",
        "preflight did not use fixture inventory",
    )
    require(
        preflight.get("snowbridge_web_backend_8080_enabled") is True,
        "preflight omitted Snowbridge web",
    )
    require(
        preflight.get("repositories") == manifest.get("repositories"),
        "repository evidence drifted",
    )
    assert_repository_evidence(manifest)

    recorded_artifacts = manifest.get("artifacts")
    require(
        isinstance(recorded_artifacts, dict), "aggregate artifact hashes are missing"
    )
    require(
        len(recorded_artifacts) == 6,
        "aggregate manifest must bind exactly six child artifacts",
    )
    for raw_path, expected_digest in recorded_artifacts.items():
        path = Path(raw_path)
        require(
            path.is_file() and not path.is_symlink(),
            f"recorded artifact is missing: {path}",
        )
        require(
            sha256(path) == expected_digest,
            f"recorded artifact digest mismatch: {path}",
        )

    assert_private_artifacts(generation, snow_generation)

    clockwork_path = generation / "outputs/clockwork" / CLOCKWORK_PLIST
    clockwork = plistlib.loads(clockwork_path.read_bytes())
    require(
        clockwork.get("Label") == CLOCKWORK_PLIST.removesuffix(".plist"),
        "wrong Clockwork label",
    )
    require(
        clockwork.get("WorkingDirectory") == os.fspath(util_repos_root / "clockwork"),
        "wrong Clockwork working directory",
    )
    environment = clockwork.get("EnvironmentVariables")
    require(isinstance(environment, dict), "Clockwork plist lacks environment")
    require(
        environment.get("CLOCKWORK_WEB_HOST") == "127.0.0.1",
        "Clockwork is not loopback-only",
    )
    require(
        environment.get("CLOCKWORK_WEB_PORT") == "5001",
        "Clockwork backend port changed",
    )
    require(
        environment.get("CLOCKWORK_WEB_AUTOGENERATE_CRON") == "0",
        "Clockwork cron autogeneration is enabled",
    )
    arguments = clockwork.get("ProgramArguments")
    require(
        isinstance(arguments, list) and arguments[0] == "/usr/bin/python3",
        "Clockwork did not use the safe env loader",
    )
    argument_text = "\n".join(str(value) for value in arguments)
    require(
        os.fspath(private_root / "clockwork.env") in argument_text,
        "Clockwork env file reference is missing",
    )

    snow_plan = load_json(snow_generation / "activation-plan.json")
    require(snow_plan.get("mode") == "render-only", "Snowbridge mode widened")
    require(
        snow_plan.get("activation_supported") is False,
        "Snowbridge claims activation support",
    )
    require(
        snow_plan.get("inventory_provenance") == "fixture",
        "Snowbridge did not use fixture inventory",
    )
    share = snow_plan.get("share", {})
    require(share.get("guest_access") is False, "Snowbridge permits guest access")
    require(
        share.get("read_only") is False, "Snowbridge share is unexpectedly read-only"
    )
    require(
        share.get("smb3_encryption_required") is True,
        "Snowbridge encryption requirement is missing",
    )
    boundary = snow_plan.get("wireguard_boundary", {})
    require(
        boundary.get("interface") == WIREGUARD_INTERFACE, "Snowbridge interface changed"
    )
    require(
        boundary.get("host_address") == f"{AIR_ADDRESS}/32",
        "Snowbridge host /32 changed",
    )
    require(
        boundary.get("allowed_client_addresses")
        == [f"{MINI_ADDRESS}/32", f"{PRO_ADDRESS}/32"],
        "Snowbridge client /32s changed",
    )
    pf_text = (snow_generation / "snowbridge-smb.pf").read_text(encoding="utf-8")
    for expected in (
        f'wg_if = "{WIREGUARD_INTERFACE}"',
        f'wg_host = "{AIR_ADDRESS}"',
        f'allowed_clients = "{{ {MINI_ADDRESS}, {PRO_ADDRESS} }}"',
        "pass in quick on $wg_if inet proto tcp from $allowed_clients to $wg_host port 445",
        "block drop in quick inet proto tcp from any to any port 445",
        "block drop in quick inet6 proto tcp from any to any port 445",
    ):
        require(expected in pf_text, f"Snowbridge PF artifact lacks: {expected}")

    wiring_dir = generation / "outputs/wiring"
    wiring = load_json(wiring_dir / "manifest.json")
    require(wiring.get("activation") == "render-only", "wiring-harness mode widened")
    require(
        wiring.get("wireguard_interface") == WIREGUARD_INTERFACE,
        "wiring interface attestation changed",
    )
    require(
        wiring.get("wireguard_bind") == f"{AIR_ADDRESS}/32",
        "wiring /32 attestation changed",
    )
    require(
        wiring.get("client_auth") == "require_and_verify", "wiring mTLS policy weakened"
    )
    require(
        wiring.get("caddy", {}).get("validated") is True,
        "wiring did not validate Caddy",
    )
    services = wiring.get("services")
    require(isinstance(services, list), "wiring service evidence is missing")
    require(
        [service.get("role") for service in services] == ["clockwork", "snowbridge"],
        "wiring role set changed",
    )
    require(
        [service.get("upstream") for service in services]
        == ["http://127.0.0.1:5001", "http://127.0.0.1:8080"],
        "wiring upstream set changed",
    )
    wiring_plist = plistlib.loads((wiring_dir / WIRING_PLIST).read_bytes())
    require(
        wiring_plist.get("Label") == WIRING_PLIST.removesuffix(".plist"),
        "wrong wiring LaunchAgent label",
    )

    caddyfile = (wiring_dir / "Caddyfile").read_text(encoding="utf-8")
    for expected in (
        f"https://{AIR_ADDRESS}:8443",
        f"https://{AIR_ADDRESS}:8444",
        f"bind {AIR_ADDRESS}",
        "reverse_proxy 127.0.0.1:5001",
        "reverse_proxy 127.0.0.1:8080",
        "mode require_and_verify",
        f"default_sni {AIR_ADDRESS}",
    ):
        require(expected in caddyfile, f"Caddyfile lacks: {expected}")
    require("bind 0.0.0.0" not in caddyfile, "Caddyfile contains an IPv4 wildcard bind")
    require("bind ::" not in caddyfile, "Caddyfile contains an IPv6 wildcard bind")

    logs = generation / "logs"
    expected_stdout = {
        "clockwork.stdout.log": "Wrote 1 LaunchAgent file(s)",
        "snowbridge.stdout.log": "rendered owner-only review plan",
        "wiring-harness.stdout.log": "rendered owner-only macOS edge",
    }
    for filename, marker in expected_stdout.items():
        require(
            marker in (logs / filename).read_text(encoding="utf-8"),
            f"real renderer marker missing: {filename}",
        )
    for filename in (
        "clockwork.stderr.log",
        "snowbridge.stderr.log",
        "wiring-harness.stderr.log",
    ):
        require(
            (logs / filename).read_text(encoding="utf-8") == "",
            f"real renderer wrote stderr: {filename}",
        )

    secret = os.environ.get("AIR_PRIMARY_TEST_SECRET", "")
    require(len(secret) >= 32, "test secret was not provided")
    secret_bytes = secret.encode("utf-8")
    require(
        secret not in argument_text, "Clockwork plist copied the environment secret"
    )
    for path in artifact_files(generation, snow_generation):
        require(
            secret_bytes not in path.read_bytes(),
            f"test secret leaked into artifact: {path}",
        )

    snapshot = snapshot_paths(generation, snow_generation)
    snapshot_path = private_root / "success-snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    snapshot_path.chmod(0o600)


def assert_unchanged(util_repos_root: Path, private_root: Path) -> None:
    generation = util_repos_root / "traction-control/artifacts/air-primary/generation-1"
    snow_generation = (
        util_repos_root
        / "snowbridge/artifacts/traction-control-air-primary/generation-1"
    )
    expected = load_json(private_root / "success-snapshot.json")
    actual = snapshot_paths(generation, snow_generation)
    require(
        actual == expected, "immutable rerender changed successful generation evidence"
    )
    require(
        not (generation / "failure-report.json").exists(),
        "successful rerender added failure evidence",
    )


def assert_failure(util_repos_root: Path, private_root: Path) -> None:
    del private_root
    traction_generation = (
        util_repos_root / "traction-control/artifacts/air-primary/generation-2"
    )
    snow_generation = (
        util_repos_root
        / "snowbridge/artifacts/traction-control-air-primary/generation-2"
    )
    report_path = traction_generation / "failure-report.json"
    report = load_json(report_path)
    require(report.get("ok") is False, "failed generation reports success")
    require(
        report.get("category") == "unsafe_configuration",
        "wrong failed-generation category",
    )
    require(
        not (traction_generation / "manifest.json").exists(),
        "failed generation has an aggregate manifest",
    )
    stderr_path = traction_generation / "logs/snowbridge.stderr.log"
    require(
        "PF boundary" in stderr_path.read_text(encoding="utf-8"),
        "Snowbridge failure reason is missing",
    )
    require(
        not (traction_generation / "logs/wiring-harness.stdout.log").exists(),
        "wiring ran after Snowbridge refusal",
    )
    require(
        not (traction_generation / "outputs/wiring").exists(),
        "wiring output exists after Snowbridge refusal",
    )
    require(
        not (snow_generation / "activation-plan.json").exists(),
        "unsafe Snowbridge plan was published",
    )
    require_mode(traction_generation, 0o700)
    for path in artifact_files(traction_generation):
        require_mode(path, 0o600)


def assert_native_disabled(util_repos_root: Path, private_root: Path) -> None:
    traction_generation = (
        util_repos_root / "traction-control/artifacts/air-primary/generation-3"
    )
    snow_generation = (
        util_repos_root
        / "snowbridge/artifacts/traction-control-air-primary/generation-3"
    )
    manifest = load_json(traction_generation / "manifest.json")
    preflight = load_json(traction_generation / "preflight.json")

    require(
        manifest.get("snowbridge_native_smb_enabled") is False,
        "aggregate manifest did not record native SMB as disabled",
    )
    disabled_reason = manifest.get("snowbridge_native_smb_disabled_reason")
    require(
        isinstance(disabled_reason, str)
        and "snowbridge.native_smb_enabled=false" in disabled_reason,
        "aggregate manifest lacks the explicit native-SMB-disabled reason",
    )
    require(
        manifest.get("snowbridge_web_backend_8080_enabled") is True,
        "native SMB disabled the independent Snowbridge web role",
    )
    require(
        preflight.get("snowbridge_inventory") == "disabled",
        "disabled native SMB still reported an inventory source",
    )
    require(
        preflight.get("snowbridge_native_smb_enabled") is False,
        "preflight did not record native SMB as disabled",
    )
    require(
        preflight.get("snowbridge_native_smb_disabled_reason") == disabled_reason,
        "preflight and aggregate disabled reasons differ",
    )
    require(
        preflight.get("snowbridge_web_backend_8080_enabled") is True,
        "preflight disabled the independent Snowbridge web role",
    )
    require(
        preflight.get("repositories") == manifest.get("repositories"),
        "repository evidence drifted in the disabled-native-SMB render",
    )
    assert_repository_evidence(manifest, native_smb_enabled=False)

    recorded_artifacts = manifest.get("artifacts")
    require(
        isinstance(recorded_artifacts, dict) and len(recorded_artifacts) == 4,
        "disabled native SMB must bind exactly four non-SMB child artifacts",
    )
    require(
        all("snowbridge" not in raw_path for raw_path in recorded_artifacts),
        "aggregate manifest recorded an SMB artifact while native SMB was disabled",
    )
    for raw_path, expected_digest in recorded_artifacts.items():
        path = Path(raw_path)
        require(path.is_file() and not path.is_symlink(), f"missing artifact: {path}")
        require(sha256(path) == expected_digest, f"artifact digest mismatch: {path}")

    inputs = traction_generation / "inputs"
    logs = traction_generation / "logs"
    require(
        not (inputs / "snowbridge-air-smb.toml").exists(),
        "disabled native SMB staged an SMB renderer input",
    )
    require(
        not (inputs / "snowbridge-inventory.json").exists(),
        "disabled native SMB staged an inventory fixture",
    )
    require(
        not any(logs.glob("snowbridge.*.log")),
        "disabled native SMB created Snowbridge child logs",
    )
    require(
        not snow_generation.exists(),
        "disabled native SMB created a Snowbridge artifact generation",
    )

    wiring_dir = traction_generation / "outputs/wiring"
    wiring = load_json(wiring_dir / "manifest.json")
    services = wiring.get("services")
    require(isinstance(services, list), "wiring service evidence is missing")
    require(
        [service.get("role") for service in services]
        == [
            "clockwork",
            "snowbridge",
        ],
        "disabled native SMB changed the independently enabled web role set",
    )
    require(
        [service.get("upstream") for service in services]
        == ["http://127.0.0.1:5001", "http://127.0.0.1:8080"],
        "disabled native SMB changed the independent web upstreams",
    )
    caddyfile = (wiring_dir / "Caddyfile").read_text(encoding="utf-8")
    require(
        "reverse_proxy 127.0.0.1:8080" in caddyfile,
        "disabled native SMB removed the Snowbridge web reverse proxy",
    )

    private_directories = (
        traction_generation,
        inputs,
        inputs / "wiring",
        logs,
        traction_generation / "outputs",
        traction_generation / "outputs/clockwork",
        wiring_dir,
        wiring_dir / "logs",
        traction_generation / "runtime-home",
    )
    for directory in private_directories:
        require(
            directory.is_dir() and not directory.is_symlink(),
            f"missing private directory: {directory}",
        )
        require_mode(directory, 0o700)
    for path in artifact_files(traction_generation):
        require(not path.is_symlink(), f"artifact must not be a symlink: {path}")
        require_mode(path, 0o600)

    secret = os.environ.get("AIR_PRIMARY_TEST_SECRET", "")
    require(len(secret) >= 32, "test secret was not provided")
    for path in artifact_files(traction_generation):
        require(
            secret.encode("utf-8") not in path.read_bytes(),
            f"test secret leaked into artifact: {path}",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("positive", "unchanged", "failure", "native-disabled")
    )
    parser.add_argument("--util-repos-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if arguments.command == "positive":
        assert_positive(arguments.util_repos_root, arguments.private_root)
    elif arguments.command == "unchanged":
        assert_unchanged(arguments.util_repos_root, arguments.private_root)
    elif arguments.command == "failure":
        assert_failure(arguments.util_repos_root, arguments.private_root)
    else:
        assert_native_disabled(arguments.util_repos_root, arguments.private_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
