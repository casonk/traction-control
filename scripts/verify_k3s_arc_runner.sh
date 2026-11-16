#!/usr/bin/env bash
# verify_k3s_arc_runner.sh — prove the runner network boundary on a Cilium K3s node. Read-mostly; no sudo.
#
# Starts a temporary pod in arc-runners with the same labels and security context as a runner pod, probes from inside
# it, and deletes it. Run it after installing or changing the policy, Cilium, or the node's network. Exit status is
# the number of failed probes (0 = the boundary holds). A probe pod is never a job: it runs no workflow code.
#
# Allowed: GitHub, the Actions hosts, PyPI, the Go proxy (443). Denied: unlisted hosts and ports, DNS for unlisted
# names, resolvers other than CoreDNS, the node, the API server, the LAN gateway, link-local and cloud metadata,
# other pods and services, ingress from the node and from other pods; no service-account token, read-only root,
# no sudo.
set -uo pipefail

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/k3s.yaml}"
NS=arc-runners
POD="probe-$$"
IMAGE="${ARC_PROBE_IMAGE:-ghcr.io/actions/actions-runner:2.338.2}"
fail=0

command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 99; }
kubectl get ns "$NS" >/dev/null 2>&1 || { echo "namespace $NS missing: install the controller first" >&2; exit 99; }

