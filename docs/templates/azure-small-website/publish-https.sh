#!/usr/bin/env bash
# Publish this template onto one existing Azure VM stack.
# The stack must already allow inbound TCP 80 and 443.
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" || "$#" != 1 ]]; then
  echo "Usage: publish-https.sh <stack>" >&2
  exit 2
fi

STACK="$1"
[[ "$STACK" =~ ^[a-z0-9][a-z0-9-]{0,22}$ ]] || {
  echo "stack must be a short lowercase name" >&2
  exit 1
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEMPLATE_DIR="${REPO_ROOT}/docs/templates/azure-small-website"
if [[ "$STACK" =~ ^[a-z0-9]{3,8}$ ]]; then
  NAME_PREFIX="${AZURE_VM_NAME_PREFIX:-$STACK}"
else
  NAME_PREFIX="${AZURE_VM_NAME_PREFIX:?set AZURE_VM_NAME_PREFIX}"
fi
RESOURCE_GROUP="${AZURE_VM_RESOURCE_GROUP:-rg-${STACK}}"
VM_NAME="${NAME_PREFIX}-vm"
STATE_DIR="${AZURE_VM_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/share}/traction-control/azure-vm/${STACK}}"
CERTBOT="${CERTBOT:-certbot}"
SITE_HTML="${SITE_HTML:-${TEMPLATE_DIR}/index.html}"

command -v az >/dev/null 2>&1 || { echo "az is required" >&2; exit 1; }
command -v "$CERTBOT" >/dev/null 2>&1 || { echo "certbot 5.4 or newer is required" >&2; exit 1; }
[[ -f "$SITE_HTML" && -f "${TEMPLATE_DIR}/https-server.py" ]] || {
  echo "template files are missing" >&2
  exit 1
}

IP="$(az network public-ip show \
  --resource-group "$RESOURCE_GROUP" \
  --name "${NAME_PREFIX}-pip" \
  --query ipAddress -o tsv --only-show-errors)"
[[ "$IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || {
  echo "the stack has no IPv4 public address" >&2
  exit 1
}

install -d -m 700 \
  "${STATE_DIR}/letsencrypt/config" \
  "${STATE_DIR}/letsencrypt/work" \
  "${STATE_DIR}/letsencrypt/logs" \
  "${STATE_DIR}/run"

cat > "${STATE_DIR}/acme-auth.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
: "${CERTBOT_TOKEN:?}"
: "${CERTBOT_VALIDATION:?}"
: "${AZURE_WEBSITE_RESOURCE_GROUP:?}"
: "${AZURE_WEBSITE_VM:?}"
: "${AZURE_WEBSITE_IP:?}"
az vm run-command invoke \
  --resource-group "$AZURE_WEBSITE_RESOURCE_GROUP" \
  --name "$AZURE_WEBSITE_VM" \
  --command-id RunShellScript \
  --scripts "install -d -m 755 /var/www/html/.well-known/acme-challenge && printf '%s\n' '${CERTBOT_VALIDATION}' > '/var/www/html/.well-known/acme-challenge/${CERTBOT_TOKEN}' && chmod 644 '/var/www/html/.well-known/acme-challenge/${CERTBOT_TOKEN}'" \
  --query "value[0].message" -o tsv --only-show-errors >/dev/null
curl -fsS --max-time 20 \
  "http://${AZURE_WEBSITE_IP}/.well-known/acme-challenge/${CERTBOT_TOKEN}" \
  | grep -F -x "${CERTBOT_VALIDATION}"
EOF
chmod 700 "${STATE_DIR}/acme-auth.sh"

export AZURE_WEBSITE_RESOURCE_GROUP="$RESOURCE_GROUP"
export AZURE_WEBSITE_VM="$VM_NAME"
export AZURE_WEBSITE_IP="$IP"
"$CERTBOT" certonly \
  --non-interactive \
  --agree-tos \
  --register-unsafely-without-email \
  --preferred-profile shortlived \
  --manual \
  --preferred-challenges http \
  --manual-auth-hook "${STATE_DIR}/acme-auth.sh" \
  --keep-until-expiring \
  --ip-address "$IP" \
  --key-type ecdsa \
  --config-dir "${STATE_DIR}/letsencrypt/config" \
  --work-dir "${STATE_DIR}/letsencrypt/work" \
  --logs-dir "${STATE_DIR}/letsencrypt/logs"

LIVE="${STATE_DIR}/letsencrypt/config/live/${IP}"
CERT_B64="$(base64 -w0 "${LIVE}/fullchain.pem")"
KEY_B64="$(base64 -w0 "${LIVE}/privkey.pem")"
HTML_B64="$(base64 -w0 "$SITE_HTML")"
PY_B64="$(base64 -w0 "${TEMPLATE_DIR}/https-server.py")"
cat > "${STATE_DIR}/run/install-https.sh" << EOF
set -eu
install -d -m 755 /var/www/html
install -d -m 700 /etc/azure-website
printf '%s' '${HTML_B64}' | base64 -d > /var/www/html/index.html
printf '%s' '${CERT_B64}' | base64 -d > /etc/azure-website/fullchain.pem
printf '%s' '${KEY_B64}' | base64 -d > /etc/azure-website/privkey.pem
chmod 600 /etc/azure-website/fullchain.pem /etc/azure-website/privkey.pem
printf '%s' '${PY_B64}' | base64 -d > /usr/local/sbin/azure-website.py
chmod 755 /usr/local/sbin/azure-website.py
cat > /etc/systemd/system/azure-website.service << 'UNIT'
[Unit]
Description=Small public website
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/sbin/azure-website.py
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable azure-website.service
if systemctl is-active grocery-list.service >/dev/null 2>&1; then
  systemctl disable --now grocery-list.service
fi
systemctl restart azure-website.service
systemctl is-active azure-website.service
EOF
chmod 700 "${STATE_DIR}/run/install-https.sh"
az vm run-command invoke \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --command-id RunShellScript \
  --scripts @"${STATE_DIR}/run/install-https.sh" \
  --query "value[0].message" -o tsv --only-show-errors
rm -f "${STATE_DIR}/run/install-https.sh"
find "${STATE_DIR}/letsencrypt" "${STATE_DIR}/run" -type d -exec chmod 700 {} +
find "${STATE_DIR}/letsencrypt" "${STATE_DIR}/run" -type f -exec chmod 600 {} +
echo "HTTPS site published for ${STACK}"
