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
| `ACORE_WEBADMIN_BEHIND_TLS_PROXY` | `0` | Set to `1` when a TLS-terminating reverse proxy sits in front of the app (the installer sets this for you when `ENABLE_TLS=1`). Turns on `SESSION_COOKIE_SECURE`, `Strict-Transport-Security`, and trusting one hop of `X-Forwarded-For`/`-Proto` from the proxy. Never set this without an actual proxy in front — with nothing to strip forged headers, the app would trust whatever the client sent. |

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

## TLS

`deploy/install.sh` puts nginx in front of the app and terminates TLS there. The
app itself always binds `127.0.0.1` only whenever a proxy is present — there is
never a direct route to it from `BIND_ADDR`.

`TLS_MODE` picks how the certificate is obtained.

| Mode | Cert | Browser warning | Needs |
|---|---|---|---|
| `selfsigned` *(default)* | generated locally | **yes** | nothing |
| `tailscale` | real Let's Encrypt, via Tailscale | no | a tailnet with HTTPS Certificates enabled |
| `letsencrypt` | real Let's Encrypt, via certbot | no | a public domain + inbound 80/443 |
| `none` | — | n/a | app binds `BIND_ADDR` directly, plaintext |

### `TLS_MODE=tailscale` — recommended for private deployments

The best option if you already reach the host over Tailscale: a genuine,
browser-trusted certificate with **nothing exposed to the internet**. Tailscale
performs the ACME dance; no ports are opened and no DNS is published.

```bash
sudo env TLS_MODE=tailscale ./deploy/install.sh
```

The installer detects this node's MagicDNS name, binds the tailnet IP if
`BIND_ADDR` is still the loopback default, and installs a **daily renewal timer**
(`<service>-tls-renew.timer`) — Tailscale certs last ~90 days, so shipping
without renewal would just be a scheduled outage. It reloads nginx only when the
certificate actually changes, and only after `nginx -t` passes.

Prerequisite: enable **HTTPS Certificates** in the Tailscale admin console under
DNS. Without it `tailscale cert` fails and the installer stops with that message
rather than silently falling back to a self-signed cert.

If the host runs `tailscale up --accept-dns=false`, it cannot resolve its own
`*.ts.net` name. The installer adds a `/etc/hosts` entry so local health checks
work, rather than handing your system resolver to tailscaled.

### `TLS_MODE=letsencrypt` — public-facing

**Read [SECURITY.md](SECURITY.md) first.** This panel has one shared admin login,
a login lockout that resets on restart, no audit trail, and a GM console that is
one session hijack away from full realm control. HTTPS stops eavesdropping. It
does not make any of that safe to expose.

```bash
sudo env TLS_MODE=letsencrypt \
  TLS_DOMAIN=panel.example.com \
  LETSENCRYPT_EMAIL=you@example.com \
  BIND_ADDR=0.0.0.0 \
  ./deploy/install.sh
```

`TLS_DOMAIN` must already resolve to this host and port 80 must be reachable —
certbot validates over HTTP-01. The installer brings up an HTTP-only vhost that
answers `/.well-known/acme-challenge/`, obtains the certificate, then renders the
real config. Renewal is certbot's own systemd timer, with a deploy hook that
reloads nginx.

Test with `LETSENCRYPT_STAGING=1` first. Let's Encrypt's production rate limit
for a domain is low and a few failed attempts will lock you out for a week.

### All TLS variables

| Variable | Default | Purpose |
|---|---|---|
| `TLS_MODE` | `selfsigned` | `selfsigned` \| `tailscale` \| `letsencrypt` \| `none` |
| `ENABLE_TLS` | `1` | Back-compat. `0` is equivalent to `TLS_MODE=none`. |
| `TLS_DOMAIN` | *(auto for tailscale)* | Hostname users type. **Required** for `letsencrypt`. |
| `LETSENCRYPT_EMAIL` | — | **Required** for `letsencrypt`; receives expiry notices. |
| `LETSENCRYPT_STAGING` | `0` | `1` uses the staging CA — untrusted certs, no rate limit. |
| `ACME_WEBROOT` | `/var/www/<service>-acme` | Where HTTP-01 challenge files are served from. |
| `HTTPS_PORT` | `443` | Where nginx listens for HTTPS on `BIND_ADDR`. |
| `HTTP_PORT` | `80` | Plain HTTP on `BIND_ADDR`: ACME challenges, otherwise 301 to HTTPS. |
| `LEGACY_REDIRECT_PORT` | — | An older port to keep answering with a 301, so existing bookmarks survive. |
| `TLS_CERT_PATH` | `$CONFIG_DIR/tls/fullchain.pem` | Certificate (+ chain) nginx serves. Ignored for `letsencrypt`, which uses certbot's own path. |
| `TLS_KEY_PATH` | `$CONFIG_DIR/tls/privkey.pem` | Its private key. Written `root:root 0600`. |
| `TLS_CN` | `$BIND_ADDR` | CN/SAN for the self-signed fallback only. |

