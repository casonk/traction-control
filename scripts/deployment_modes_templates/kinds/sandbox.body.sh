healthz() { curl -fsS --max-time 5 "http://127.0.0.1:$PORT/@@HEALTH_PATH@@" && echo; }
bridge() { "$K3S_DIR/bridge.sh" "$1"; }

# The service uses the sandbox only while this drop-in exists. Writing it and removing it are the whole switch.
install_dropin() {
  mkdir -p "$DROPIN_DIR"
  cat > "$DROPIN" <<DROPIN_EOF
# Managed by deploy/deploy.sh. Remove with: deploy/deploy.sh --mode host up
[Service]
@@DROPIN_ENV@@
DROPIN_EOF
  systemctl --user daemon-reload
  systemctl --user restart "$DAEMON"
}
remove_dropin() {
  rm -f "$DROPIN"
  systemctl --user daemon-reload
  systemctl --user restart "$DAEMON"
}

case "$cmd:$mode" in
  mode:*) echo "$mode (from $source_of)" ;;

  # ---- dev: the sandbox service in the foreground on this machine, no container ----
  up:dev) @@DEV_UP@@ ;;
  down:dev) echo "dev mode runs the sandbox in the foreground; stop it with Ctrl-C" ;;
  status:dev|verify:dev) healthz || echo "no sandbox answering on 127.0.0.1:$PORT (deploy/deploy.sh up starts one)" ;;

  # ---- host: the service does the risky work in its own process, as it always did ----
  up:host)
    if marker_is_k3s; then
      echo "the K3s sandbox is active; switching the service back to in-process work"
      bridge off || true
      if command -v "${KUBECTL:-kubectl}" >/dev/null 2>&1; then
        kc -n "$NAMESPACE" scale "deploy/$DEPLOYMENT" --replicas=0 || true
      else
        echo "kubectl is not installed: the pod keeps running, idle; stop it with: kubectl -n $NAMESPACE scale deploy/$DEPLOYMENT --replicas=0" >&2
      fi
    fi
    remove_dropin
    clear_marker
    ;;
  down:host) echo "host mode has no service of its own: the service is managed by Clockwork (systemctl --user stop $DAEMON)" ;;
  status:host)
    printf '%-26s %-9s sandbox drop-in: %s\n' "$DAEMON" "$(systemctl --user is-active "$DAEMON" 2>/dev/null || true)" \
      "$([ -f "$DROPIN" ] && echo present || echo absent)" ;;
  verify:host) [ ! -f "$DROPIN" ] && echo "the service works in-process (no sandbox drop-in)" ;;

  # ---- k3s: the sandbox in a pod with no network, no secrets and none of your files ----
  up:k3s)
    require_cluster_tools
@@PREPARE@@    "$K3S_DIR/build-image.sh"
    "$K3S_DIR/apply.sh"
    # Anything that fails from here on leaves the service as it was: the bridge goes back down, and the
    # service is restarted only if it had already been pointed at the sandbox.
    restore() {
      echo "the K3s sandbox could not be brought up; keeping in-process work" >&2
      if [ -f "$DROPIN" ]; then
        rm -f "$DROPIN"
        systemctl --user daemon-reload || true
        systemctl --user restart "$DAEMON" || true
      fi
      bridge off || true
    }
    trap restore ERR
    # The bridge goes up and is verified before the service is pointed at it, so it never sees a dead port.
    bridge on
    "$K3S_DIR/verify.sh"
    install_dropin
    trap - ERR
    set_marker k3s
    ;;
  down:k3s) "$0" --mode host up; echo "rolled back: the service works in-process again; the pod is scaled to 0 and left in place (kubectl delete ns $NAMESPACE removes it)" ;;
  status:k3s)
    printf '%-26s %-9s sandbox drop-in: %s\n' "$DAEMON" "$(systemctl --user is-active "$DAEMON" 2>/dev/null || true)" \
      "$([ -f "$DROPIN" ] && echo present || echo absent)"
    bridge status || true
    kc -n "$NAMESPACE" get deploy,pods 2>&1 || true ;;
  verify:k3s) "$K3S_DIR/verify.sh" ;;
esac
