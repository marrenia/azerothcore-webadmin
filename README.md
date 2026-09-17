# azerothcore-webadmin

A web admin panel for a private [AzerothCore](https://www.azerothcore.org/) 3.3.5 realm:
accounts, characters, moderation, a live player tracker, world editing, realm list
editing, and a GM command console.

Flask + gunicorn + MySQL. No build step, no JavaScript framework, no external assets.

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

### 2. It has only ever been tested inside a private tailnet

The one and only deployment this has run on is a single-user private realm, bound to
a [Tailscale](https://tailscale.com/) address, on a host where the public firewall
drops everything except WireGuard and tailnet SSH.

**It has never been exposed to the public internet, and it is not built to be.**

Concretely, it assumes a trusted network and therefore does *not* have:

- **TLS.** It serves plain HTTP. `SESSION_COOKIE_SECURE` is not set, because there
  is no HTTPS to set it for. Session cookies and your admin password cross the
  network in cleartext — which is acceptable over WireGuard and *only* over
  WireGuard.
- **Multi-user accounts, roles, or an audit trail of who did what.** There is one
  admin login. Everyone who has it can do everything.
- **Meaningful brute-force protection.** There is an in-process login lockout, but
  it is per-gunicorn-worker and resets on restart. It is a speed bump, not a defence.
- **CSRF protection beyond a session-bound token**, no rate limiting on actions, and
  no protection against a hostile client on the same trusted network.

The panel binds to whatever address you configure. **Configure it to a private one.**
If you put this on `0.0.0.0` with a public IP, you are handing the internet a login
form that fronts your game database and a GM command console. Don't.

A reasonable deployment is: Tailscale/WireGuard/VPN address, or `127.0.0.1` behind an
authenticating reverse proxy that terminates TLS. The installer defaults to
`127.0.0.1` for exactly this reason.

---

## What it does

| Tab | What's in it |
|---|---|
| **Accounts** | Create (correct SRP6), list, search, set GM level and expansion, change password, delete. Hides playerbot accounts by default. |
| **Characters** | Browse, inspect, rename, set level, kick, teleport, send mail/items/money, force combat stop, restore deleted characters. |
| **Online** | Who is connected right now. |
| **Tracker** | Periodic `pinfo` sampling into SQLite, with per-character history and inferred activity. |
| **Moderation** | Account bans, character bans, IP bans, mutes — with reasons and durations. |
| **World** | Guilds, arena teams, and data reloads. |
| **Customize** | Edit creature and gameobject templates, spawns, vendors and teleport points. |
| **Realms** | Edit the realm list: address, port, type, region, flags, who may log in. |
| **Tickets** | Open GM tickets. |
| **Server** | Live status, MOTD, broadcasts, save-all, realm gate, restart/shutdown. |
| **Console** | Arbitrary GM commands over SOAP, with a deny list for the genuinely destructive ones. |

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
- `SOAP.Enabled = 1` in `worldserver.conf` (loopback only) for anything that issues
  a GM command
- systemd, if you want the supplied unit

## Install

```bash
git clone https://github.com/marrenia/azerothcore-webadmin.git
cd azerothcore-webadmin
sudo ./deploy/install.sh
```

The installer creates a service user, generates the MySQL account and grants,
writes credential files with a generated admin password, installs a systemd unit,
and prints the URL. Full walkthrough and every configurable value:
**[docs/INSTALL.md](docs/INSTALL.md)**.

Nothing in the code is specific to the machine it was written on — paths, bind
address, database names and DB host are all configuration. See
[docs/CONFIGURATION.md](docs/CONFIGURATION.md).

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