### Replacing the self-signed certificate

The installer generates a self-signed cert at `TLS_CERT_PATH`/`TLS_KEY_PATH` only
if neither file already exists — it never overwrites a certificate you installed.
To install a CA-issued (or otherwise externally obtained) certificate:

```bash
sudo install -o root -g root -m 644 /path/to/your/fullchain.pem "$TLS_CERT_PATH"
sudo install -o root -g root -m 600 /path/to/your/privkey.pem  "$TLS_KEY_PATH"
sudo nginx -t && sudo systemctl reload nginx
```

No source change, no rebuild, no restart of `acore-webadmin` itself — only nginx
needs to reload, and `nginx -t` validates the config before it touches the
running service. Renewal (e.g. via certbot/ACME on a real domain) is the same
two `install` commands plus a reload, on whatever schedule your CA requires; this
repo does not automate certificate issuance or renewal.

### Running without TLS

`ENABLE_TLS=0` is supported for a purely loopback, single-machine setup where a
proxy adds nothing (e.g. `BIND_ADDR=127.0.0.1` with SSH port-forwarding for
remote access). It restores the original direct-bind behaviour: the app binds
`BIND_ADDR:PORT` itself, in plaintext, and `ACORE_WEBADMIN_BEHIND_TLS_PROXY` stays
`0`. Do not use this with a non-loopback `BIND_ADDR`.

## Docker / Compose

`Dockerfile` and `docker-compose.yml` at the repo root are a portable
alternative to the systemd installer, for running the app anywhere Docker runs.
They do **not** bundle AzerothCore's own MySQL or worldserver — point them at
your existing ones via `.env` (copy `.env.example` to start).

| Variable (in `.env`) | Purpose |
|---|---|
| `WEB_DB_HOST`, `WEB_DB_PORT`, `WEB_DB_USER`, `WEB_DB_PASS` | Same meaning as `webdb.env` above. `WEB_DB_HOST` almost always needs to be your Docker host's LAN/VPN address, not `127.0.0.1` — `127.0.0.1` inside the container is the container itself. |
| `AUTH_DB`, `CHAR_DB`, `WORLD_DB` | Same as above. |
| `WEB_ADMIN_USER`, `WEB_ADMIN_PASS`, `WEB_SECRET_KEY` | Same as `webadmin.env`. There is no generated default here — the container refuses to start without them (see `app.py`'s startup check on `WEB_SECRET_KEY`). |
| `SOAP_HOST`, `SOAP_PORT`, `SOAP_USER`, `SOAP_PASS` | Same as `soap.env`. `SOAP_HOST` has the same "not 127.0.0.1" caveat as `WEB_DB_HOST`. |
| `TLS_BIND_ADDR` | Host interface the proxy container publishes `HTTPS_PORT`/`HTTP_PORT` on. Keep this a private/VPN address, exactly as with the systemd install — Compose does not change that guidance. |
| `HTTPS_PORT`, `HTTP_PORT` | Same meaning as the installer variables above. |

Certificates: bind-mount a host directory to `/certs` in the `proxy` service
(see the `volumes:` entry in `docker-compose.yml`). If it's empty on first start,
an init step generates the same self-signed fallback the systemd installer does.
Replace `/certs/fullchain.pem` and `/certs/privkey.pem` on the host and run
`docker compose exec proxy nginx -s reload` — no rebuild.

Only the `proxy` service publishes ports to the host; `app` is reachable solely
over the internal Compose network. See `docker-compose.yml` for the full
resource/security settings (non-root, read-only root filesystem where
practical, dropped capabilities, `no-new-privileges`, a healthcheck on
`/healthz`).

## Installer variables

`deploy/install.sh` reads these from the environment. Pass them with
`sudo env VAR=value ./deploy/install.sh` — plain `sudo` resets the environment, and
`sudo -E` is rejected by many sudoers configurations.

| Variable | Default |
|---|---|
| `BIND_ADDR` | `127.0.0.1` |
| `PORT` | `8090` |
| `WORKERS` | `2` |
| `ENABLE_TLS` | `1` |
| `HTTPS_PORT` | `443` |
| `HTTP_PORT` | `80` |
| `TLS_CERT_PATH` | `$CONFIG_DIR/tls/fullchain.pem` |
| `TLS_KEY_PATH` | `$CONFIG_DIR/tls/privkey.pem` |
| `TLS_CN` | `$BIND_ADDR` |
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
