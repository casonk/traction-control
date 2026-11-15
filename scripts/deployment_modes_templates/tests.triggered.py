

# ---- triggered job: the host timer stays the trigger; the work runs in a pod ---------------------------------


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
    assert done.returncode == 1 and "drop-in" in done.stderr
    assert f"systemctl --user enable --now {UNIT}" not in calls(log)
    assert marker_file.exists()


def test_k3s_up_runs_the_steps_in_order_and_hands_over_only_after_verify(sandbox):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode == 0, done.stderr
    assert [n for n in names(log) if n.endswith(".sh") and n != "handover.sh"] == K3S_STEPS
    seen = calls(log)
    # The timer is the trigger in this mode: it is never disabled, and the unit is switched over after verify.
    assert seen.index("verify.sh ") < seen.index("handover.sh pod-on")
    assert f"systemctl --user disable --now {UNIT}" not in seen
    assert marker_path(tmp).read_text().strip() == "k3s"


@pytest.mark.parametrize("failing", K3S_STEPS)
def test_a_failed_step_leaves_the_unit_running_on_the_host(sandbox, failing):
    env, log, tmp = sandbox
    cluster_ready(tmp)
    _stub(tmp / "k3s" / failing, log, failing, "exit 1")
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode != 0
    seen = calls(log)
    assert "handover.sh pod-on" not in seen
    assert not marker_path(tmp).exists()


def test_k3s_without_the_host_timer_refuses_and_changes_nothing(sandbox):
    # The timer is the trigger (and carries the job guard and any post-step); without it nothing would run.
    env, log, tmp = sandbox
    cluster_ready(tmp)
    timer_not_installed(tmp)
    done = run(env, "--mode", "k3s", "up")
    assert done.returncode == 1 and "not installed" in done.stderr
    assert "handover.sh pod-on" not in calls(log)


def test_the_cronjob_is_only_a_template_that_never_schedules_itself():
    # The host timer triggers every run through run-job.sh; a CronJob that also ran would run twice.
    texts = [p.read_text() for p in (REPO / "deploy" / "k3s" / "manifests").glob("*.y*ml") if "kind: CronJob" in p.read_text()]
    if not texts:
        pytest.skip("no CronJob manifest yet (the scaffold's manifests are yours to write); this guards it once there is one")
    assert all("suspend: true" in t for t in texts)


def test_the_dropin_replaces_only_execstart():
    # The job guard, the resource arbiter, the time limit and any post-step live in other drop-ins and must keep applying.
    text = (REPO / "deploy" / "k3s" / "systemd" / "99-k3s-run.conf").read_text()
    body = [line for line in text.splitlines() if line and not line.startswith("#")]
    keys = {line.split("=", 1)[0] for line in body if "=" in line}
    assert keys <= {"Environment", "ExecStart"}, keys
    assert body.index("ExecStart=") < max(i for i, line in enumerate(body) if line.startswith("ExecStart=/"))


def test_handover_installs_and_removes_the_dropin(tmp_path):
    for name in ("kubectl", "systemctl"):
        stub = tmp_path / "bin" / name
        stub.parent.mkdir(exist_ok=True)
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    script = str(REPO / "deploy" / "k3s" / "handover.sh")
    dropin = tmp_path / "cfg" / "systemd" / "user" / "@@SERVICE_UNIT@@.d" / "99-k3s-run.conf"
    subprocess.run(["bash", script, "pod-on"], env=env, check=True)
    text = dropin.read_text()
    assert "run-job.sh" in text and "__" not in text
    subprocess.run(["bash", script, "pod-off"], env=env, check=True)
    assert not dropin.exists()


def test_run_job_reports_the_jobs_status(tmp_path):
    log = tmp_path / "calls.log"
    kubectl = tmp_path / "bin" / "kubectl"
    kubectl.parent.mkdir()
    kubectl.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {log}\n'
        'case "$*" in *"jsonpath={.status.failed}"*) echo 1 ;; esac\n'
    )
    kubectl.chmod(0o755)
    env = {**os.environ, "KUBECTL": str(kubectl), "KUBECONFIG": "/nonexistent"}
    done = subprocess.run(["bash", str(REPO / "deploy" / "k3s" / "run-job.sh")], env=env, capture_output=True, text=True)
    assert done.returncode == 1 and "failed" in done.stderr
    assert "create job" in log.read_text()