cleanup() {
  kubectl -n "$NS" delete pod "$POD" --wait=false >/dev/null 2>&1 || true
  kubectl -n default delete pod "$POD-ing" --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

NODE_IP="$(kubectl get node -o jsonpath='{range .items[0].status.addresses[?(@.type=="InternalIP")]}{.address}{"\n"}{end}' | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1)"
GATEWAY="$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')"
HOST_POD_IP="$(ip -4 addr show cilium_host 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')"
API_SVC="$(kubectl -n default get svc kubernetes -o jsonpath='{.spec.clusterIP}')"
OTHER_POD_IP="$(kubectl get pods -A -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.namespace}{" "}{.status.podIP}{"\n"}{end}' \
  | awk '$1!="arc-runners" && $1!="kube-system" && $2!=""{print $2; exit}')"
OTHER_SVC_IP="$(kubectl get svc -A -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.spec.clusterIP}{"\n"}{end}' \
  | awk '$1!="default" && $1!="kube-system" && $2 ~ /^[0-9]/{print $2; exit}')"

kubectl -n "$NS" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $POD
  namespace: $NS
  labels: {app.kubernetes.io/component: runner}
spec:
  automountServiceAccountToken: false
  restartPolicy: Never
  securityContext: {runAsNonRoot: true, runAsUser: 1001, runAsGroup: 123, seccompProfile: {type: RuntimeDefault}}
  containers:
    - name: p
      image: $IMAGE
      command: ["python3", "-m", "http.server", "8080"]
      securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
EOF
kubectl -n "$NS" wait --for=condition=Ready "pod/$POD" --timeout=120s >/dev/null || { echo "probe pod did not start" >&2; exit 99; }
# A probe against a malformed address would "pass" as blocked for the wrong reason, so anything that is not a plain
# IPv4 address is dropped and its probe is reported as skipped.
for var in NODE_IP GATEWAY HOST_POD_IP API_SVC OTHER_POD_IP OTHER_SVC_IP; do
  [[ ${!var} =~ ^[0-9]+(\.[0-9]+){3}$ ]] || printf -v "$var" ''
done
POD_IP="$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.podIP}')"

probe() { # label want(allow|deny) command...
  local label="$1" want="$2" got verdict=FAIL; shift 2
  if kubectl -n "$NS" exec "$POD" -- bash -c "$*" >/dev/null 2>&1; then got=reachable; else got=blocked; fi
  if { [[ $want == allow && $got == reachable ]]; } || { [[ $want == deny && $got == blocked ]]; }; then verdict=ok; else fail=$((fail + 1)); fi
  printf '%-4s %-9s want=%-5s %s\n' "$verdict" "$got" "$want" "$label"
}
skip() { printf '%-4s %-9s %-10s %s\n' skip - - "$1 (no address found)"; }
CURL='curl -sS -m 6 -o /dev/null'

probe "github.com:443"                      allow "$CURL https://github.com"
probe "api.github.com:443"                  allow "$CURL https://api.github.com"
probe "pypi.org:443"                        allow "$CURL https://pypi.org/simple/"
probe "proxy.golang.org:443"                allow "$CURL https://proxy.golang.org"
probe "DNS resolves a listed name"          allow "getent hosts github.com"
probe "example.com:443 (unlisted)"          deny  "$CURL https://example.com"
probe "github.com:80"                       deny  "$CURL http://github.com"
probe "DNS refuses an unlisted name"        deny  "getent hosts example.com"
probe "resolver 1.1.1.1:53 directly"        deny  "timeout 5 bash -c 'exec 3<>/dev/tcp/1.1.1.1/53'"
[[ -n $NODE_IP ]] && probe "node $NODE_IP:22"            deny "$CURL telnet://$NODE_IP:22" || skip "node ssh"
[[ -n $NODE_IP ]] && probe "node $NODE_IP:6443 (API)"    deny "$CURL -k https://$NODE_IP:6443" || skip "node API"
[[ -n $HOST_POD_IP ]] && probe "node pod-network $HOST_POD_IP:9091" deny "$CURL http://$HOST_POD_IP:9091" || skip "cilium_host"
[[ -n $API_SVC ]] && probe "kubernetes service $API_SVC:443" deny "$CURL -k https://$API_SVC:443" || skip "kubernetes service"
[[ -n $GATEWAY ]] && probe "LAN gateway $GATEWAY:80"     deny "$CURL http://$GATEWAY" || skip "gateway"
[[ -n $GATEWAY ]] && probe "LAN gateway $GATEWAY:443"    deny "$CURL -k https://$GATEWAY" || skip "gateway tls"
probe "cloud metadata 169.254.169.254"      deny  "$CURL http://169.254.169.254/"
probe "Azure wireserver 168.63.129.16"      deny  "$CURL http://168.63.129.16/"
[[ -n $OTHER_POD_IP ]] && probe "another pod $OTHER_POD_IP" deny "$CURL http://$OTHER_POD_IP:5000/" || skip "other pod"
[[ -n $OTHER_SVC_IP ]] && probe "another service $OTHER_SVC_IP" deny "$CURL http://$OTHER_SVC_IP/" || skip "other service"
probe "service-account token mounted"       deny  "test -e /var/run/secrets/kubernetes.io/serviceaccount/token"
probe "write to the root filesystem"        deny  "touch /usr/x"
probe "sudo"                                deny  "sudo -n true"

# Ingress: the probe pod serves HTTP; neither the node nor another pod may reach it.
ingress() { # label want command...
  local label="$1" got verdict=FAIL; shift
  if "$@" >/dev/null 2>&1; then got=reachable; else got=blocked; fi
  if [[ $got == blocked ]]; then verdict=ok; else fail=$((fail + 1)); fi
  printf '%-4s %-9s want=%-5s %s\n' "$verdict" "$got" deny "$label"
}
if kubectl -n "$NS" exec "$POD" -- curl -s -m 3 -o /dev/null http://127.0.0.1:8080; then
  ingress "ingress from the node"        curl -s -m 5 -o /dev/null "http://$POD_IP:8080"
  ingress "ingress from another pod"     kubectl -n default run "$POD-ing" --rm -i --restart=Never --image="$IMAGE" \
    --overrides='{"spec":{"securityContext":{"runAsUser":1001}}}' --command -- curl -s -m 5 -o /dev/null -f "http://$POD_IP:8080"
else
  echo "FAIL probe server did not answer on loopback: ingress not tested"; fail=$((fail + 1))
fi

echo
if ((fail)); then echo "$fail probe(s) FAILED: do not enroll repositories until the boundary is fixed"; else echo "all probes passed"; fi
exit "$fail"
