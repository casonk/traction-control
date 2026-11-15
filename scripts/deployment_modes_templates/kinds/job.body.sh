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
      echo "the K3s job is active; suspending it before the host timer starts"
      if command -v "${KUBECTL:-kubectl}" >/dev/null 2>&1; then "$K3S_DIR/handover.sh" pod-off
      else echo "kubectl is not installed: suspend the CronJob yourself (docs/deployment.md) or both will run" >&2; exit 1; fi
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

  # ---- k3s: a CronJob, created suspended and handed work only after verify passes ----
  up:k3s)
    require_cluster_tools
@@PREPARE@@    "$K3S_DIR/build-image.sh"
    "$K3S_DIR/apply.sh"
    "$K3S_DIR/verify.sh"
    # Hand over only after verify passed: the timer keeps running until the pod is proven.
    systemctl --user disable --now "$TIMER" 2>/dev/null || true
    # `|| true` lets a timer that was never installed pass, but it also hides a failed disable (no user bus in
    # an ssh or cron shell). Two copies of a job must never run, so check the timer is really off.
    if systemctl --user is-active --quiet "$TIMER" 2>/dev/null; then
      echo "$TIMER is still active; stopping here so the host timer and the CronJob never both run" >&2
      exit 1
    fi
    "$K3S_DIR/handover.sh" pod-on
    set_marker k3s
    ;;
  down:k3s) "$0" --mode host up; echo "rolled back to the host timer; the CronJob is suspended and left in place (kubectl delete ns $NAMESPACE removes it)" ;;
  status:k3s) show_timer; kc -n "$NAMESPACE" get cronjob,pods 2>&1 || true ;;
  verify:k3s) "$K3S_DIR/verify.sh" ;;
esac
