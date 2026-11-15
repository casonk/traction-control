#!/usr/bin/env python3
"""Scaffold the portfolio's deployment-modes contract into a repository.

    scaffold_deployment_modes.py --repo PATH --app NAME --kind job|worker|service|sandbox [options]

Writes ``deploy/deploy.sh`` (one setting chooses ``dev``, ``host`` or ``k3s``; only ``k3s`` touches a cluster), the
generic helpers under ``deploy/k3s/``, ``config/deploy.example.env``, ``docs/deployment.md``, a full
``tests/test_deploy_modes.py`` that checks the contract against stub commands, a ``.gitignore`` rule and a README
link. The app-specific pieces (``apply.sh``, ``verify.sh``, the Containerfile, the manifests) are written as stubs
that **fail loudly** until you replace them; the generated tests pass from the start and keep passing as you do.

Kinds (see docs/deployment-modes.md):

  job      a periodic job: a host timer, and a CronJob created suspended and handed work only after verify
  worker   a process that must never run twice: the host copy stops first and is restored on failure
  service  a process with a port: a pod behind a loopback forwarder, so the front end keeps its address
  sandbox  one risky step of a service: a no-network pod, switched in by a removable systemd drop-in

Refuses to overwrite files unless ``--force``; ``--dry-run`` shows the plan. Run
``check_deployment_modes.py --repo PATH`` afterwards (and in review) to check the result against the contract.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "deployment_modes_templates"
DOCS = HERE.parent / "docs" / "deployment-modes.md"
KINDS = ("job", "triggered", "worker", "service", "sandbox")
RESERVED_PORTS = {8081: "the password UI", 8090: "the remote-desktop dev container", 8091: "the remote-desktop dev container", 9090: "the machine's web console"}
CLUSTER_RANGE = (ipaddress.ip_address("10.43.0.51"), ipaddress.ip_address("10.43.0.99"))
TIMER_KINDS = ("job", "triggered")
GITIGNORE_BLOCK = "\n# Which way this runs on this machine (deploy/deploy.sh)\nconfig/deploy.local.env\n"

BLURBS = {
    "job": (
        "one run in the foreground, no systemd, no cluster",
        "the systemd user timer",
        "a CronJob in K3s, created suspended and started only after verification",
    ),
    "triggered": (
        "one run in the foreground, no systemd, no cluster",
        "the systemd user timer runs the job on the host",
        "the same timer triggers the job, which runs in a pod; its guards and post-steps stay on the host",
    ),
    "worker": (
        "the worker in the foreground, no systemd, no cluster",
        "the systemd user service",
        "a pod in K3s; the host copy stops first so the work is never done twice",
    ),
    "service": (
        "the service in the foreground on 127.0.0.1, no systemd, no cluster",
        "the systemd user service on 127.0.0.1",
        "a pod in K3s behind a loopback forwarder, so the front end keeps its address",
    ),
    "sandbox": (
        "the sandbox service in the foreground on this machine, no container",
        "the service does the risky work in its own process, as it always did",
        "the risky work in a K3s pod with no network, no secrets and none of your files",
    ),
}

MODE_TEXT = {
    "job": """### dev
`up` runs the job once in the foreground. `status` and `verify` check it without changing anything.

### host
`up` enables the systemd user timer (install it once with Clockwork; `up` says so if it is missing). `down` disables it.

### k3s
`up` refuses to start, and changes nothing, unless kubectl, podman and the in-cluster registry are present. Then it
builds the image (refusing uncommitted source), applies the manifests (the CronJob is created **suspended**), runs
`verify.sh` (which must pass), and only then disables the host timer and unsuspends the CronJob. A failed step
changes nothing: the timer keeps running.""",
    "triggered": """### dev
`up` runs the job once in the foreground. `status` and `verify` check it without changing anything.

### host
`up` removes the K3s drop-in from the unit (if there) and enables the systemd user timer (install it once with
Clockwork; `up` says so if it is missing). `down` disables the timer.

