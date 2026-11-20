from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "azure_runner_power", Path(__file__).resolve().parents[1] / "scripts" / "azure_runner_power.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def result(out="", rc=0, err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Fake:
    """Answers az/gh calls; records the az verbs that change power."""

    def __init__(self, power="running", counts=None, gh_fail=False, az_fail=False):
        self.power, self.counts, self.gh_fail, self.az_fail, self.calls = power, counts or {}, gh_fail, az_fail, []

    def __call__(self, cmd):
        if cmd[0] == "gh":
            if self.gh_fail:
                return result(rc=1, err="rate limited")
            status = cmd[2].split("status=")[1].split("&")[0]
            return result(str(self.counts.get(status, 0)))
        if "get-instance-view" in cmd:
            return result(f"PowerState/{self.power}")
        self.calls.append(cmd[2])
        return result(rc=1, err="boom") if self.az_fail else result()


def setup(tmp_path, repos=("casonk/a", "casonk/b")):
    (tmp_path / "azure-enrolled.json").write_text(json.dumps({"repositories": list(repos)}))
    return tmp_path


def args(tmp_path, *extra):
    return mod.argparse.Namespace(state_dir=tmp_path, group="g", vm="v", idle_minutes=20, dry_run="--dry-run" in extra)


def test_waiting_run_starts_a_deallocated_vm(tmp_path):
    fake = Fake("deallocated", {"queued": 1})
    assert mod.cmd_tick(args(setup(tmp_path)), fake, NOW) == 0
    assert fake.calls == ["start"]


def test_nothing_waiting_leaves_a_deallocated_vm_alone(tmp_path):
    fake = Fake("deallocated")
    mod.cmd_tick(args(setup(tmp_path)), fake, NOW)
    assert fake.calls == []


def test_idle_clock_must_expire_before_deallocating(tmp_path):
    setup(tmp_path)
    fake = Fake("running")
    mod.cmd_tick(args(tmp_path), fake, NOW)  # first sight starts the clock
    mod.cmd_tick(args(tmp_path), fake, NOW + timedelta(minutes=10))
    assert fake.calls == []
    mod.cmd_tick(args(tmp_path), fake, NOW + timedelta(minutes=21))
    assert fake.calls == ["deallocate"]


def test_activity_resets_the_idle_clock(tmp_path):
    setup(tmp_path)
    mod.cmd_tick(args(tmp_path), Fake("running"), NOW)
    busy = Fake("running", {"in_progress": 1})
    mod.cmd_tick(args(tmp_path), busy, NOW + timedelta(minutes=19))
    quiet = Fake("running")
    mod.cmd_tick(args(tmp_path), quiet, NOW + timedelta(minutes=30))
    assert quiet.calls == []  # only 11 minutes since the last activity


def test_unknown_github_state_never_powers_off(tmp_path):
    setup(tmp_path)
    mod.cmd_tick(args(tmp_path), Fake("running"), NOW)
    flaky = Fake("running", gh_fail=True)
    mod.cmd_tick(args(tmp_path), flaky, NOW + timedelta(hours=5))
    assert flaky.calls == []


def test_unknown_vm_state_does_nothing(tmp_path):
    setup(tmp_path)
    fake = Fake("weird", {"queued": 3})
    mod.cmd_tick(args(tmp_path), fake, NOW)
    assert fake.calls == []


def test_dry_run_changes_nothing(tmp_path):
    fake = Fake("deallocated", {"queued": 2})
    mod.cmd_tick(args(setup(tmp_path), "--dry-run"), fake, NOW)
    assert fake.calls == []


def test_az_failure_is_reported(tmp_path):
    fake = Fake("deallocated", {"queued": 1}, az_fail=True)
    assert mod.cmd_tick(args(setup(tmp_path)), fake, NOW) == 1


def test_no_enrolled_repositories_is_idle(tmp_path):
    fake = Fake("running")
    mod.cmd_tick(args(tmp_path), fake, NOW)
    mod.cmd_tick(args(tmp_path), fake, NOW + timedelta(minutes=25))
    assert fake.calls == ["deallocate"]
