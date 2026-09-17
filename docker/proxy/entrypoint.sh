#!/bin/sh
# Runs as /docker-entrypoint.d/00-tls-bootstrap.sh, before the official nginx
# image's own entrypoint scripts (which render nginx.conf.template with
# envsubst and then start nginx). Only job here: make sure a certificate and
# key exist at the mounted cert path, generating the self-signed fallback if
# neither is present yet. Never overwrites an existing certificate.
set -eu

CERT_PATH="${TLS_CERT_PATH:-/certs/fullchain.pem}"
KEY_PATH="${TLS_KEY_PATH:-/certs/privkey.pem}"

if [ -f "$CERT_PATH" ] && [ -f "$KEY_PATH" ]; then
    echo "TLS bootstrap: certificate already present at $CERT_PATH - leaving it alone"
    exit 0
fi

mkdir -p "$(dirname "$CERT_PATH")"
echo "TLS bootstrap: generating a self-signed fallback certificate at $CERT_PATH"
openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$KEY_PATH" -out "$CERT_PATH" -days 825 \
    -subj "/CN=${TLS_CN:-localhost}" \
    -addext "subjectAltName=DNS:${TLS_CN:-localhost},IP:127.0.0.1" \
    >/dev/null 2>&1

chmod 600 "$KEY_PATH"
chmod 644 "$CERT_PATH"
echo "TLS bootstrap: done. Replace both files and restart the proxy container for a real certificate."
