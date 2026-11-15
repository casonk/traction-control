

# ---- job: a host timer and a CronJob that must never both run ----------------------------------------------


def timer_not_installed(tmp_path):
    body = 'case "$*" in *" cat "*) exit 1 ;; *is-active*) echo inactive; exit 3 ;; esac'
    _stub(tmp_path / "bin" / "systemctl", tmp_path / "calls.log", "systemctl", body)


def test_host_up_enables_the_timer_and_never_touches_kubectl_or_podman(sandbox):
    env, log, _ = sandbox
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    assert f"systemctl --user enable --now {UNIT}" in calls(log)
    assert [n for n in names(log) if n in {"kubectl", "podman"}] == []


def test_host_up_without_the_timer_says_how_to_install_it_and_changes_nothing(sandbox):
    env, log, tmp = sandbox
    timer_not_installed(tmp)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 1 and "not installed" in done.stderr
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)


def test_host_down_disables_only_the_timer(sandbox):
    env, log, _ = sandbox
    run(env, "--mode", "host", "down")
    assert f"systemctl --user disable --now {UNIT}" in calls(log)
    assert [n for n in names(log) if n in {"kubectl", "podman"}] == []


def test_leaving_k3s_suspends_the_pod_before_the_timer_is_enabled(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    marker_file = marker(tmp)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    seen = calls(log)
    assert seen.index("handover.sh pod-off") < seen.index(f"systemctl --user enable --now {UNIT}")
    assert not marker_file.exists()


def test_leaving_k3s_without_kubectl_refuses_rather_than_run_twice(sandbox):
    env, log, tmp = sandbox
    marker_file = marker(tmp)
    done = run({**env, "KUBECTL": "definitely-not-installed"}, "--mode", "host", "up")
    assert done.returncode == 1 and "both will run" in done.stderr
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)
    assert marker_file.exists()


def test_k3s_up_runs_the_steps_in_order_and_hands_over_only_after_verify(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode == 0, done.stderr
    assert [n for n in names(log) if n.endswith(".sh") and n != "handover.sh"] == K3S_STEPS
    seen = calls(log)
    verify = seen.index("verify.sh ")
    disable = seen.index(f"systemctl --user disable --now {UNIT}")
    assert verify < disable < seen.index("handover.sh pod-on")
    assert marker_path(tmp).read_text().strip() == "k3s"


@pytest.mark.parametrize("failing", K3S_STEPS)
def test_a_failed_step_leaves_the_timer_running_and_the_pod_suspended(sandbox, failing):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / failing, log, failing, "exit 1")
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    seen = calls(log)
    assert f"systemctl --user disable --now {UNIT}" not in seen
    assert "handover.sh pod-on" not in seen
    assert not marker_path(tmp).exists()


def test_k3s_up_stops_when_the_timer_is_still_active_after_disable(sandbox):
    # `systemctl disable` failing silently (no user bus in an ssh or cron shell) must not let the pod start
    # alongside the host timer.
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "bin" / "systemctl", log, "systemctl", 'case "$*" in *is-active*) exit 0 ;; esac')
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode == 1 and "still active" in done.stderr
    assert "handover.sh pod-on" not in calls(log)
    assert not marker_path(tmp).exists()