### k3s
The host timer stays the trigger, so its job guard, resource-arbiter limits and any post-step keep working; only the work
moves into a pod. `up` refuses to start, and changes nothing, unless kubectl, podman, the in-cluster registry and the
host timer are present. Then it builds the image (refusing uncommitted source), applies the manifests (the CronJob is
only a **suspended template**), runs `verify.sh` (which must pass), and only then installs a systemd drop-in that
replaces the unit's `ExecStart` with `deploy/k3s/run-job.sh`. That script creates one Job from the template, streams
its log into the journal and exits with the job's status. A failed step changes nothing.""",
    "worker": """### dev
`up` runs the worker in the foreground. `status` and `verify` report without changing anything.

### host
`up` enables the systemd user service (install it once with Clockwork; `up` says so if it is missing).

### k3s
`up` refuses to start, and changes nothing, unless kubectl, podman and the in-cluster registry are present. **Two
copies of the worker would do every job twice**, so it stops the host copy first, then builds (refusing uncommitted
source), applies and verifies. If any step fails it scales the pod to 0 and restarts the host copy if it had been
running, so you are never left with neither.""",
    "service": """### dev
`up` runs the service in the foreground on `127.0.0.1`. `status` and `verify` call its health endpoint.

### host
`up` enables the systemd user service. If the K3s bridge was on, it takes the bridge down and starts the service again.

### k3s
`up` refuses to start, and changes nothing, unless kubectl, podman and the in-cluster registry are present. Then it
builds (refusing uncommitted source), applies, verifies, and only then runs `bridge.sh on`, which stops and disables
the host service and forwards `127.0.0.1` to the pod, so the front end is unchanged. If the bridge cannot come up
the host service is put back.""",
    "sandbox": """### dev
`up` runs the sandbox service in the foreground on this machine, without a container, so a developer can try the
sandbox path with no cluster.

### host
`up` removes the sandbox drop-in from the service's unit and restarts it, so it does the risky work in its own
process. If the K3s sandbox was active it also takes the bridge down and scales the pod to 0.

### k3s
`up` refuses to start, and changes nothing, unless kubectl, podman and the in-cluster registry are present. Then it
builds (refusing uncommitted source), applies, brings the bridge up, verifies through it, and only then writes a
systemd drop-in that points the service at the sandbox and restarts it. If anything from the bridge onward fails,
the bridge goes back down and the service keeps working in-process.""",
}

SWITCH_TEXT = {
    "job": """| From | To | Command |
|---|---|---|
| dev | host | `deploy/deploy.sh --mode host up` |
| host | k3s | `deploy/deploy.sh --mode k3s up` (verifies first, then hands over) |
| k3s | host | `deploy/deploy.sh --mode host up` (suspends the CronJob, re-enables the timer) |""",
    "triggered": """| From | To | Command |
|---|---|---|
| dev | host | `deploy/deploy.sh --mode host up` |
| host | k3s | `deploy/deploy.sh --mode k3s up` (verifies first, then points the unit's `ExecStart` at the pod) |
| k3s | host | `deploy/deploy.sh --mode host up` (removes the drop-in; the unit runs on the host again) |""",
    "worker": """| From | To | Command |
|---|---|---|
| dev | host | stop the foreground process, then `deploy/deploy.sh --mode host up` |
| host | k3s | `deploy/deploy.sh --mode k3s up` (stops the host copy, deploys, verifies) |
| k3s | host | `deploy/deploy.sh --mode host up` (scales the pod to 0, starts the host copy) |""",
    "service": """| From | To | Command |
|---|---|---|
| dev | host | stop the foreground process, then `deploy/deploy.sh --mode host up` |
| host | k3s | `deploy/deploy.sh --mode k3s up` (verifies first, then brings the bridge up) |
| k3s | host | `deploy/deploy.sh --mode host up` (takes the bridge down, starts the host service) |""",
    "sandbox": """| From | To | Command |
|---|---|---|
| dev | host | stop the foreground sandbox (Ctrl-C), then `deploy/deploy.sh --mode host up` |
| host | k3s | `deploy/deploy.sh --mode k3s up` (verifies first, then switches the service over) |
| k3s | host | `deploy/deploy.sh --mode host up` (drops the service back to in-process work) |""",
}


class ScaffoldError(Exception):
    pass


@dataclass
class Plan:
    files: dict[str, tuple[str, bool]]  # path -> (content, executable)


def _read(*parts: str) -> str:
    return (TEMPLATES.joinpath(*parts)).read_text(encoding="utf-8")


def render(text: str, tokens: dict[str, str]) -> str:
    for key, value in tokens.items():
        text = text.replace(f"@@{key}@@", value)
    leftover = sorted(set(re.findall(r"@@([A-Z_]+)@@", text)))
    if leftover:
        raise ScaffoldError(f"unfilled template placeholders: {', '.join(leftover)}")
    return text


