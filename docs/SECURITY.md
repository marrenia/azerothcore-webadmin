# Security

## Status of this code

This panel was written by AI agents against a live server. **It has not received a
human line-by-line security review.** Automated tests now cover the route/role
audit and the authentication/scoping logic (see `tests/`), but that is not a
substitute for one. Treat the assurances below as design intent that was checked
by hand and by test, not as an audited guarantee.

## Threat model

The panel is built for:

> A small group of trusted-but-different-privilege operators (an administrator,
> game masters, moderators) and, optionally, the realm's own players, reached over
> a private network (Tailscale/WireGuard/VPN) or - once fronted by the bundled TLS
> proxy - a network you are prepared to expose more broadly.

Login now gates *what* a session can do, not just *whether* it can log in at all.
Multiple roles with different privilege are an explicit, supported case (see
"Roles" below), which is a change from the single-shared-admin model this panel
started with.

### Still explicitly out of scope

- Exposure to the untrusted public internet without a deliberate, separate
  decision to do so (see "Public-internet deployment prerequisites" below) - this
  build is *ready* for that step, it has not *taken* it. The app is not bound to
  a public interface by anything in this repo, and nothing here opens a firewall
  port.
- Non-repudiation / a full audit trail of who did what (world edits do emit
  `WORLD_EDIT` to the journal; most other actions do not)
- Defending a PLAYER-role account holder against their own compromised game
  client/credentials - self-service is bounded by role and account ownership, not
  by detecting a hijacked session belonging to the legitimate owner
- Denial of service beyond the coarse rate limits described below

## Roles

Role is derived from `acore_auth.account_access.gmlevel` (the same numbering
AzerothCore itself uses), read from the row with `RealmID=-1` ("all realms").
No row, or `gmlevel=0`, is **PLAYER**.

| gmlevel | Role | Web panel access |
|---|---|---|
| 0 / no row | PLAYER | Self-service only: own account, own ban/mute state, own password change, own characters (including deleted), own tickets and GM responses. No SOAP is used for any of this - it is all direct, ownership-scoped reads plus a single password UPDATE. |
| 1 | MODERATOR | Ticket queue: list, comment, close (via SOAP). Mute / unmute a character, kick a character. Nothing else - explicitly no bans, no deletes, no World tab, no Customize tab, no Server tab, no Console. |
| 2 | GAMEMASTER | Everything ADMIN has except writing `account_access` (GM levels) and the handful of routes reserved for ADMIN below. |
| 3 | ADMIN | Full access, including granting/revoking GM levels, account/character deletion and restore, the realm list editor, and the raw GM console. Preserves the original single-admin panel's behaviour in full. |

The **bootstrap `WEB_ADMIN`** identity (`webadmin.env`) is not a database account:
it has no `account_id`, is always ADMIN, and cannot be locked or banned through
the database (only by editing or removing `webadmin.env`). It exists so the panel
is administrable before any GM account exists.

Routes reserved for ADMIN even though GAMEMASTER can do almost everything else:
writing `account_access` (gmlevel), account/character delete, deleted-character
purge/restore, the realm list editor, the raw console, and server
restart/shutdown. These are the routes where a mistake or a compromised session
does the most damage or is hardest to reverse.

### How this is enforced

Every route in every blueprint carries an explicit `@require_role(...)`
decorator - not a blueprint-wide `before_request` hook, which is easy to forget
to apply when a new blueprint is added. `core.require_role`:

1. Redirects unauthenticated requests to `/login` (nothing sensitive to leak by
   doing so).
2. Re-checks, on **every** request, whether the account behind the session is
   still allowed to hold one at all: locked, or under a currently-active ban,
   ends the session immediately - not just at the next login. An idle timeout
   (30 minutes) does the same.
3. Re-reads the account's current `gmlevel` on every request, so a GM-level
   change made by an admin takes effect on the affected session's very next
   request, not at its next login.
4. Returns a flat `403` for an authenticated session whose role isn't in the
   route's allow-list - never a redirect, so a logged-in low-privilege user is
   never bounced into a login loop that implies their own session is invalid.

