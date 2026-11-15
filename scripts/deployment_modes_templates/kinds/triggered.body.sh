timer_installed() { systemctl --user cat "$TIMER" >/dev/null 2>&1; }
show_timer() {
  printf '%-34s %-9s %s\n' "$TIMER" "$(systemctl --user is-active "$TIMER" 2>/dev/null || true)" \
    "$(systemctl --user is-enabled "$TIMER" 2>/dev/null || true)"
}

case "$cmd:$mode" in
  mode:*) echo "$mode (from $source_of)" ;;

  # ---- dev: one run in the foreground, no systemd, no cluster ----
  up:dev) @@DEV_UP@@ ;;
  down:dev) echo "dev mode runs once in the foreground; there is nothing to stop" ;;
  status:dev|verify:dev) @@DEV_STATUS@@ ;;

  # ---- host: the systemd user timer ----
  up:host)
    if marker_is_k3s; then
      echo "the K3s run is active; switching the unit back to run on the host"
      if command -v "${KUBECTL:-kubectl}" >/dev/null 2>&1; then "$K3S_DIR/handover.sh" pod-off
      else echo "kubectl is not installed: remove the unit's k3s drop-in yourself (docs/deployment.md) or the work runs in the pod" >&2; exit 1; fi
    fi
    timer_installed || {
      echo "$TIMER is not installed. Install it with Clockwork from its manifest (see docs/deployment.md)" >&2
      exit 1
    }
    systemctl --user enable --now "$TIMER"
    clear_marker
    ;;
  down:host) systemctl --user disable --now "$TIMER"; clear_marker ;;
  status:host) show_timer ;;
  verify:host) systemctl --user is-active --quiet "$TIMER" && echo "$TIMER is active" ;;

  # ---- k3s: the host timer stays the trigger; only the work moves into a pod ----
  # A heavy or guarded job keeps its timer, its job guard / the resource arbiter drop-ins and any post-step on the host. A
  # CronJob would bypass all of them, so only ExecStart changes: a removable drop-in runs run-job.sh, which creates one
  # Job from the suspended CronJob template and waits for it.
  up:k3s)
    require_cluster_tools
    timer_installed || {
      echo "$TIMER is not installed: k3s mode keeps the host timer as the trigger. Install it with Clockwork (see docs/deployment.md)" >&2
      exit 1
    }
@@PREPARE@@    "$K3S_DIR/build-image.sh"
    "$K3S_DIR/apply.sh"
    "$K3S_DIR/verify.sh"
    # Switch the unit over only after verify passed: until then the host runs the work as before.
    "$K3S_DIR/handover.sh" pod-on
    systemctl --user enable --now "$TIMER"
    set_marker k3s
    ;;
  down:k3s) "$0" --mode host up; echo "rolled back: the unit runs on the host again; the CronJob template is left in place (kubectl delete ns $NAMESPACE removes it)" ;;
  status:k3s) show_timer; kc -n "$NAMESPACE" get cronjob,pods 2>&1 || true ;;
  verify:k3s) "$K3S_DIR/verify.sh" ;;
esac
