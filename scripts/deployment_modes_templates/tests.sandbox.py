

# ---- sandbox: the service stays on the host; one removable systemd drop-in points it at the sandbox pod ------


def dropin(tmp_path) -> Path:
    return tmp_path / "units" / f"{UNIT}.d" / "20-sandbox.conf"


def with_dropin(tmp_path) -> Path:
    path = dropin(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Service]\nEnvironment=X=1\n")
    return path


RESTART = f"systemctl --user restart {UNIT}"


def test_host_up_removes_the_drop_in_restarts_the_service_and_never_touches_a_cluster(sandbox):
    env, log, tmp = sandbox
    present = with_dropin(tmp)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    assert not present.exists()
    seen = calls(log)
    assert seen.index("systemctl --user daemon-reload") < seen.index(RESTART)
    assert [n for n in names(log) if n in {"kubectl", "podman"} or n == "bridge.sh"] == []


def test_leaving_k3s_takes_the_bridge_down_scales_the_pod_to_zero_and_clears_the_marker(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    with_dropin(tmp)
    marker_file = marker(tmp)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    seen = calls(log)
    assert "bridge.sh off" in seen
    assert any(c.startswith("kubectl") and "--replicas=0" in c for c in seen)
    assert not dropin(tmp).exists() and not marker_file.exists()


def test_k3s_up_points_the_service_at_the_sandbox_only_after_the_bridge_is_up_and_verify_passes(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode == 0, done.stderr
    seen = calls(log)
    order = [seen.index(f"{step} ") for step in K3S_STEPS if step != "verify.sh"]
    order += [seen.index("bridge.sh on"), seen.index("verify.sh "), seen.index(RESTART)]
    assert order == sorted(order), seen
    assert "Managed by deploy/deploy.sh" in dropin(tmp).read_text()
    assert marker_path(tmp).read_text().strip() == "k3s"


@pytest.mark.parametrize("failing", [s for s in K3S_STEPS if s != "verify.sh"])
def test_a_failed_build_or_apply_changes_nothing_on_the_host(sandbox, failing):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / failing, log, failing, "exit 1")
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    assert not dropin(tmp).exists() and RESTART not in calls(log)
    assert not any(c.startswith("bridge.sh") for c in calls(log))


def test_a_failed_verify_takes_the_bridge_down_and_never_touches_the_service(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / "verify.sh", log, "verify.sh", "exit 1")
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    seen = calls(log)
    assert "bridge.sh on" in seen and "bridge.sh off" in seen
    assert RESTART not in seen and not dropin(tmp).exists()
    assert not marker_path(tmp).exists()


def test_a_bridge_that_does_not_come_up_is_rolled_back_and_nothing_is_verified_through_it(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / "bridge.sh", log, "bridge.sh", '[ "$1" = on ] && exit 1 || exit 0')
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    assert "bridge.sh off" in calls(log) and "verify.sh " not in calls(log)
    assert not dropin(tmp).exists()


def test_a_service_that_will_not_restart_is_put_back_to_in_process_work(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    body = 'case "$*" in *restart*) exit 1 ;; *is-active*) echo inactive; exit 3 ;; esac'
    _stub(tmp / "bin" / "systemctl", log, "systemctl", body)
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    assert not dropin(tmp).exists()
    assert "bridge.sh off" in calls(log)
    assert not marker_path(tmp).exists()
