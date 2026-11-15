#!/usr/bin/env python3
"""Check a repository against the portfolio's deployment-modes contract.

    check_deployment_modes.py --repo PATH [--kind job|worker|service|sandbox] [--json]

Static checks only: nothing is run and no cluster is needed. Each rule prints PASS, FAIL or WARN. A FAIL means the
repository breaks the contract (docs/deployment-modes.md); a WARN means something is a scaffold stub or optional
guidance is missing. Exit status is 1 when any rule FAILs, so it can gate review or CI.

The kind is detected from the files (``handover.sh`` is a job, ``bridge.sh`` with a drop-in is a sandbox, ``bridge.sh``
alone is a service, ``--replicas=0`` is a worker) unless ``--kind`` says otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


@dataclass
class Report:
    results: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, status: str, rule: str, detail: str = "") -> None:
        self.results.append((status, rule, detail))

    def check(self, condition: bool, rule: str, detail: str = "") -> bool:
        self.add(PASS if condition else FAIL, rule, "" if condition else detail)
        return condition

    @property
    def failed(self) -> bool:
        return any(status == FAIL for status, _, _ in self.results)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def detect_kind(repo: Path, deploy: str) -> str:
    k3s = repo / "deploy" / "k3s"
    if (k3s / "handover.sh").exists() or "handover.sh" in deploy:
        return "job"
    if (k3s / "bridge.sh").exists() or "bridge.sh" in deploy:
        return "sandbox" if re.search(r"DROPIN|drop-in", deploy) else "service"
    if "--replicas=0" in deploy:
        return "worker"
    return "unknown"


def is_ignored(repo: Path, rel: str) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", rel], capture_output=True, timeout=20, check=False
        )
        if (repo / ".git").exists():
            return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    return rel in read(repo / ".gitignore")


def position(text: str, needle: str) -> int:
    return text.find(needle)


def strip_quotes(text: str) -> str:
    """Remove "double" and 'single' quoted text, so words inside messages are not mistaken for commands."""
    return re.sub(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'', '""', text)


def k3s_up_section(deploy: str) -> str:
    """The `up:k3s)` case of deploy.sh, up to `down:k3s)`; empty when it is missing."""
    match = re.search(r"\n\s*up:k3s\)(.*?)\n\s*down:k3s\)", deploy, flags=re.S)
    return match.group(1) if match else ""


def check(repo: Path, kind: str | None = None) -> Report:
    r = Report()
    deploy_path = repo / "deploy" / "deploy.sh"
    deploy = read(deploy_path)
    if not r.check(deploy_path.is_file(), "deploy/deploy.sh exists", "the single entry point is missing"):
        return r
    r.check(os.access(deploy_path, os.X_OK), "deploy/deploy.sh is executable", "chmod +x it (and commit the mode)")
    kind = kind or detect_kind(repo, deploy)
    r.add(PASS, f"kind: {kind}")

    # ---- files the contract names ----
    example = read(repo / "config" / "deploy.example.env")
    prefix_match = re.search(r"\b([A-Z][A-Z0-9_]*)_DEPLOY_MODE\b", deploy)
    prefix = prefix_match.group(1) if prefix_match else ""
    r.check(bool(prefix), "deploy.sh names its <PREFIX>_DEPLOY_MODE variable", "no <PREFIX>_DEPLOY_MODE found")
    r.check(
        bool(prefix) and f"{prefix}_DEPLOY_MODE" in example and all(m in example for m in ("dev", "host", "k3s")),
        "config/deploy.example.env selects a mode and names dev, host and k3s",
        "the example config is missing or incomplete",
    )
    docs = read(repo / "docs" / "deployment.md")
    needed = ("`dev`", "`host`", "`k3s`", "Switching", "only the `k3s` mode needs a")
    r.check(
        all(n in docs for n in needed),
        "docs/deployment.md explains every mode, switching, and that only k3s needs a cluster",
        "missing: " + ", ".join(n for n in needed if n not in docs),
    )
    r.check("docs/deployment.md" in read(repo / "README.md"), "README.md links docs/deployment.md", "add the link")
    r.check(
        is_ignored(repo, "config/deploy.local.env"),
        "config/deploy.local.env is gitignored",
        "a repository once missed this rule; add it and check with git check-ignore",
    )
    r.check((repo / "tests" / "test_deploy_modes.py").is_file(), "tests/test_deploy_modes.py exists", "no contract tests")
    r.check((repo / "deploy" / "k3s" / "env_value.py").is_file(), "deploy/k3s/env_value.py exists (config read like systemd)", "")
    scripts = sorted((repo / "deploy").rglob("*.sh"))
    not_exec = [str(p.relative_to(repo)) for p in scripts if not os.access(p, os.X_OK)]
    r.check(not not_exec, "every script under deploy/ is executable", ", ".join(not_exec))

    # ---- deploy.sh: mode selection ----
    r.check("set -eEuo pipefail" in deploy, "deploy.sh uses set -eEuo pipefail (ERR traps must reach functions)", "use set -E, not just set -e")
    r.check('MODES="dev host k3s"' in deploy, "deploy.sh defines the three modes", "")
    r.check(
        "--mode" in deploy and "config/deploy.local.env" in deploy and bool(prefix),
        "mode comes from --mode, the environment, then config/deploy.local.env",
        "selection order is incomplete",
    )
    r.check(
        'mode="dev"' in deploy and "default" in deploy,
        "the default mode is dev",
        "a fresh checkout must run with no systemd and no cluster",
    )
    r.check(
        "unknown deployment mode" in deploy and re.search(r"unknown deployment mode.*\n?.*exit 2", deploy) is not None,
        "an unknown mode exits 2 and names its source",
        "an unknown mode must be an error, never a silent fallback",
    )
    env_value_lines = [line for line in code_lines(deploy) if "env_value.py" in line]
    r.check(
        bool(env_value_lines) and not any("|| true" in line for line in env_value_lines),
        "reading the config file never hides a failure (no `|| true`)",
        "a failed config read must stop the script, not pick dev",
    )
    host_p = [line.strip() for line in code_lines(deploy + "\n".join(read(p) for p in scripts if p != deploy_path)) if "python3 -P" in line and " exec " not in line]
    r.check(not host_p, "host scripts use python3 -I, not -P (3.11+)", "; ".join(host_p[:2]))
    r.check(
        any("python3 -I" in line for line in env_value_lines),
        "the config is read with python3 -I",
        "",
    )

    # ---- deploy.sh: the k3s path ----
    r.check(
        "dev and host modes need neither" in deploy and "10.43.0.50" in deploy,
        "k3s up checks kubectl, podman and the registry first and says dev and host need none of them",
        "",
    )
    steps = [position(deploy, s) for s in ("build-image.sh", "apply.sh", "verify.sh")]
    r.check(
        all(i >= 0 for i in steps) and steps == sorted(steps),
        "k3s up runs build-image, apply, then verify, in that order",
        "",
    )
    r.check(
        "deploy-mode" in deploy and "set_marker" in deploy and "clear_marker" in deploy,
        "a marker records which mode last held the work",
        "",
    )
    r.check(
        re.search(r"down:k3s\)[^\n]*--mode host up", deploy) is not None,
        "k3s down is host up",
        "rollback must be one command, the same one that switches",
    )
    build = read(repo / "deploy" / "k3s" / "build-image.sh")
    if build:
        r.check("ALLOW_DIRTY" in build and "git" in build, "build-image.sh refuses uncommitted source", "the pod would not match the host")
    # Messages may *tell the user* to run sudo; a script must not *call* it. Quoted text is therefore ignored.
    sudo = [p.name for p in scripts if re.search(r"\bsudo\b", strip_quotes("\n".join(code_lines(read(p)))))]
    r.check(not sudo, "deploy scripts never invoke sudo (they may tell the user to)", ", ".join(sudo))

    # ---- deploy.sh: safe hand-over, by kind (looked for only inside the `k3s up` section) ----
    up = k3s_up_section(deploy)
    r.check(bool(up), "deploy.sh has a k3s up section", "no `up:k3s)` case found")
    if kind == "job":
        r.check(
            0 <= up.find("verify.sh") < up.find("disable --now") < up.find("pod-on"),
            "job: the host timer is disabled and the CronJob started only after verify",
            "",
        )
    elif kind == "worker":
        r.check("trap restore ERR" in up, "worker: a failed deploy restores the host copy (ERR trap)", "")
        r.check(
            0 <= up.find("disable --now") < up.find("build-image.sh"),
            "worker: the host copy stops before anything is built or applied",
            "two workers would do the work twice",
        )
    elif kind == "service":
        r.check(
            0 <= up.find("verify.sh") < up.find('bridge.sh" on'),
            "service: the bridge comes up only after verify",
            "",
        )
        r.check("trap" in up and 'bridge.sh" off' in up, "service: a bridge that fails to come up puts the host service back", "")
    elif kind == "sandbox":
        r.check("trap restore ERR" in up, "sandbox: a failure restores in-process work (ERR trap)", "")
        bridge_on, verify_at, dropin_call = up.find("bridge on"), up.find("verify.sh"), up.rfind("install_dropin")
        r.check(
            0 <= bridge_on < verify_at < dropin_call,
            "sandbox: the bridge is up and verified before the service is pointed at it",
            "the service must never see a dead port",
        )
    else:
        r.add(WARN, "kind not recognised", "pass --kind to get the hand-over checks")

    # ---- k3s files that are still scaffold stubs, and the manifests ----
    for name in ("apply.sh", "verify.sh", "prepare-host.sh"):
        path = repo / "deploy" / "k3s" / name
        if path.exists() and "scaffold stub" in read(path):
            r.add(WARN, f"deploy/k3s/{name} is still a scaffold stub", "implement it before relying on k3s mode")
    verify = read(repo / "deploy" / "k3s" / "verify.sh")
    if verify and "scaffold stub" not in verify:
        r.check(
            "exit 2" in verify and "nothing was verified" in verify,
            "verify.sh exits 2 and says so when it could not verify anything",
            "a verifier must be able to fail",
        )
    manifests = sorted((repo / "deploy" / "k3s" / "manifests").glob("*.y*ml*")) if (repo / "deploy" / "k3s" / "manifests").is_dir() else []
    if manifests:
        text = "\n".join(read(m) for m in manifests)
        r.check("pod-security.kubernetes.io/enforce: restricted" in text, "the namespace enforces the restricted pod-security level", "")
        uncommented = re.sub(r"#.*", "", text)
        mounts_host_files = "local:" in uncommented or "hostPath" in uncommented
        # fsGroup recursively re-groups every file on a volume that supports it: fatal for a pod that mounts the
        # owner's real files, harmless (and needed to read root-owned Secret files) for one that mounts none.
        r.check(
            "fsGroup" not in uncommented or not mounts_host_files,
            "no fsGroup on a pod that mounts host files (it would re-group them)",
            "remove fsGroup, or stop mounting host folders into this pod",
        )
        r.check("hostPath" not in uncommented, "no hostPath volumes (use a local PersistentVolume)", "")
        if "kind: CronJob" in text:
            r.check(re.search(r"suspend:\s*true", text) is not None, "a CronJob is created suspended", "it would race the host timer")
        r.check("CiliumNetworkPolicy" in text, "a Cilium network policy accompanies the workload", "default deny both ways")
    return r


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--kind", choices=("job", "worker", "service", "sandbox"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.repo.is_dir():
        print(f"error: {args.repo} is not a directory", file=sys.stderr)
        return 2
    report = check(args.repo, args.kind)
    if args.json:
        print(json.dumps([{"status": s, "rule": rule, "detail": d} for s, rule, d in report.results], indent=2))
    else:
        for status, rule, detail in report.results:
            print(f"{status:4}  {rule}" + (f"  [{detail}]" if detail else ""))
        counts = {s: sum(1 for x in report.results if x[0] == s) for s in (PASS, FAIL, WARN)}
        print(f"\n{counts[PASS]} pass, {counts[FAIL]} fail, {counts[WARN]} warn")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
