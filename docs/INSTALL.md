# Installation

## Before you start

Read the warnings in the [README](../README.md) and [SECURITY.md](SECURITY.md).
Short version: this is vibecoded, it has no TLS, it has one admin login, and it has
only ever run inside a private tailnet. Bind it to a private address.

You need:

- An AzerothCore 3.3.5 realm whose databases are already imported and reachable
- MySQL or MariaDB, and a way to connect as an administrative user
- Python 3.9+ with `flask`, `pymysql`, `gunicorn`
- systemd
- `openssl` and `curl` (used by the installer)

## Quick install

```bash
git clone https://github.com/marrenia/azerothcore-webadmin.git
cd azerothcore-webadmin
sudo ./deploy/install.sh
```

Defaults: binds `127.0.0.1:8090`, service user `acoreweb`, app in
`/opt/acore-webadmin`, config in `/etc/acore`.

The installer prints a generated admin password at the end. **Save it** — it is
stored only in `/etc/acore/webadmin.env`.

## Binding to a private network address

The default is loopback. To reach the panel from another machine, bind it to a
VPN/tailnet address:

```bash
# Tailscale example
sudo env BIND_ADDR="$(tailscale ip -4)" ./deploy/install.sh
```

Use `sudo env VAR=value ...`, not `VAR=value sudo ...` and not `sudo -E`. sudo
resets the environment by default, and many sudoers configurations refuse `-E`
outright (`sudo: preserving the entire environment is not supported`) — in which
case your settings are silently ignored and you get the defaults.

Do not bind to `0.0.0.0` or a public IP.

## Configuring SOAP

Anything that issues a GM command — deleting characters, kicking, teleporting,
broadcasts, the console, live tracker samples — goes through the worldserver's SOAP
interface. Without it the panel still runs, but those features fail.

In `worldserver.conf`:

```ini
SOAP.Enabled = 1
SOAP.IP      = 127.0.0.1
SOAP.Port    = 7878
```

Keep it on loopback. Then create a GM account for the panel, in the worldserver
console:

```
account create panelsoap <password>
account set gmlevel panelsoap 3 -1
```

Pass it to the installer:

```bash
sudo env SOAP_USER=panelsoap SOAP_PASS='<password>' ./deploy/install.sh
```

Or edit `/etc/acore/soap.env` afterwards and restart the service.

## Installing without the script

1. Create a system user: `useradd --system --no-create-home --shell /usr/sbin/nologin acoreweb`
2. Copy `app/` to `/opt/acore-webadmin`, owned by that user
3. Create the MySQL account and apply `deploy/grants.sql` with the placeholders replaced
4. Create `/etc/acore` mode `0711`, and the three `*.env` files mode `0640`,
   owned `root:acoreweb` — see [CONFIGURATION.md](CONFIGURATION.md) for their contents
5. Render `deploy/acore-webadmin.service.template` into `/etc/systemd/system/`
6. `systemctl daemon-reload && systemctl enable --now acore-webadmin`

## Verifying

```bash
systemctl status acore-webadmin
curl -s http://127.0.0.1:8090/healthz
```

`/healthz` returns `{"status":"ok","worldserver":"up"}` when the database is
reachable and the worldserver answers SOAP. It returns `503` if the database is
down, and `"worldserver":"down"` if only SOAP is unavailable.

## Upgrading

```bash
git pull
sudo ./deploy/install.sh
```

Re-running is safe: existing credential files are kept, the MySQL grants are
re-applied, and the service restarts. Only the application files are replaced.

## Uninstalling

```bash
sudo systemctl disable --now acore-webadmin
sudo rm /etc/systemd/system/acore-webadmin.service
sudo systemctl daemon-reload
sudo rm -rf /opt/acore-webadmin /var/lib/acore-webadmin
sudo rm -f /etc/acore/webdb.env /etc/acore/webadmin.env /etc/acore/soap.env
sudo mysql -e "DROP USER 'acoreweb'@'localhost';"
sudo userdel acoreweb
```

Nothing in the game databases is modified by uninstalling. The SOAP GM account, if
you made one, has to be deleted separately from the worldserver console.

## Troubleshooting

**`Cannot connect to MySQL`** — the installer connects as `mysql` with socket auth.
If your setup needs a password:
`sudo env MYSQL_ADMIN='mysql -u root -pSECRET' ./deploy/install.sh`

**`No module named gunicorn`** — Debian and Ubuntu ship `python3-gunicorn` without a
`/usr/bin/gunicorn` binary. The unit invokes `python3 -m gunicorn`, which works. If
you installed gunicorn with pip into a venv, point `ExecStart` at the venv's Python.

**Service starts then immediately fails** — usually a credential file it cannot read.
Check `journalctl -u acore-webadmin -n 50`. The files must be mode `0640`, owned
`root:acoreweb`, in a directory the service user can traverse (`0711`).

**`gunicorn: error: unrecognized arguments: --no-control-socket`** — you are on
gunicorn < 23. Remove that flag from the unit. It exists because gunicorn 25 opens a
control socket in the working directory, which fails under `ProtectSystem=strict`.

**Customize tab errors** — the world database grants are missing, or `WORLD_DB`
points at the wrong name. See `deploy/grants.sql`.

**Everything involving a GM command fails** — SOAP is not configured, the worldserver
is down, or the SOAP account's GM level is below 3.
