unit_installed() { systemctl --user cat "$UNIT" >/dev/null 2>&1; }
bridge_on() { command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet "$BRIDGE" 2>/dev/null; }
healthz() { curl -fsS --max-time 5 "http://127.0.0.1:$PORT/@@HEALTH_PATH@@" && echo; }
show_units() {
  for u in "$UNIT" "$BRIDGE"; do
    printf '%-30s %-9s %s\n' "$u" "$(systemctl --user is-active "$u" 2>/dev/null || true)" "$(systemctl --user is-enabled "$u" 2>/dev/null || true)"
  done
}

case "$cmd:$mode" in
  mode:*) echo "$mode (from $source_of)" ;;

  # ---- dev: the service in the foreground on 127.0.0.1, no systemd, no cluster ----
  up:dev) @@DEV_UP@@ ;;
  down:dev) echo "dev mode runs in the foreground; stop it with Ctrl-C" ;;
  status:dev|verify:dev) healthz || echo "not answering on 127.0.0.1:$PORT (dev mode runs in the foreground: deploy/deploy.sh up)" ;;

  # ---- host: the systemd user service listening on 127.0.0.1:$PORT ----
  up:host)
    if bridge_on; then
      echo "the K3s bridge is on; switching back to the host service"
      "$K3S_DIR/bridge.sh" off
    else
      unit_installed || { echo "$UNIT is not installed (see docs/deployment.md)" >&2; exit 1; }
      systemctl --user enable --now "$UNIT"
    fi
    clear_marker
    ;;
  down:host) systemctl --user disable --now "$UNIT"; clear_marker ;;
  status:host) show_units ;;
  verify:host) healthz ;;

  # ---- k3s: a pod behind a loopback forwarder, so the front end keeps using 127.0.0.1:$PORT ----
  up:k3s)
    require_cluster_tools
@@PREPARE@@    "$K3S_DIR/build-image.sh"
    "$K3S_DIR/apply.sh"
    "$K3S_DIR/verify.sh"
    # `bridge on` stops and disables the host service; if it cannot come up, put the host service back.
    trap '"$K3S_DIR/bridge.sh" off || true' ERR
    "$K3S_DIR/bridge.sh" on
    trap - ERR
    set_marker k3s
    ;;
  down:k3s) "$0" --mode host up; echo "rolled back to the host service; the pod is left running (kubectl delete ns $NAMESPACE removes it)" ;;
  status:k3s) show_units; kc -n "$NAMESPACE" get pods 2>&1 || true ;;
  verify:k3s) "$K3S_DIR/verify.sh" ;;
esac