`tests/test_route_audit.py` statically parses every `bp_*.py` file's AST and
fails if any `@bp.route` lacks a `@require_role` decorator, independent of the
running app - a missing decorator is a test failure, not just a hoped-for
convention.

Self-service (`bp_self.py`) additionally scopes every query by the session's own
`account_id` - never by a value taken from the URL or form. `/me/characters/<guid>`
looks the character up and then checks `character.account == session account_id`,
returning `403` (not a leaked 404-vs-403 distinction, and not silently showing
someone else's data) on mismatch. There is no route anywhere that accepts an
`account_id` from the client for a PLAYER-role action.

## Login

- Two credential paths: the bootstrap `WEB_ADMIN` (constant-time compared, as
  before) and any `acore_auth.account` row, verified against the *real* SRP6
  verifier using the same `srp6_make` the account-creation form uses, compared
  with `hmac.compare_digest`. A database account whose password you set with the
  panel, with the in-game client, or with the server's own console all work
  identically here.
- Locked accounts (`account.locked=1`) and accounts with a currently-active ban
  are rejected at login **and** on every subsequent request (see above) - not a
  point-in-time check that a ban applied five minutes into a session ignores.
  This is a simplification of AzerothCore's own "locked" semantics, which
  normally means "restricted to the last IP used to log in": here it means
  "cannot hold a web panel session at all". Documented here because it is a
  deliberate behavioural difference from the game client, not an oversight.
- Both paths return the same generic "Invalid credentials." message and (mostly)
  do the same shape of work, so response content can't be used to enumerate
  which usernames exist. The one residual gap: the bootstrap-admin check is a
  local comparison and the database path does a query plus SRP6 math, so the
  two paths take measurably different time. A network attacker could use that to
  learn whether a given username is *specifically* the bootstrap admin username
  (not whether it is a valid database account - that path's timing is uniform).
  Low value information for a single fixed username; documented rather than
  engineered around.
- Per-IP **and** per-username lockout after 8 failures within 5 minutes, each
  tracked in a bounded in-process dict (oldest/expired entries are pruned once
  either table exceeds 4096 entries, so a hostile client cannot grow it without
  bound - the previous version of this panel could). Still per-worker/per-process
  and resets on restart; still a speed bump, not a durable defence. Rate-limit at
  the TLS proxy (or another layer in front of it) if you need more.
- CSRF: every `POST` requires a session-bound token compared with
  `hmac.compare_digest`, enforced globally in `before_request` so a new
  blueprint cannot forget it. Also enforced: the request `Content-Type` must be
  a real HTML form encoding, not JSON or anything else that couldn't carry the
  token anyway.
- Session cookie: `HttpOnly`, `SameSite=Lax` always; `Secure` when
  `ACORE_WEBADMIN_BEHIND_TLS_PROXY=1` (set automatically by the installer when
  the TLS proxy is enabled - see below). Login clears the session before setting
  new session data (fixation prevention: a pre-login session id is never reused
  post-login). Logout clears the session outright. Sessions are Flask's signed
  client-side cookie, bounded by an 8-hour absolute lifetime and a 30-minute idle
  timeout, both enforced server-side on every request.
- `WEB_SECRET_KEY` is now checked at startup: the app refuses to start if it is
  missing or under 32 characters, rather than silently signing sessions with a
  weak or empty key.

## TLS

A reverse proxy (nginx) terminates TLS in front of the app; the app itself binds
`127.0.0.1` only, always, whether or not the proxy is enabled - it has never been
reachable directly from `BIND_ADDR`.

- **Three ways to get a certificate**, chosen with `TLS_MODE`:
  - `selfsigned` (default) - generated at `$CONFIG_DIR/tls/{fullchain.pem,privkey.pem}`
    if neither file exists. Key is `root:root 0600`, directory `0711`. Browsers
    warn, which over time trains people to click through warnings - treat this as
    a fallback, not a destination.
  - `tailscale` - a real Let's Encrypt certificate for the node's MagicDNS name,
    issued by Tailscale. Browser-trusted with **nothing exposed to the internet**
    and no inbound ports opened. The best option for a private deployment. The
    installer also adds a daily renewal timer; these certs last ~90 days, so
    omitting renewal would just be a delayed outage.
  - `letsencrypt` - certbot over HTTP-01 for a public domain. This necessarily
    means the panel is internet-reachable; read "Public-internet deployment
    prerequisites" below before choosing it.
- **Replacing any of them**: drop a CA-issued `fullchain.pem` and `privkey.pem` at
  the same two paths (same names, same permissions) and `systemctl reload nginx`.
  No source change, no rebuild, no app restart. See docs/CONFIGURATION.md for
  the exact commands.
- Automatic renewal exists for `tailscale` (a timer this repo installs) and
  `letsencrypt` (certbot's own timer, with a hook that reloads nginx). The
  `selfsigned` mode has no renewal: its certificate is valid 825 days and then
  simply expires.
- HTTP on the same `BIND_ADDR` is redirect-only (301 to HTTPS); the app never
  serves plaintext on that address.
- `Strict-Transport-Security` is set by the app itself, but only when
  `ACORE_WEBADMIN_BEHIND_TLS_PROXY=1` - it would be actively wrong to send HSTS
  over a plaintext connection.
- TLS 1.2/1.3 only, a modern cipher list, no session tickets. Verified with
  `openssl s_client` and `curl -v` (see the RBAC/TLS test report for the exact
  commands and output).
- `X-Forwarded-For`/`-Proto` are trusted from exactly one hop (the proxy itself:
  `ProxyFix(x_for=1, x_proto=1)`), and only when `ACORE_WEBADMIN_BEHIND_TLS_PROXY=1`.
  Without the proxy in front, nothing sets those headers and nothing trusts them.
  The proxy itself *overwrites* `X-Forwarded-For` with the real peer address
  rather than appending to whatever the client sent, so a client cannot spoof a
  chain of forwarded-for values.

## Browser-facing headers

Set by the app on every response (`app.py:security_headers`):
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Content-Security-Policy` (`default-src 'self'`, no inline scripts, no framing),
`Referrer-Policy: same-origin`, a restrictive `Permissions-Policy`, and
`Cache-Control: no-store` on any response served to an authenticated session (so
an intermediate cache or a shared machine's browser cache does not retain
account/character data).

## What is done

These were implemented deliberately, and each was verified by hand and/or by an
automated test:

- **Default-deny authorization**: see "Roles" above.
- **CSRF**: as described under "Login".
- **SQL injection**: all user input is passed as query parameters. SQL
  identifiers (database and table names) are fixed constants or configuration,
  never user input.
- **Least-privilege database account**: unchanged from before - table-scoped
  grants only, no `acore_playerbots` access, no `DROP`. See `deploy/grants.sql`.
  RBAC/self-service needed no new grants: everything PLAYER can read was already
  readable by this account, and the one write (self password change) uses the
  `account` table's existing `UPDATE` grant.
- **Credential isolation**: the panel's DB user is not the game server's DB user.
- **Destructive operations delegated to the server**: character and account
  deletion go through the worldserver over loopback SOAP (ADMIN-only routes),
  refused outright when the worldserver is down.
- **Command injection into GM commands**: arguments containing `"`, newline, tab
  or carriage return are rejected rather than escaped.
- **Console deny list**, unchanged, and now ADMIN-only rather than available to
  every logged-in user.
- **systemd sandboxing**: `ProtectSystem=strict`, `ProtectHome=read-only`,
  `NoNewPrivileges`, `MemoryDenyWriteExecute`, a syscall allow-list, restricted
  address families. Unchanged by this work; not weakened.
- **Request size bound**: `MAX_CONTENT_LENGTH=2MB` app-wide; `client_max_body_size 2m`
  at the proxy.
- **Coarse abuse resistance**: a bounded per-IP request counter rejects a source
  address sending more than 120 `POST`s in 60 seconds, on top of the login
  lockout above.
- **No debug mode, no stack traces**: `debug=False` always; 400/403/404/413/429/500
  all render the same generic error template. `FLASK_DEBUG` is never read.

## Known weaknesses

Listed because you should know them, not because they're acceptable everywhere:

1. **The admin password is stored in plaintext** in `webadmin.env` (mode `0640`).
   Compared in constant time, not hashed at rest.
2. **The login lockout and the coarse rate limiter are both per-process**,
   in-memory, and reset on restart. Bounded in size (see "Login"), but not
   durable and not shared across `--workers`.
3. **The SOAP account is a level-3 (Administrator) GM account.** Anything the
   panel asks the server to do over SOAP - which now includes MODERATOR-role
   ticket/mute/kick actions - runs with full GM authority on the worldserver
   side. The web panel's own RBAC does not (cannot) reach into the worldserver's
   own permission model; it only decides which panel routes a given web session
   may call. A MODERATOR cannot reach any panel route that would issue a ban,
   but the SOAP account itself is not scoped down to match.
4. **The console can run arbitrary GM commands.** ADMIN-only now, but a
   compromised ADMIN session is still equivalent to full realm control.
5. **Login-path timing** can reveal whether a given username matches the
   bootstrap admin specifically (see "Login" above).
6. **No durable audit trail** of who took which panel action, beyond
   `WORLD_EDIT` log lines for world data edits and the systemd journal's own
   request logging.
7. **Self-signed TLS by default**: until you install a CA-issued certificate,
   browsers will warn on every visit and nothing validates the proxy's identity
   beyond "some server holds this key". Acceptable for a VPN/tailnet-only
   deployment where the network already implies who you're talking to; not
   acceptable if you are relying on the certificate itself for trust.
8. **Dependency surface**: see `requirements.txt` / the Docker image's pinned
   versions. Run `pip-audit` (or equivalent) periodically; this repo does not
   do so automatically.

## Public-internet deployment prerequisites

This build is closer to public-internet-ready than the original single-admin
panel, but going from "tailnet-only" to "reachable from the internet" is still a
deliberate step this repo does not take for you. Before doing it:

1. **Use a CA-issued certificate**, not the self-signed fallback - install with
   `TLS_MODE=letsencrypt TLS_DOMAIN=... LETSENCRYPT_EMAIL=...`, or drop your own
   certificate in at the documented paths. A self-signed cert trains users to
   click through browser warnings, which defeats the point of TLS.
   Note that `TLS_MODE=letsencrypt` configures TLS and nothing else: it does not
   add accounts, rate limiting, or an audit trail, and the installer prints a
   warning saying so.
2. **Put a real rate limiter / WAF in front of it.** The in-process limiters here
   are a speed bump against a casual attempt, not protection against a
   distributed one.
3. **Narrow the SOAP account's GM level if AzerothCore ever supports scoped SOAP
   credentials** - today it doesn't, so know that MODERATOR-role actions run
   with full GM authority behind the scenes (see "Known weaknesses" #3).
4. **Decide on your own account-lockout/ban policy for internet-facing PLAYER
   self-service.** The 8-attempts/5-minute lockout was sized for a small
   trusted community, not an internet-scale credential-stuffing target.
5. **Firewall/ufw**: this repo does not open a port for you, and this work did
   not touch `ufw`, `sshd`, or `/etc/acore` permissions on the deployment it
   targeted. Opening the panel to the internet means deliberately punching a
   hole for `HTTPS_PORT` (443 by default) to the TLS proxy's `BIND_ADDR` and
   nothing else - never expose the app's own loopback port, and never expose
   the SOAP port.
6. **Re-review PLAYER self-service** for your own community's abuse tolerance -
   it is read-mostly by design (own account/characters/tickets, one password
   change) specifically so it would be low-risk to expose, but "low-risk" is
   not "zero-risk", and this has not been through adversarial testing at
   internet scale.

## Reporting a problem

Open an issue. Given the nature of this project, please don't expect a fast fix -
and please don't report it as though it were production software with a security
team behind it.
