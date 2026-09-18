#!/usr/bin/env bash
#
# Renew the panel's Tailscale-issued Let's Encrypt certificate.
#
# Tailscale certs are valid ~90 days. Installing TLS without renewal is just a
# scheduled outage, so deploy/install.sh installs this behind a daily timer when
# TLS_MODE=tailscale.
#
# `tailscale cert` is a no-op until the certificate is close to expiry, so this
# is cheap to run daily. nginx is reloaded only when the certificate actually
# changed - reloading every day would be pointless churn - and only after
# `nginx -t` passes, so a bad certificate cannot take the panel down.
#
# Usage: tls-renew-tailscale.sh <domain> <cert_path> <key_path>
#
set -euo pipefail

DOMAIN="${1:?domain required}"
CERT="${2:?cert path required}"
KEY="${3:?key path required}"

before="$(sha256sum "$CERT" 2>/dev/null | cut -d' ' -f1 || echo none)"

tailscale cert --cert-file "$CERT" --key-file "$KEY" "$DOMAIN"

chown root:root "$CERT" "$KEY"
chmod 644 "$CERT"
chmod 600 "$KEY"

after="$(sha256sum "$CERT" | cut -d' ' -f1)"
expires="$(openssl x509 -in "$CERT" -noout -enddate | cut -d= -f2)"

if [[ "$before" != "$after" ]]; then
    nginx -t
    systemctl reload nginx
    echo "acore-tls-renew: certificate renewed, nginx reloaded (expires $expires)"
else
    echo "acore-tls-renew: certificate unchanged (expires $expires)"
fi
