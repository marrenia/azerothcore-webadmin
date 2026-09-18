# azerothcore-webadmin

A web admin panel for a private [AzerothCore](https://www.azerothcore.org/) 3.3.5 realm:
accounts, characters, moderation, a live player tracker, world editing, realm list
editing, a GM command console, and role-based access down to a read-mostly
self-service view for ordinary players.

Flask + gunicorn + MySQL, TLS-terminated by a bundled nginx reverse proxy. No
build step, no JavaScript framework, no external assets. Runnable directly on a
host via `deploy/install.sh`, or as a portable Docker Compose stack - see
[Install](#install) below.

## Roles

Login now works with any `acore_auth` database account, not just a single shared
admin. Role comes straight from AzerothCore's own `gmlevel`:

| gmlevel | Role | Can do |
|---|---|---|
| 0 / none | **PLAYER** | View their own account, ban/mute state, characters (incl. deleted) and ticket history; change their own password. No SOAP, no access to anyone else's data. |
| 1 | **MODERATOR** | Ticket queue (list/comment/close), mute/unmute, kick. Nothing else. |
| 2 | **GAMEMASTER** | Everything below except granting GM levels and the handful of ADMIN-only routes (account/character delete & restore, realm list, console, server power). |
| 3 | **ADMIN** | Everything, unchanged from the original single-admin panel. |

The bootstrap `WEB_ADMIN` login from `webadmin.env` is always ADMIN and exists
independent of any database account, so the panel is administrable from a clean
install. Full detail: [docs/SECURITY.md](docs/SECURITY.md#roles).

---

## Read this before you deploy it

Two things you need to know, up front, because they decide whether this project is
appropriate for you:

### 1. This is vibecoded

This panel was written by AI agents (Claude) working against a live server, with a
human directing the work. It was built fast, by prompt, and shaped by what happened
to break during testing.

What that means in practice:

- **It has not had a human line-by-line security review.** Nobody has audited this
  the way you would audit software that holds admin credentials to a database.
- The design decisions in here are real and were verified against a running server
  (see `docs/DESIGN-NOTES.md`), but they were reached empirically — try it, watch it
  fail, fix it — not from deep familiarity with the AzerothCore codebase.
- There is no test suite. Verification was manual: run it, hit the endpoint, check
  the database, read the server log.
- Expect rough edges. Expect bugs that a test suite would have caught.

If you need something you can trust with an exposed, multi-user, or commercial
realm, this is not that. Use it as a starting point, or read it and steal the parts
that are useful.

### 2. It was developed and tested inside a private tailnet

The one and only live deployment this has run on is bound to a
[Tailscale](https://tailscale.com/) address, on a host where the public firewall
drops everything except WireGuard/tailnet traffic and tailnet SSH.

**It has never been exposed to the public internet.** It is now closer to
public-internet-ready than the original single-admin build - TLS, role-based
access, ownership-scoped self-service, security headers and coarse rate limiting
are all now present - but going public is still a deliberate step this repo does
not take for you. Read
[docs/SECURITY.md § Public-internet deployment prerequisites](docs/SECURITY.md#public-internet-deployment-prerequisites)
before you consider it.

What changed and what's still true:

- **TLS is now bundled**, with three ways to get a certificate (`TLS_MODE`):
  a self-signed fallback needing nothing, a **real Let's Encrypt certificate via
  Tailscale** for the node's MagicDNS name (browser-trusted, nothing exposed to
  the internet), or **certbot** for a public domain. Renewal is automatic for the
  latter two. The app itself binds loopback only, always, and HTTP is
  redirect-only. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md#tls).
- **Roles exist now** — see the table above — but there is still no full audit
  trail of who did what beyond world-edit log lines and the systemd journal.
- **Login lockout** is per-IP and per-username, bounded in memory, but still
  per-process and resets on restart. It is a speed bump, not a defence against a
  determined distributed attempt.
- **CSRF** is enforced globally, with a strict content-type check alongside it.

The panel — and the TLS proxy in front of it — bind to whatever address you
configure. **Configure it to a private one.** If you put either on a public
interface, you are handing the internet a login form that fronts your game
database and, for ADMIN, a GM command console. Don't. The installer defaults to
`127.0.0.1` for exactly this reason, and nothing in this repo opens a firewall
port for you.

---

## What it does

| Tab | What's in it |
|---|---|
| **Accounts** | Create (correct SRP6), list, search, set GM level and expansion, change password, delete. Hides playerbot accounts by default. |
| **Characters** | Browse, inspect, rename, set level, kick, teleport, send mail/items/money, force combat stop, restore deleted characters. |
| **Online** | Who is connected right now. |
| **Tracker** | Periodic `pinfo` sampling into SQLite, with per-character history and inferred activity. |
| **Moderation** | Account bans, character bans, IP bans, mutes — with reasons and durations. GAMEMASTER+. |
| **Mute / Kick** | Mute, unmute, kick — the moderation subset MODERATOR can reach. |
| **World** | Guilds, arena teams, and data reloads. GAMEMASTER+. |
| **Customize** | Edit creature and gameobject templates, spawns, vendors and teleport points. GAMEMASTER+. |
| **Realms** | Edit the realm list: address, port, type, region, flags, who may log in. ADMIN only. |
| **Tickets** | Open GM tickets: list, comment, close. MODERATOR+. |
| **Server** | Live status, MOTD, broadcasts, save-all, realm gate. GAMEMASTER+; restart/shutdown is ADMIN only. |
| **Console** | Arbitrary GM commands over SOAP, with a deny list for the genuinely destructive ones. ADMIN only. |
| **My Account** | PLAYER (and everyone else's own account): own details, ban/mute state, password change, own characters including deleted, own tickets and GM responses. |

## How it's put together

Three deliberate decisions, each of which came from something going wrong:

**Deletes go through the server, not the database.** Deleting a character by removing
rows while the worldserver is running is wrong: the server caches character data and
will happily rewrite the rows you just deleted, and raw deletion skips COD-mail
return, guild leadership handoff, group/arena/petition cleanup, and pet deletion. So
the panel issues `character erase` over loopback SOAP and lets the server's own
`Player::DeleteFromDB` run. If the worldserver is down, the delete buttons are
disabled rather than falling back to SQL.

**The database user is not the game server's database user.** The panel gets its own
MySQL account with table-scoped grants — full CRUD on the handful of `acore_auth`
tables it manages, read-only on most character tables, and narrow access to the
specific `acore_world` tables the customizer edits. It cannot drop a table, cannot
touch `acore_playerbots`, and cannot read the game server's credentials.

**Live state comes from the server, not the database.** The `characters` table is
stale between periodic saves — a character can be in Dun Morogh while the row still
says The Hinterlands. Anything presented as "current" is read out of the running
server via `pinfo`. Where that isn't possible (exact coordinates, bot AI strategy),
the UI says so instead of showing you a stale number dressed up as a live one.

## Requirements

- A working AzerothCore 3.3.5 realm with its databases in MySQL/MariaDB
- Python 3.9+, `python3-flask`, `python3-pymysql`, `python3-gunicorn`
- `nginx` and `openssl` for the bundled TLS proxy (installed automatically by
  `deploy/install.sh` unless `ENABLE_TLS=0`)
- `SOAP.Enabled = 1` in `worldserver.conf` (loopback only) for anything that issues
  a GM command
- systemd, if you want the supplied unit — or Docker + Compose, see below

## Install

### Directly on a host (systemd)

```bash
git clone https://github.com/marrenia/azerothcore-webadmin.git
cd azerothcore-webadmin
sudo ./deploy/install.sh
```

If you reach the host over Tailscale, this gets you a properly trusted
certificate with no ports exposed and automatic renewal:

```bash
sudo env TLS_MODE=tailscale ./deploy/install.sh
```

The installer creates a service user, generates the MySQL account and grants,
writes credential files with a generated admin password, installs a systemd unit,
sets up the TLS proxy, and prints the URL. Full
walkthrough and every configurable value: **[docs/INSTALL.md](docs/INSTALL.md)**.

Nothing in the code is specific to the machine it was written on — paths, bind
address, database names and DB host are all configuration. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

### Docker / Compose

```bash
git clone https://github.com/marrenia/azerothcore-webadmin.git
cd azerothcore-webadmin
cp .env.example .env   # edit: point at your existing AzerothCore MySQL + SOAP
docker compose up -d --build
```

Builds the app image (non-root, multi-stage, pinned base) and runs it behind the
same nginx TLS proxy config used by the host installer, on an internal Compose
network — only the proxy's ports are published to the host. AzerothCore's own
databases and worldserver are **not** bundled; point `.env` at your existing
ones. Full detail, including certificate replacement and production notes:
**[docs/CONFIGURATION.md](docs/CONFIGURATION.md#docker--compose)**.

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — installation, upgrade, uninstall
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — every setting and env var
- [docs/SECURITY.md](docs/SECURITY.md) — threat model and what is deliberately absent
- [docs/DESIGN-NOTES.md](docs/DESIGN-NOTES.md) — AzerothCore behaviours learned the hard way
- [docs/WORLD-CUSTOMIZER.md](docs/WORLD-CUSTOMIZER.md) — world editing safety model
- [docs/ASSET-NOTICE.md](docs/ASSET-NOTICE.md) — visual assets

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Blizzard Entertainment or the AzerothCore
project. World of Warcraft is a trademark of Blizzard Entertainment. This tool
contains no Blizzard assets.
