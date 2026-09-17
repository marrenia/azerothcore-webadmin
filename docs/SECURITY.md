# Security

## Status of this code

This panel was written by AI agents against a live server. **It has not received a
human line-by-line security review.** There is no test suite. Treat the assurances
below as design intent that was checked by hand, not as audited guarantees.

## Threat model

The panel is built for exactly one situation:

> A single trusted administrator, on a private network (Tailscale/WireGuard/VPN or
> loopback), administering their own private realm.

Everyone who can reach the listening port is assumed to be trusted-but-for-the-login.
The login exists so that another device on your tailnet cannot casually administer
your realm — **not** to withstand a determined attacker with network access.

### Explicitly out of scope

- Exposure to the public internet
- Hostile clients on the same network
- Multiple administrators with different privilege levels
- Non-repudiation / "who deleted this character" auditing
- Denial of service

## What is deliberately absent

| Missing | Why | What you should do |
|---|---|---|
| TLS | Assumes WireGuard/tailnet already encrypts the link | Put it behind a TLS-terminating reverse proxy if your network isn't encrypted |
| `SESSION_COOKIE_SECURE` | Cannot be set without HTTPS | Set it if you add TLS |
| Multi-user accounts / roles | One admin, by design | — |
| Durable brute-force lockout | In-process dict; per-worker, resets on restart | Rate-limit at a reverse proxy if you need it |
| Action rate limiting | Trusted single user | — |
| Audit log of admin actions | Not built | World edits do emit `WORLD_EDIT` to the journal |

## What *is* done

These were implemented deliberately, and each was verified by hand:

- **CSRF**: every `POST` requires a session-bound token compared with
  `hmac.compare_digest`. Enforced globally in a `before_request` hook, so a new
  blueprint cannot forget it. Verified: a POST without a token returns 400.
- **SQL injection**: all user input is passed as query parameters. SQL identifiers
  (database and table names) are fixed constants or configuration, never user input.
- **Least-privilege database account**: the panel's MySQL user has table-scoped
  grants only. It has no access to `acore_playerbots`, cannot `DROP`, and has
  read-only access to most character tables. See `deploy/grants.sql`.
- **Credential isolation**: the panel's DB user is not the game server's DB user.
  Credential files are `0640`, owned `root:<service-user>`, in a `0711` directory so
  each service can read its own file and not list or read the others'.
- **Destructive operations delegated to the server**: character and account deletion
  go through the worldserver over loopback SOAP, so the server's own cleanup runs.
  Refused outright when the worldserver is down, rather than falling back to SQL.
- **Command injection into GM commands**: arguments containing `"`, newline, tab or
  carriage return are rejected rather than escaped, because the server parses a
  command as a single line.
- **Console deny list**: the raw console refuses `account delete`, `character erase`,
  `character deleted purge` and `server exit`. This is a guard against mistakes, not
  against a malicious operator — they could still do damage by other means.
- **systemd sandboxing**: `ProtectSystem=strict`, `ProtectHome=read-only`,
  `NoNewPrivileges`, `MemoryDenyWriteExecute`, a syscall allow-list, and a restricted
  address family set. The app has no write access to disk outside its `StateDirectory`.
- **Login**: constant-time comparison of username and password.

## Known weaknesses

Listed because you should know them, not because they're acceptable everywhere:

1. **The admin password is stored in plaintext** in `webadmin.env` (mode `0640`).
   It is compared in constant time, but it is not hashed at rest. Anyone who can
   read that file, or who has root, has the password.
2. **The login lockout is per-process.** With `--workers 2`, the effective attempt
   count before lockout is roughly double the configured value, and it resets on
   service restart.
3. **`_login_failures` grows unbounded** — one entry per source IP, never evicted.
   Irrelevant on a tailnet, a memory leak on a hostile network.
4. **The SOAP account is a level-3 (Administrator) GM account.** Anything the panel
   can ask the server to do, it does with full GM authority.
5. **No CSP or security headers** are set.
6. **The console can run arbitrary GM commands.** That is its purpose. It means a
   session hijack is equivalent to full realm control.

## Reporting a problem

Open an issue. Given the nature of this project, please don't expect a fast fix —
and please don't report it as though it were production software with a security
team behind it.
