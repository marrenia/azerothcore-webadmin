#!/bin/sh
# Builds the three credential files the app expects (see core.py's
# load_env()) from the environment, then execs the real command.
#
# Supports plain env vars and the Docker/Compose secrets convention: for any
# of the secret variables below, setting "<NAME>_FILE=/path" reads the value
# from that file instead (e.g. a Docker secret mounted at
# /run/secrets/web_admin_pass). A plain env var wins if both are set, so a
# compose override always takes priority over a baked-in secret file.
#
# Fails loudly and refuses to start gunicorn if anything required is missing
# or looks like a placeholder - this is a container, "warn and write a
# placeholder" (what the bare-metal installer does) has no operator standing
# next to it to notice the warning.
set -eu

CONFIG_DIR="${ACORE_WEBADMIN_CONFIG_DIR:-/run/acore-webadmin/config}"
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

resolve() {  # resolve VAR_NAME -> prints value, preferring VAR over VAR_FILE
    var="$1"
    file_var="${1}_FILE"
    eval "val=\${$var:-}"
    eval "file=\${$file_var:-}"
    if [ -n "$val" ]; then
        printf '%s' "$val"
    elif [ -n "$file" ] && [ -r "$file" ]; then
        tr -d '\n' < "$file"
    else
        printf ''
    fi
}

require() {  # require VAR_NAME "purpose" -> die with a clear message if empty
    val="$(resolve "$1")"
    if [ -z "$val" ]; then
        echo "FATAL: $1 (or ${1}_FILE) is not set - $2" >&2
        exit 1
    fi
    printf '%s' "$val"
}

WEB_DB_HOST="$(resolve WEB_DB_HOST)"; WEB_DB_HOST="${WEB_DB_HOST:-127.0.0.1}"
WEB_DB_PORT="$(resolve WEB_DB_PORT)"; WEB_DB_PORT="${WEB_DB_PORT:-3306}"
WEB_DB_USER="$(require WEB_DB_USER "the app's own least-privilege MySQL account")"
WEB_DB_PASS="$(require WEB_DB_PASS "that account's password")"
AUTH_DB="$(resolve AUTH_DB)"; AUTH_DB="${AUTH_DB:-acore_auth}"
CHAR_DB="$(resolve CHAR_DB)"; CHAR_DB="${CHAR_DB:-acore_characters}"
WORLD_DB="$(resolve WORLD_DB)"; WORLD_DB="${WORLD_DB:-acore_world}"

WEB_ADMIN_USER="$(require WEB_ADMIN_USER "the bootstrap admin username")"
WEB_ADMIN_PASS="$(require WEB_ADMIN_PASS "the bootstrap admin password")"
WEB_SECRET_KEY="$(require WEB_SECRET_KEY "the Flask session-signing key - generate with: openssl rand -hex 32")"
if [ "${#WEB_SECRET_KEY}" -lt 32 ]; then
    echo "FATAL: WEB_SECRET_KEY is too short (need >=32 chars) - generate with: openssl rand -hex 32" >&2
    exit 1
fi
BOT_ACCOUNT_PREFIX="$(resolve BOT_ACCOUNT_PREFIX)"; BOT_ACCOUNT_PREFIX="${BOT_ACCOUNT_PREFIX:-rndbot}"

SOAP_HOST="$(resolve SOAP_HOST)"; SOAP_HOST="${SOAP_HOST:-127.0.0.1}"
SOAP_PORT="$(resolve SOAP_PORT)"; SOAP_PORT="${SOAP_PORT:-7878}"
SOAP_USER="$(require SOAP_USER "the worldserver GM account the panel commands as")"
SOAP_PASS="$(require SOAP_PASS "that account's password")"

umask 077
cat > "$CONFIG_DIR/webdb.env" <<EOF
WEB_DB_HOST=$WEB_DB_HOST
WEB_DB_PORT=$WEB_DB_PORT
WEB_DB_USER=$WEB_DB_USER
WEB_DB_PASS=$WEB_DB_PASS
AUTH_DB=$AUTH_DB
CHAR_DB=$CHAR_DB
WORLD_DB=$WORLD_DB
EOF
cat > "$CONFIG_DIR/webadmin.env" <<EOF
WEB_ADMIN_USER=$WEB_ADMIN_USER
WEB_ADMIN_PASS=$WEB_ADMIN_PASS
WEB_SECRET_KEY=$WEB_SECRET_KEY
BOT_ACCOUNT_PREFIX=$BOT_ACCOUNT_PREFIX
EOF
cat > "$CONFIG_DIR/soap.env" <<EOF
SOAP_HOST=$SOAP_HOST
SOAP_PORT=$SOAP_PORT
SOAP_USER=$SOAP_USER
SOAP_PASS=$SOAP_PASS
EOF
chmod 600 "$CONFIG_DIR"/*.env

exec "$@"
