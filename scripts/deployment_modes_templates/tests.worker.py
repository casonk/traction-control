

# ---- worker: two copies would do every job twice, so the host copy stops first and comes back on failure ----


def unit_state(tmp_path, *, installed=True, active=False):
    log = tmp_path / "calls.log"
    body = (
        'case "$*" in\n'
        f'  *" cat "*) exit {0 if installed else 1} ;;\n'
        f'  *is-active*) {"echo active; exit 0" if active else "echo inactive; exit 3"} ;;\n'
        "  *is-enabled*) echo disabled; exit 1 ;;\n"
        "esac"
    )
    _stub(tmp_path / "bin" / "systemctl", log, "systemctl", body)


def test_host_up_enables_the_unit_and_never_touches_kubectl_or_podman(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    assert f"systemctl --user enable --now {UNIT}" in calls(log)
    assert [n for n in names(log) if n in {"kubectl", "podman"}] == []


def test_host_up_without_the_unit_says_how_to_install_it_and_changes_nothing(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp, installed=False)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 1 and "not installed" in done.stderr
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)


def test_leaving_k3s_stops_the_pod_before_the_host_worker_starts(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp)
    cluster_ready(tmp)
    marker_file = marker(tmp)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    seen = calls(log)
    scale = next(i for i, c in enumerate(seen) if c.startswith("kubectl") and "--replicas=0" in c)
    assert scale < seen.index(f"systemctl --user enable --now {UNIT}")
    assert not marker_file.exists()


def test_leaving_k3s_without_kubectl_refuses_rather_than_run_two_workers(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp)
    marker_file = marker(tmp)
    done = run({**env, "KUBECTL": "definitely-not-installed"}, "--mode", "host", "up")
    assert done.returncode == 1 and "twice" in done.stderr
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)
    assert marker_file.exists()


def test_k3s_up_stops_the_host_worker_before_anything_starts_and_records_the_hand_over(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp, active=True)
    cluster_ready(tmp)
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode == 0, done.stderr
    seen = calls(log)
    stop = seen.index(f"systemctl --user disable --now {UNIT}")
    assert [n for n in names(log) if n.endswith(".sh")] == K3S_STEPS
    assert stop < seen.index(f"{K3S_STEPS[0]} ")
    assert marker_path(tmp).read_text().strip() == "k3s"
    assert f"systemctl --user enable --now {UNIT}" not in seen


@pytest.mark.parametrize("failing", K3S_STEPS)
def test_a_failed_step_scales_the_pod_down_and_restores_the_host_worker(sandbox, failing):
    env, log, tmp = sandbox
    unit_state(tmp, active=True)
    cluster_ready(tmp)
    _stub(tmp / "k3s" / failing, log, failing, "exit 1")
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    seen = calls(log)
    assert any(c.startswith("kubectl") and "--replicas=0" in c for c in seen)
    assert f"systemctl --user enable --now {UNIT}" in seen
    assert not marker_path(tmp).exists()


def test_a_failed_step_does_not_start_a_host_worker_that_was_not_running(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp, active=False)
    cluster_ready(tmp)
    _stub(tmp / "k3s" / "verify.sh", log, "verify.sh", "exit 1")
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)
