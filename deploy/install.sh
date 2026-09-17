#!/usr/bin/env bash
#
# Installer for acore-webadmin.
#
# Creates a service user, a least-privilege MySQL account, credential files and a
# systemd unit. Safe to re-run: it preserves existing credentials and only fills in
# what is missing.
#
# Everything is overridable from the environment, e.g.
#   sudo env BIND_ADDR=100.64.0.5 PORT=8090 ./deploy/install.sh
#
# Use `sudo env VAR=...`: plain sudo resets the environment, and `sudo -E` is
# rejected by many sudoers configurations, which would silently give you defaults.
#
set -euo pipefail

# ------------------------------------------------------------------ settings

SERVICE_USER="${SERVICE_USER:-acoreweb}"
APP_DIR="${APP_DIR:-/opt/acore-webadmin}"
CONFIG_DIR="${CONFIG_DIR:-/etc/acore}"
STATE_DIR_NAME="${STATE_DIR_NAME:-acore-webadmin}"
SERVICE_NAME="${SERVICE_NAME:-acore-webadmin}"

# Default to loopback on purpose. This panel has no TLS and no multi-user model;
# binding it to a public interface would expose a GM console to the internet.
BIND_ADDR="${BIND_ADDR:-127.0.0.1}"
PORT="${PORT:-8090}"
WORKERS="${WORKERS:-2}"

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-acoreweb}"
AUTH_DB="${AUTH_DB:-acore_auth}"
CHAR_DB="${CHAR_DB:-acore_characters}"
WORLD_DB="${WORLD_DB:-acore_world}"

ADMIN_USER="${ADMIN_USER:-admin}"
BOT_ACCOUNT_PREFIX="${BOT_ACCOUNT_PREFIX:-rndbot}"

SOAP_HOST="${SOAP_HOST:-127.0.0.1}"
SOAP_PORT="${SOAP_PORT:-7878}"
SOAP_USER="${SOAP_USER:-}"          # leave empty to skip SOAP configuration
SOAP_PASS="${SOAP_PASS:-}"

# MySQL admin connection used to create the panel's account. Defaults to socket
# auth as root, which is how a stock Debian/Ubuntu MySQL is set up.
MYSQL_ADMIN=(${MYSQL_ADMIN:-mysql})

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------------ preflight

[[ $EUID -eq 0 ]] || die "Run as root (sudo $0)."

command -v systemctl >/dev/null || die "systemd is required."
command -v mysql     >/dev/null || die "The mysql client is required."
command -v python3   >/dev/null || die "python3 is required."

say "Checking Python dependencies"
missing=()
for mod in flask pymysql gunicorn; do
    python3 -c "import $mod" 2>/dev/null || missing+=("$mod")
