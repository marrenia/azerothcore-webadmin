# Configuration

All configuration lives in three files in `$CONFIG_DIR` (default `/etc/acore`), plus
two environment variables set by the systemd unit. There is no config file in the
application directory.

Each file is `KEY=value`, one per line. `#` comments and blank lines are ignored.
Values are taken literally — **no quoting, no escaping**. A trailing space is part of
the value.

All three files should be mode `0640`, owned `root:<service-user>`, in a directory
mode `0711`.

## Environment variables

Set in the systemd unit, not in the env files.

| Variable | Default | Purpose |
|---|---|---|
| `ACORE_WEBADMIN_CONFIG_DIR` | `/etc/acore` | Where the three files below live. Change it to run two panels against two realms on one host. |
| `ACORE_STATE_DIR` | `/var/lib/acore-webadmin` | Writable directory for the tracker's SQLite database. Must match `StateDirectory`. |

## `webdb.env` — database

| Key | Default | Purpose |
|---|---|---|
| `WEB_DB_HOST` | `127.0.0.1` | MySQL host |
| `WEB_DB_PORT` | `3306` | MySQL port |
| `WEB_DB_USER` | *required* | The panel's own least-privilege account |
| `WEB_DB_PASS` | *required* | Its password |
| `AUTH_DB` | `acore_auth` | Auth database name |
| `CHAR_DB` | `acore_characters` | Characters database name |
| `WORLD_DB` | `acore_world` | World database name |

The three `*_DB` keys exist for installs that renamed the stock databases. If you
change them, re-apply `deploy/grants.sql` with matching names.

```ini
WEB_DB_HOST=127.0.0.1
WEB_DB_PORT=3306
WEB_DB_USER=acoreweb
WEB_DB_PASS=generated-by-the-installer
AUTH_DB=acore_auth
CHAR_DB=acore_characters
WORLD_DB=acore_world
```

## `webadmin.env` — the panel itself

| Key | Default | Purpose |
|---|---|---|
| `WEB_ADMIN_USER` | *required* | The single admin username |
| `WEB_ADMIN_PASS` | *required* | Its password, **stored in plaintext** (see SECURITY.md) |
| `WEB_SECRET_KEY` | *required* | Flask session signing key. Changing it logs everyone out. |
| `BOT_ACCOUNT_PREFIX` | `rndbot` | Accounts whose username starts with this are treated as playerbots and hidden by default |

```ini
WEB_ADMIN_USER=admin
WEB_ADMIN_PASS=change-me
WEB_SECRET_KEY=64-hex-chars-from-openssl-rand-hex-32
BOT_ACCOUNT_PREFIX=rndbot
```

To change the admin password, edit the file and
`systemctl restart acore-webadmin`. It is read once at startup.

`WEB_SECRET_KEY` must be random and secret — anyone who knows it can forge a session
cookie and bypass the login entirely.

## `soap.env` — worldserver command interface

| Key | Default | Purpose |
|---|---|---|
| `SOAP_HOST` | `127.0.0.1` | Must match `SOAP.IP` in `worldserver.conf` |
| `SOAP_PORT` | `7878` | Must match `SOAP.Port` |
| `SOAP_USER` | *required* | A GM account, level 3 |
| `SOAP_PASS` | *required* | Its password |

```ini
SOAP_HOST=127.0.0.1
SOAP_PORT=7878
SOAP_USER=panelsoap
SOAP_PASS=change-me
```

Keep SOAP on loopback. It is an unauthenticated-by-network, HTTP-Basic-authenticated
remote command interface with full GM authority.

## Installer variables

`deploy/install.sh` reads these from the environment. Pass them with
`sudo env VAR=value ./deploy/install.sh` — plain `sudo` resets the environment, and
`sudo -E` is rejected by many sudoers configurations.

| Variable | Default |
|---|---|
| `BIND_ADDR` | `127.0.0.1` |
| `PORT` | `8090` |
| `WORKERS` | `2` |
| `SERVICE_USER` | `acoreweb` |
| `SERVICE_NAME` | `acore-webadmin` |
| `APP_DIR` | `/opt/acore-webadmin` |
| `CONFIG_DIR` | `/etc/acore` |
| `STATE_DIR_NAME` | `acore-webadmin` |
| `DB_HOST`, `DB_PORT`, `DB_USER` | `127.0.0.1`, `3306`, `acoreweb` |
| `AUTH_DB`, `CHAR_DB`, `WORLD_DB` | stock AzerothCore names |
| `ADMIN_USER` | `admin` |
| `BOT_ACCOUNT_PREFIX` | `rndbot` |
| `SOAP_HOST`, `SOAP_PORT`, `SOAP_USER`, `SOAP_PASS` | `127.0.0.1`, `7878`, empty, empty |
| `MYSQL_ADMIN` | `mysql` |

### Running two panels on one host

```bash
sudo env \
SERVICE_NAME=acore-webadmin-pvp \
CONFIG_DIR=/etc/acore-pvp \
APP_DIR=/opt/acore-webadmin-pvp \
SERVICE_USER=acoreweb-pvp \
STATE_DIR_NAME=acore-webadmin-pvp \
DB_USER=acoreweb_pvp \
AUTH_DB=pvp_auth CHAR_DB=pvp_characters WORLD_DB=pvp_world \
PORT=8091 \
./deploy/install.sh
```
