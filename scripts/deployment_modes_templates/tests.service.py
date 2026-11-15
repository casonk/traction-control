

# ---- service: a pod behind a loopback forwarder; the host service and the forwarder must never both listen ----


def unit_state(tmp_path, *, installed=True, bridge_active=False):
    log = tmp_path / "calls.log"
    bridge = "  *is-active*@@BRIDGE_UNIT@@.socket*) echo active; exit 0 ;;\n" if bridge_active else ""
    body = (
        'case "$*" in\n'
        f'  *" cat "*) exit {0 if installed else 1} ;;\n'
        + bridge
        + "  *is-active*) echo inactive; exit 3 ;;\n"
        "esac"
    )
    _stub(tmp_path / "bin" / "systemctl", log, "systemctl", body)


def test_host_up_enables_the_service_and_never_touches_kubectl_or_podman(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    assert f"systemctl --user enable --now {UNIT}" in calls(log)
    assert [n for n in names(log) if n in {"kubectl", "podman"} or n == "bridge.sh"] == []


def test_host_up_without_the_unit_says_so_and_changes_nothing(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp, installed=False)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 1 and "not installed" in done.stderr
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)


def test_leaving_k3s_hands_the_port_back_through_the_bridge_script(sandbox):
    env, log, tmp = sandbox
    unit_state(tmp, bridge_active=True)
    done = run(env, "--mode", "host", "up")
    assert done.returncode == 0, done.stderr
    assert "bridge.sh off" in calls(log)
    # the bridge script owns re-enabling the host service
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)


def test_k3s_up_brings_the_bridge_up_only_after_verify_passes(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode == 0, done.stderr
    seen = calls(log)
    order = [seen.index(f"{step} ") for step in K3S_STEPS] + [seen.index("bridge.sh on")]
    assert order == sorted(order), seen
    assert marker_path(tmp).read_text().strip() == "k3s"


@pytest.mark.parametrize("failing", K3S_STEPS)
def test_a_failed_step_never_touches_the_host_service(sandbox, failing):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / failing, log, failing, "exit 1")
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    assert not any(c.startswith("bridge.sh") for c in calls(log))
    assert not any("disable" in c for c in calls(log))


def test_a_bridge_that_cannot_come_up_puts_the_host_service_back(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / "bridge.sh", log, "bridge.sh", '[ "$1" = on ] && exit 1 || exit 0')
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    assert "bridge.sh off" in calls(log)


def test_a_failed_step_leaves_no_marker_and_a_successful_host_up_clears_it(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / "verify.sh", log, "verify.sh", "exit 1")
    assert run(env, "--mode", "k3s", "up").returncode != 0
    assert not marker_path(tmp).exists()
    marker(tmp)
    unit_state(tmp)
    assert run(env, "--mode", "host", "up").returncode == 0
    assert not marker_path(tmp).exists()