done
if (( ${#missing[@]} )); then
    warn "Missing Python modules: ${missing[*]}"
    if command -v apt-get >/dev/null; then
        say "Installing via apt"
        apt-get update -qq
        apt-get install -y -qq python3-flask python3-pymysql python3-gunicorn
    else
        die "Install them however your distro prefers, then re-run. (pip: pip3 install ${missing[*]})"
    fi
fi
for mod in flask pymysql gunicorn; do
    python3 -c "import $mod" 2>/dev/null || die "Still cannot import '$mod'."
done

say "Checking database connectivity"
"${MYSQL_ADMIN[@]}" -N -e "SELECT 1" >/dev/null 2>&1 \
    || die "Cannot connect to MySQL with: ${MYSQL_ADMIN[*]}
       Set MYSQL_ADMIN, e.g. MYSQL_ADMIN='mysql -u root -pSECRET'"

for db in "$AUTH_DB" "$CHAR_DB"; do
    "${MYSQL_ADMIN[@]}" -N -e "USE \`$db\`" 2>/dev/null \
        || die "Database '$db' does not exist. Is this an AzerothCore host?"
done
HAVE_WORLD=1
"${MYSQL_ADMIN[@]}" -N -e "USE \`$WORLD_DB\`" 2>/dev/null || {
    HAVE_WORLD=0
    warn "Database '$WORLD_DB' not found - the Customize tab will not work."
}

# ------------------------------------------------------------------ user + dirs

if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    say "Service user '$SERVICE_USER' already exists"
else
    say "Creating service user '$SERVICE_USER'"
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

say "Installing application to $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/app/." "$APP_DIR/"
find "$APP_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod -R go-w "$APP_DIR"

# 0711: each service user can traverse to its own 0640 file without being able to
# list the directory or read anyone else's credentials.
mkdir -p "$CONFIG_DIR"
chmod 711 "$CONFIG_DIR"
chown root:root "$CONFIG_DIR"

write_env() {  # write_env <file> <content...>
    local file="$1"; shift
    if [[ -f "$file" ]]; then
        say "Keeping existing $file"
        return 1
    fi
    printf '%s\n' "$@" > "$file"
    chown "root:$SERVICE_USER" "$file"
    chmod 640 "$file"
    say "Wrote $file"
    return 0
}

# ------------------------------------------------------------------ database

DB_ENV="$CONFIG_DIR/webdb.env"
if [[ -f "$DB_ENV" ]]; then
    say "Reusing existing database credentials"
    DB_PASS="$(grep -E '^WEB_DB_PASS=' "$DB_ENV" | cut -d= -f2-)"
else
    DB_PASS="$(openssl rand -base64 30 | tr -d '/+=' | head -c 32)"
fi
[[ -n "$DB_PASS" ]] || die "Could not determine a database password."

say "Creating MySQL user '$DB_USER'@'localhost' and grants"
"${MYSQL_ADMIN[@]}" <<SQL
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
SQL

# Table-scoped grants only. Rendered from grants.sql so the documented set and the
# applied set cannot drift apart.
GRANT_SQL="$(sed -e "s/{{AUTH_DB}}/${AUTH_DB}/g" \
                 -e "s/{{CHAR_DB}}/${CHAR_DB}/g" \
                 -e "s/{{WORLD_DB}}/${WORLD_DB}/g" \
                 -e "s/{{DB_USER}}/${DB_USER}/g" \
                 "$SRC_DIR/deploy/grants.sql")"
if (( HAVE_WORLD == 0 )); then
    GRANT_SQL="$(printf '%s\n' "$GRANT_SQL" | grep -v "\`${WORLD_DB}\`")"
fi
printf '%s\n' "$GRANT_SQL" | "${MYSQL_ADMIN[@]}"
"${MYSQL_ADMIN[@]}" -e "FLUSH PRIVILEGES;"

write_env "$DB_ENV" \
    "WEB_DB_HOST=$DB_HOST" \
    "WEB_DB_PORT=$DB_PORT" \
    "WEB_DB_USER=$DB_USER" \
    "WEB_DB_PASS=$DB_PASS" \
    "AUTH_DB=$AUTH_DB" \
    "CHAR_DB=$CHAR_DB" \
    "WORLD_DB=$WORLD_DB" || true

say "Verifying the panel's own database account works"
mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" \
      -N -e "SELECT COUNT(*) FROM \`$AUTH_DB\`.account" >/dev/null \
    || die "The '$DB_USER' account cannot read $AUTH_DB.account."

# ------------------------------------------------------------------ app config

ADMIN_ENV="$CONFIG_DIR/webadmin.env"
GENERATED_PASS=""
if [[ ! -f "$ADMIN_ENV" ]]; then
    GENERATED_PASS="$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)"
    write_env "$ADMIN_ENV" \
        "WEB_ADMIN_USER=$ADMIN_USER" \
        "WEB_ADMIN_PASS=$GENERATED_PASS" \
        "WEB_SECRET_KEY=$(openssl rand -hex 32)" \
        "BOT_ACCOUNT_PREFIX=$BOT_ACCOUNT_PREFIX" || true
else
    say "Keeping existing admin credentials"
fi

SOAP_ENV="$CONFIG_DIR/soap.env"
if [[ -n "$SOAP_USER" && -n "$SOAP_PASS" ]]; then
    write_env "$SOAP_ENV" \
        "SOAP_HOST=$SOAP_HOST" "SOAP_PORT=$SOAP_PORT" \
        "SOAP_USER=$SOAP_USER" "SOAP_PASS=$SOAP_PASS" || true
elif [[ ! -f "$SOAP_ENV" ]]; then
    warn "No SOAP credentials given - writing a placeholder."
    warn "Anything that issues a GM command will fail until you fix $SOAP_ENV."
    write_env "$SOAP_ENV" \
        "SOAP_HOST=$SOAP_HOST" "SOAP_PORT=$SOAP_PORT" \
        "SOAP_USER=CHANGEME" "SOAP_PASS=CHANGEME" || true
fi

# ------------------------------------------------------------------ systemd

say "Installing systemd unit '$SERVICE_NAME.service'"
sed -e "s|{{SERVICE_USER}}|$SERVICE_USER|g" \
    -e "s|{{APP_DIR}}|$APP_DIR|g" \
    -e "s|{{CONFIG_DIR}}|$CONFIG_DIR|g" \
    -e "s|{{STATE_DIR_NAME}}|$STATE_DIR_NAME|g" \
    -e "s|{{BIND_ADDR}}|$BIND_ADDR|g" \
    -e "s|{{PORT}}|$PORT|g" \
    -e "s|{{WORKERS}}|$WORKERS|g" \
    "$SRC_DIR/deploy/acore-webadmin.service.template" \
    > "/etc/systemd/system/$SERVICE_NAME.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
systemctl restart "$SERVICE_NAME"

say "Waiting for the service to answer"
ok=0
for _ in $(seq 1 20); do
    if curl -fsS -o /dev/null "http://$BIND_ADDR:$PORT/healthz" 2>/dev/null; then ok=1; break; fi
    sleep 1
done

echo
if (( ok )); then
    say "Installed and responding."
else
    warn "Service did not answer on http://$BIND_ADDR:$PORT/healthz"
    warn "Check: journalctl -u $SERVICE_NAME -n 50 --no-pager"
fi

echo
echo "  URL       http://$BIND_ADDR:$PORT/"
echo "  Username  $ADMIN_USER"
if [[ -n "$GENERATED_PASS" ]]; then
    echo "  Password  $GENERATED_PASS"
    echo
    echo "  ^ generated now, stored in $ADMIN_ENV. Save it somewhere."
else
    echo "  Password  (unchanged - see $ADMIN_ENV)"
fi
echo
if [[ "$BIND_ADDR" != "127.0.0.1" ]] && [[ "$BIND_ADDR" != "localhost" ]]; then
    warn "Bound to $BIND_ADDR. This panel has no TLS and one shared admin login."
    warn "Make sure that address is only reachable from a network you trust."
fi
