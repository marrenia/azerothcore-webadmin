# Installation

## Before you start

Read the warnings in the [README](../README.md) and [SECURITY.md](SECURITY.md).
Short version: this is vibecoded and it has only ever been run inside a
private tailnet. It now ships TLS and role-based access, but neither turns it
into software that has had a real security review.

You need:

- An AzerothCore 3.3.5 realm whose databases are already imported and reachable
- MySQL or MariaDB, and a way to connect as an administrative user
- Python 3.9+ with `flask`, `pymysql`, `gunicorn`
- systemd
- `openssl` and `curl` (used by the installer)
- nginx — installed automatically unless you pass `TLS_MODE=none`

Optional, depending on how you want your certificate issued:

- `tailscale`, for `TLS_MODE=tailscale`
- `certbot`, for `TLS_MODE=letsencrypt` (installed automatically on apt systems)

## Quick install

```bash
git clone https://github.com/marrenia/azerothcore-webadmin.git
cd azerothcore-webadmin
sudo ./deploy/install.sh
```

Defaults: nginx terminates TLS on `127.0.0.1:443` with a self-signed
certificate, the app itself binds `127.0.0.1:8090` and is never reachable
directly, service user `acoreweb`, app in `/opt/acore-webadmin`, config in
`/etc/acore`.

The installer prints a generated admin password at the end. **Save it** — it is
stored only in `/etc/acore/webadmin.env`.

That default certificate is self-signed, so browsers will warn. See
[TLS](#tls) below for how to get a properly trusted one — on a tailnet it is a
single extra variable.

## Binding to a private network address

The default is loopback. To reach the panel from another machine, bind it to a
VPN/tailnet address:

```bash
sudo env BIND_ADDR="$(tailscale ip -4)" ./deploy/install.sh
```

Use `sudo env VAR=value ...`, not `VAR=value sudo ...` and not `sudo -E`. sudo
resets the environment by default, and many sudoers configurations refuse `-E`
outright (`sudo: preserving the entire environment is not supported`) — in which
case your settings are silently ignored and you get the defaults.

`BIND_ADDR` is where **nginx** listens. The app process always binds `127.0.0.1`
regardless, so there is exactly one route in and it is TLS-terminated.

Only bind `0.0.0.0` if you have deliberately decided to expose the panel to the
internet — see `TLS_MODE=letsencrypt` below and the prerequisites in
[SECURITY.md](SECURITY.md).

## TLS

`TLS_MODE` decides how the certificate is obtained. Full reference:
[CONFIGURATION.md](CONFIGURATION.md#tls).

| Mode | Certificate | Browser warning |
|---|---|---|
| `selfsigned` *(default)* | generated locally | yes |
| `tailscale` | real Let's Encrypt, via Tailscale | no |
| `letsencrypt` | real Let's Encrypt, via certbot | no |
| `none` | none — plain HTTP | n/a |

**On a tailnet**, this is the one to use. It gets a genuinely trusted
certificate without exposing anything to the internet or opening any port:

```bash
sudo env TLS_MODE=tailscale ./deploy/install.sh
```

It detects the node's MagicDNS name, binds the tailnet IP, and installs a daily
renewal timer. Requires **HTTPS Certificates** enabled for the tailnet in the
Tailscale admin console under DNS.

**Public-facing** needs a domain already resolving to the host and inbound
port 80 for the ACME challenge:

```bash
sudo env TLS_MODE=letsencrypt \
  TLS_DOMAIN=panel.example.com \
  LETSENCRYPT_EMAIL=you@example.com \
  BIND_ADDR=0.0.0.0 \
  ./deploy/install.sh
```

Test with `LETSENCRYPT_STAGING=1` first — Let's Encrypt's production rate limit
per domain is low, and a few failed attempts lock you out for a week.

If you are moving an existing install behind TLS, `LEGACY_REDIRECT_PORT=8090`
keeps the old address answering with a redirect so bookmarks survive.

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
systemctl status acore-webadmin nginx
# the app, directly on loopback
curl -s http://127.0.0.1:8090/healthz
# through the TLS proxy (-k only because a self-signed cert is expected here)
curl -sk https://127.0.0.1/healthz
```

With `TLS_MODE=tailscale` or `letsencrypt`, drop the `-k` and use the real
hostname — if it does not verify without `-k`, the certificate is not actually
trusted and something is wrong:

```bash
curl -s https://your-host.example.com/healthz
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

# TLS bits, if you installed with a TLS_MODE other than none
sudo rm -f /etc/nginx/sites-enabled/acore-webadmin.conf \
           /etc/nginx/sites-available/acore-webadmin.conf
sudo systemctl disable --now acore-webadmin-tls-renew.timer 2>/dev/null
sudo rm -f /etc/systemd/system/acore-webadmin-tls-renew.{service,timer} \
           /usr/local/sbin/acore-webadmin-tls-renew.sh
sudo rm -rf /etc/acore/tls /var/www/acore-webadmin-acme
sudo systemctl daemon-reload && sudo nginx -t && sudo systemctl reload nginx
```

certbot certificates under `/etc/letsencrypt/` are left alone deliberately —
other services may be using them. Remove one with
`sudo certbot delete --cert-name <domain>`.

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

**`tailscale cert` fails** — HTTPS Certificates are not enabled for the tailnet.
Turn them on in the admin console under DNS. The installer stops rather than
silently falling back to a self-signed certificate, so you find out now instead
of when a browser complains.

**Tailscale mode works but the host cannot resolve its own name** — expected if
tailscaled runs with `--accept-dns=false`. The installer adds an `/etc/hosts`
entry so local health checks work; it deliberately does not take over the system
resolver, because a tailnet DNS problem would then break apt and MySQL lookups.

**certbot cannot validate the domain** — the name must resolve to this host and
port 80 must be reachable from the internet. The installer serves the challenge
from `ACME_WEBROOT` via an HTTP-only vhost before the real config is rendered;
if something else already owns port 80, that will fail.

**Browser warns about the certificate** — you are on `TLS_MODE=selfsigned`. That
is the default and expected. Switch to `tailscale` or `letsencrypt`, or drop a
CA-issued pair at the documented paths and reload nginx.

**nginx fails to start after install** — run `sudo nginx -t`. The stock `default`
site is removed by the installer because it binds `0.0.0.0:80`; if you restored
it, it will collide with the panel vhost.