def used_addresses() -> tuple[set[str], set[int]]:
    """Cluster IPs and ports already claimed in the pattern doc's address table."""
    if not DOCS.is_file():
        return set(), set()
    # Only the address table's rows claim anything: the doc's own example commands and "next free" notes mention
    # addresses too, and reading those made the scaffold refuse an address that was in fact free.
    text = "\n".join(line for line in DOCS.read_text(encoding="utf-8").splitlines() if line.lstrip().startswith("|"))
    ips = set(re.findall(r"10\.43\.0\.\d+", text))
    ports = {int(p) for p in re.findall(r"(?:10\.43\.0\.\d+|127\.0\.0\.1):(\d+)", text)}
    return ips, ports


def validate(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9-]*", args.app):
        raise ScaffoldError("--app must be lowercase letters, digits and hyphens, starting with a letter")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.prefix):
        raise ScaffoldError("--prefix must be UPPER_SNAKE_CASE")
    for unit in (args.unit,):
        if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.(service|timer)", unit):
            raise ScaffoldError(f"--unit {unit!r} must be a systemd unit name ending in .service or .timer")
    if args.kind in TIMER_KINDS and not args.unit.endswith(".timer"):
        raise ScaffoldError(f"a {args.kind}'s --unit is its timer, e.g. my-job.timer")
    if args.kind not in TIMER_KINDS and not args.unit.endswith(".service"):
        raise ScaffoldError(f"a {args.kind}'s --unit is its service, e.g. my-app.service")
    if args.kind in ("service", "sandbox"):
        if not args.cluster_ip or not args.port:
            raise ScaffoldError(f"a {args.kind} needs --cluster-ip and --port (its fixed address and loopback port)")
        try:
            ip = ipaddress.ip_address(args.cluster_ip)
        except ValueError as exc:
            raise ScaffoldError(f"--cluster-ip: {exc}") from exc
        if not CLUSTER_RANGE[0] <= ip <= CLUSTER_RANGE[1]:
            raise ScaffoldError("--cluster-ip must be in 10.43.0.51-10.43.0.99 (10.43.0.50 is the registry)")
        if not 1024 <= args.port <= 65535:
            raise ScaffoldError("--port must be 1024-65535")
        if args.port in RESERVED_PORTS:
            raise ScaffoldError(f"--port {args.port} belongs to {RESERVED_PORTS[args.port]}")
        if not args.skip_registry_check:
            ips, ports = used_addresses()
            if args.cluster_ip in ips:
                raise ScaffoldError(f"{args.cluster_ip} is already in docs/deployment-modes.md; pick the next free one")
            if args.port in ports:
                raise ScaffoldError(f"port {args.port} is already in docs/deployment-modes.md; pick another")
    for pair in args.dropin_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", pair):
            raise ScaffoldError(f"--dropin-env {pair!r} must be KEY=VALUE")


