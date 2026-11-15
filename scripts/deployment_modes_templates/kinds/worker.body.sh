unit_installed() { systemctl --user cat "$UNIT" >/dev/null 2>&1; }
unit_active() { systemctl --user is-active --quiet "$UNIT" 2>/dev/null; }
pod_off() { kc -n "$NAMESPACE" scale "deploy/$DEPLOYMENT" --replicas=0; }

case "$cmd:$mode" in
  mode:*) echo "$mode (from $source_of)" ;;

  # ---- dev: the worker in the foreground, no systemd, no cluster ----
  up:dev) @@DEV_UP@@ ;;
  down:dev) echo "dev mode runs in the foreground; stop it with Ctrl-C" ;;
  status:dev|verify:dev) @@DEV_STATUS@@ ;;

  # ---- host: the systemd user service ----
  up:host)
    if marker_is_k3s; then
      echo "the K3s worker is active; stopping it before the host worker starts"
      if command -v "${KUBECTL:-kubectl}" >/dev/null 2>&1; then pod_off
      else echo "kubectl is not installed: scale the Deployment to 0 yourself (docs/deployment.md) or both will do the work twice" >&2; exit 1; fi
    fi
    unit_installed || {
      echo "$UNIT is not installed. Install it with Clockwork from its manifest (see docs/deployment.md)" >&2
      exit 1
    }
    systemctl --user enable --now "$UNIT"
    clear_marker
    ;;
  down:host) systemctl --user disable --now "$UNIT"; clear_marker ;;
  status:host)
    printf '%-30s %-9s %s\n' "$UNIT" "$(systemctl --user is-active "$UNIT" 2>/dev/null || true)" \
      "$(systemctl --user is-enabled "$UNIT" 2>/dev/null || true)" ;;
  verify:host) systemctl --user is-active --quiet "$UNIT" && echo "$UNIT is active" ;;

  # ---- k3s: a pod; the host worker stops first (two workers would do every job twice) ----
  up:k3s)
    require_cluster_tools
    was_active=0
    if unit_active; then was_active=1; systemctl --user disable --now "$UNIT"; fi
    restore() {
      echo "the K3s deployment failed; restoring the previous worker" >&2
      pod_off 2>/dev/null || true
      if [ "$was_active" = 1 ]; then systemctl --user enable --now "$UNIT" || true; fi
    }
    trap restore ERR
@@PREPARE@@    "$K3S_DIR/build-image.sh"
    "$K3S_DIR/apply.sh"
    "$K3S_DIR/verify.sh"
    trap - ERR
    set_marker k3s
    ;;
  down:k3s) "$0" --mode host up; echo "rolled back to the host worker; the pod is scaled to 0 and left in place (kubectl delete ns $NAMESPACE removes it)" ;;
  status:k3s)
    printf '%-30s %-9s %s\n' "$UNIT" "$(systemctl --user is-active "$UNIT" 2>/dev/null || true)" \
      "$(systemctl --user is-enabled "$UNIT" 2>/dev/null || true)"
    kc -n "$NAMESPACE" get deploy,pods 2>&1 || true ;;
  verify:k3s) "$K3S_DIR/verify.sh" ;;
esac
