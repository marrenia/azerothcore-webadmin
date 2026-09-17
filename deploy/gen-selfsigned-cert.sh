#!/usr/bin/env bash
#
# Generates a self-signed TLS certificate/key pair at the panel's stable,
# documented cert paths - the zero-configuration fallback used until you
# drop in a CA-issued certificate. Safe to re-run: does nothing if both
# files already exist, so it never clobbers a real certificate you installed.
#
# Usage: gen-selfsigned-cert.sh <cert_path> <key_path> <days> [cn] [owner:group]
#
set -euo pipefail

CERT_PATH="${1:?cert path required}"
KEY_PATH="${2:?key path required}"
DAYS="${3:-825}"
CN="${4:-localhost}"
OWNER="${5:-root:root}"

if [[ -f "$CERT_PATH" && -f "$KEY_PATH" ]]; then
    echo "==> TLS cert already present at $CERT_PATH - leaving it alone"
    exit 0
fi

mkdir -p "$(dirname "$CERT_PATH")" "$(dirname "$KEY_PATH")"
chmod 711 "$(dirname "$CERT_PATH")"

echo "==> Generating a self-signed certificate (CN=$CN, ${DAYS}d) at $CERT_PATH"
openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$KEY_PATH" -out "$CERT_PATH" -days "$DAYS" \
    -subj "/CN=${CN}" \
    -addext "subjectAltName=DNS:${CN},IP:127.0.0.1" \
    >/dev/null 2>&1

chown "$OWNER" "$CERT_PATH" "$KEY_PATH"
chmod 644 "$CERT_PATH"
chmod 600 "$KEY_PATH"

echo "==> Done. This is a self-signed fallback certificate: browsers will warn"
echo "    until you replace it with a CA-issued one at the same two paths."
