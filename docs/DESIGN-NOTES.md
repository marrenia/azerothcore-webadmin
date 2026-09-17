# Design notes

AzerothCore behaviours that shaped this panel. Each of these was found by hitting it
on a running 3.3.5 realm with `mod-playerbots`, not by reading documentation. They
are written down because every one of them cost time.

## Deleting things

**Never delete character rows while the worldserver is running.** The server holds a
character cache (`sCharacterCache`) and will rewrite rows from it. Raw deletion also
skips COD-mail return to senders, guild leadership handoff, group/arena/petition
cleanup, and pet deletion.

Use `character erase <name>` over SOAP instead, which runs the server's own
`Player::DeleteFromDB`. Measured on one bot character: 681 child rows across 16
tables removed, zero orphans. Deleting an account removed 10 characters and 359
child rows.

**`character erase` resolves names through the server's character cache.** A
character inserted directly with SQL will not be found. Test with real characters.

**If the worldserver is down, refuse the delete.** Falling back to SQL is the bug.

## Live state vs. the database

**The `characters` table is stale between periodic saves.** Observed a bot live in
Dun Morogh while the row still said zone 47 (The Hinterlands). Never present a
database position as current.

**`pinfo <name>` reads the in-memory player object** — map, zone, area, alive state,
level/XP, money, session length, latency. This is genuinely live: a teleport showed
up in the next sample within seconds.

**Exact coordinates are unreachable from a web panel.** `gps` is `Console::No`. The
only way is `saveall` followed by reading the database, which is expensive and still
a snapshot.

**Bot AI strategy is not exposed.** `playerbots rndbot stats` writes through
`LOG_INFO` to the server log, so SOAP returns an empty result. Activity in the
tracker is *inferred* by diffing consecutive samples — zone change, XP gain, money
delta, death, level-up. The UI says "inferred" because it is.

## GM commands

**Check `Console::Yes` before building UI for a command.** Grep
`src/server/scripts/Commands/cs_*.cpp`.

Console-capable: `guild create/delete/rename/invite/uninvite/rank`,
`arena create/disband/rename/lookup`, every `reload`,
`send mail/money/items/message`, `tele name/id/guid`, `kick`, `mute`,
`combatstop`, `pinfo`.

**Not** console-capable — they need an in-world GM position, so no web panel can
drive them: `gps`, `appear`, `summon`, `groupsummon`, `npc add`, `gobject add`,
`die`, `freeze`, `maxskill`, `arena captain`.

**`server set motd` takes a locale first**: `server set motd enUS <text>`. Without
it the first word is parsed as the locale and the command errors. Valid locales:
`enUS koKR frFR deDE zhCN zhWE esES esMX ruRU`.

**`announce` succeeds with empty output.** Absence of output is not failure. Assert
on "no SOAP fault", not on log content.

**`server shutdown` exits 0; `server restart` exits 2.** Under systemd with
`Restart=on-failure`, that means *shutdown leaves the realm down* and *restart brings
it back*. Label them differently in any UI — they are not variations of one action.

**Command arguments are a single parsed line.** Reject `"`, newline, tab and
carriage return in user-supplied arguments rather than trying to escape them.

**Giving items** works via `send items <char> "<subj>" "<body>" <itemid>:<count>`,
delivered by mail. This sidesteps `additem`, which needs a selected in-world target.

## Realm list

**`realmlist.address` is what the client is told to connect to after it
authenticates.** A wrong value produces no login error — the client just hangs at
"Logging in to game server". It must be reachable *by the client*, which is not
necessarily an address the server itself can reach.

**The authserver re-reads `realmlist` on a timer** (`RealmsStateUpdateDelay`,
default 20s), so edits apply without a restart.

**An empty `realmlist` means nobody can log in.** The panel refuses to delete the
last realm.

**Adding a realm row is not enough to make it playable** — a worldserver must be
running with a matching `RealmID` in its `worldserver.conf`.

**`flag` is a bitmask**, and `0x02` (offline) hides the realm from the list. That is
the usual way to pull a realm out of rotation without deleting the row. `timezone` is
a region grouping for the client's realm list, not a clock offset.

## Accounts

**SRP6 verifier**, verified byte-for-byte against an account created by the server's
own console:

```
h1 = SHA1(UPPER(username) + ":" + UPPER(password))
h2 = SHA1(salt || h1)
x  = int(h2, little-endian)
v  = pow(7, x, N)            stored little-endian, 32 bytes
N  = 894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7
```

WoW logins are case-insensitive, and the 3.3.5 client caps both username and
password at 16 characters. Validating longer values server-side is pointless — the
client cannot send them.

## Deployment

**Debian and Ubuntu's `python3-gunicorn` ships no `/usr/bin/gunicorn`.** Invoke it as
`python3 -m gunicorn`.

**gunicorn 25 opens a control socket in the working directory**, which fails under
`ProtectSystem=strict`. Pass `--no-control-socket`.

**Jinja has no bitwise operator.** Decode flag bitmasks in Python and pass the result
to the template; `{{ x & bit }}` is a template syntax error.

**`/etc/acore` is mode `0711`** so each service user can traverse to its own `0640`
credential file without being able to list the directory or read the others'.