def build_plan(args: argparse.Namespace) -> Plan:
    kind, app = args.kind, args.app
    dev_blurb, host_blurb, k3s_blurb = BLURBS[kind]
    default_dev_up = f'echo "TODO: set --dev-up: how {app} runs once in the foreground" >&2; exit 1'
    steps = (["prepare-host.sh"] if args.host_data else []) + ["build-image.sh", "apply.sh", "verify.sh"]
    extra = {"job": ["handover.sh"], "triggered": ["handover.sh"], "worker": [], "service": ["bridge.sh"], "sandbox": ["bridge.sh"]}[kind]
    tokens = {
        "APP": app,
        "PREFIX": args.prefix,
        "KIND": kind,
        "DESCRIPTION": args.description or f"run {app} the way you choose: dev, host or k3s.",
        "UNIT": args.unit,
        "SERVICE_UNIT": args.service_unit or (args.unit[: -len(".timer")] + ".service" if args.unit.endswith(".timer") else args.unit),
        "NAMESPACE": args.namespace or app,
        "WORKLOAD": args.workload,
        "BRIDGE_UNIT": args.bridge_unit,
        "CLUSTER_IP": args.cluster_ip or "",
        "PORT": str(args.port or ""),
        "HEALTH_PATH": args.health_path.lstrip("/"),
        "DEV_UP": args.dev_up or default_dev_up,
        "DEV_STATUS": args.dev_status or f'echo "dev mode runs {app} in the foreground; there is nothing to check"',
        "DEV_BLURB": dev_blurb,
        "HOST_BLURB": host_blurb,
        "K3S_BLURB": k3s_blurb,
        "PREPARE": '    "$K3S_DIR/prepare-host.sh"\n' if args.host_data else "",
        "SOURCE_PATHS": " ".join(args.source),
        "STEPS": json.dumps(steps),
        "EXTRA_STUBS": json.dumps(extra),
        "SOURCES": json.dumps(list(args.source)),
        "DROPIN_ENV": "\n".join(
            f"Environment={pair}" for pair in (args.dropin_env or [f"{args.prefix}_SANDBOX=sandbox"])
        ),
        "MODE_TEXT": MODE_TEXT[kind],
        "SWITCH_TEXT": SWITCH_TEXT[kind],
    }
    # The kind's variables and body can themselves hold placeholders, so render in two passes.
    header = _read("deploy.header.sh")
    header = header.replace("@@KIND_VARS@@", _read("kinds", f"{kind}.vars.sh").rstrip("\n"))
    header = header.replace("@@KIND_BODY@@", _read("kinds", f"{kind}.body.sh").rstrip("\n"))
    files: dict[str, tuple[str, bool]] = {
        "deploy/deploy.sh": (render(header, tokens), True),
        "deploy/k3s/env_value.py": (_read("k3s", "env_value.py"), False),
        "deploy/k3s/build-image.sh": (render(_read("k3s", "build-image.sh"), tokens), True),
        "deploy/k3s/Containerfile": (render(_read("k3s", "Containerfile"), tokens), False),
        "deploy/k3s/apply.sh": (render(_read("k3s", "apply.sh"), tokens), True),
        "deploy/k3s/verify.sh": (render(_read("k3s", "verify.sh"), tokens), True),
        "config/deploy.example.env": (render(_read("config.deploy.example.env"), tokens), False),
        "docs/deployment.md": (render(_read("docs.deployment.md"), tokens), False),
    }
    if args.host_data:
        files["deploy/k3s/prepare-host.sh"] = (render(_read("k3s", "prepare-host.sh"), tokens), True)
    if kind == "job":
        files["deploy/k3s/handover.sh"] = (render(_read("k3s", "handover.sh"), tokens), True)
    if kind == "triggered":
        files["deploy/k3s/handover.sh"] = (render(_read("k3s", "handover.triggered.sh"), tokens), True)
        files["deploy/k3s/run-job.sh"] = (render(_read("k3s", "run-job.sh"), tokens), True)
        files["deploy/k3s/systemd/zz-k3s-run.conf"] = (_read("k3s", "trigger.conf"), False)
    if kind in ("service", "sandbox"):
        bridge = "bridge.service.sh" if kind == "service" else "bridge.sandbox.sh"
        files["deploy/k3s/bridge.sh"] = (render(_read("k3s", bridge), tokens), True)
        files[f"deploy/k3s/systemd/{args.bridge_unit}.socket"] = (render(_read("k3s", "bridge.socket"), tokens), False)
        files[f"deploy/k3s/systemd/{args.bridge_unit}.service"] = (render(_read("k3s", "bridge.service"), tokens), False)
    tests = _read("tests.common.py") + _read(f"tests.{kind}.py")
    files["tests/test_deploy_modes.py"] = (render(tests, tokens), False)
    return Plan(files)


def apply_plan(plan: Plan, repo: Path, *, force: bool, dry_run: bool) -> list[str]:
    conflicts = [rel for rel in plan.files if (repo / rel).exists()]
    if conflicts and not force:
        raise ScaffoldError("these files already exist (use --force to overwrite): " + ", ".join(conflicts))
    written: list[str] = []
    for rel, (content, executable) in plan.files.items():
        written.append(rel)
        if dry_run:
            continue
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
    return written


def update_gitignore(repo: Path, *, dry_run: bool) -> bool:
    path = repo / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if "config/deploy.local.env" in current:
        return False
    if not dry_run:
        path.write_text(current.rstrip("\n") + "\n" + GITIGNORE_BLOCK, encoding="utf-8")
    return True


def update_readme(repo: Path, app: str, *, dry_run: bool) -> bool:
    path = repo / "README.md"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    if "docs/deployment.md" in text:
        return False
    section = (
        "## Ways to run it\n\n"
        f"One command, one setting: `deploy/deploy.sh up`. The mode is `dev` (the default, no cluster), `host` (systemd) or\n"
        f"`k3s` (K3s, optional). Only `k3s` needs a cluster, and switching never moves your data. Choose with `--mode`,\n"
        f"`config/deploy.example.env` copied to `config/deploy.local.env`, or the environment. Full guide:\n"
        f"[`docs/deployment.md`](docs/deployment.md).\n\n"
    )
    match = re.search(r"^## ", text, flags=re.M)
    new = text[: match.start()] + section + text[match.start() :] if match else text.rstrip("\n") + "\n\n" + section
    if not dry_run:
        path.write_text(new, encoding="utf-8")
    return True


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", type=Path, required=True, help="the repository to scaffold into")
    p.add_argument("--app", required=True, help="short kebab-case name, used for state, image and unit names")
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--prefix", help="UPPER_SNAKE env prefix (default: the app name)")
    p.add_argument("--unit", help="host systemd unit: a .timer for a job, else a .service (default derived from --app)")
    p.add_argument("--service-unit", help="the service the timer starts (triggered kind; default: the timer's name with .service)")
    p.add_argument("--namespace", help="Kubernetes namespace (default: the app name)")
    p.add_argument("--workload", help="CronJob or Deployment name (default derived from --app)")
    p.add_argument("--bridge-unit", help="loopback forwarder unit base name (service and sandbox kinds)")
    p.add_argument("--cluster-ip", help="fixed ClusterIP in 10.43.0.51-99 (service and sandbox kinds)")
    p.add_argument("--port", type=int, help="loopback and pod port (service and sandbox kinds)")
    p.add_argument("--health-path", default="healthz")
    p.add_argument("--source", action="append", default=[], help="a path baked into the image and guarded against uncommitted changes (repeatable; default src)")
    p.add_argument("--dev-up", help="shell command that runs it in the foreground for dev mode")
    p.add_argument("--dev-status", help="shell command for dev status/verify")
    p.add_argument("--dropin-env", action="append", default=[], help="KEY=VALUE for the sandbox drop-in (repeatable)")
    p.add_argument("--host-data", action="store_true", help="the pod mounts host folders: also generate prepare-host.sh")
    p.add_argument("--description", help="one sentence for the deploy.sh header")
    p.add_argument("--force", action="store_true", help="overwrite existing files")
    p.add_argument("--dry-run", action="store_true", help="show what would be written and change nothing")
    p.add_argument("--skip-registry-check", action="store_true", help="do not check the address table in docs/deployment-modes.md")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.prefix = args.prefix or args.app.upper().replace("-", "_")
    args.source = args.source or ["src"]
    args.unit = args.unit or (f"{args.app}.timer" if args.kind in TIMER_KINDS else f"{args.app}.service")
    args.workload = args.workload or {
        "job": f"{args.app}-job",
        "triggered": f"{args.app}-job",
        "worker": args.app,
        "service": f"{args.app}-web",
        "sandbox": f"{args.app}-sandbox",
    }[args.kind]
    args.bridge_unit = args.bridge_unit or (f"{args.app}-proxy" if args.kind == "service" else f"{args.app}-sandbox")
    try:
        validate(args)
        if not args.repo.is_dir():
            raise ScaffoldError(f"{args.repo} is not a directory")
        plan = build_plan(args)
        written = apply_plan(plan, args.repo, force=args.force, dry_run=args.dry_run)
        ignored = update_gitignore(args.repo, dry_run=args.dry_run)
        readme = update_readme(args.repo, args.app, dry_run=args.dry_run)
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    verb = "would write" if args.dry_run else "wrote"
    for rel in written:
        print(f"{verb} {rel}")
    if ignored:
        print(f"{verb.replace('write', 'add')} config/deploy.local.env to .gitignore")
    if readme:
        print(f"{verb.replace('write', 'add')} a 'Ways to run it' section to README.md")
    if not args.dry_run:
        print(_next_steps(args))
    return 0


def _next_steps(args: argparse.Namespace) -> str:
    todo = ["deploy/k3s/apply.sh", "deploy/k3s/verify.sh", "deploy/k3s/Containerfile", "deploy/k3s/manifests/ (namespace, workload, network policy)"]
    if args.host_data:
        todo.insert(0, "deploy/k3s/prepare-host.sh")
    lines = [
        "",
        "Next:",
        "  1. python3 -m pytest tests/test_deploy_modes.py     # the generated tests pass now and must keep passing",
        "  2. fill in what the contract leaves to you (the stubs fail loudly until you do):",
        *[f"       - {item}" for item in todo],
        "  3. set --dev-up / the dev status command in deploy/deploy.sh if you skipped them",
        "  4. python3 scripts/check_deployment_modes.py --repo .   # from traction-control: audit against the contract",
        "  5. add the service's ClusterIP and ports to docs/deployment-modes.md in traction-control, in the same change",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
