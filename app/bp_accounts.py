"""Account management and the Who's Online view."""
import time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import gamedata
from core import (
    AUTH_DB, CHAR_DB, BOT_PREFIX, EXPANSIONS, GM_LEVELS, USERNAME_RE, CHARNAME_RE,
    ban_state, execute, login_required, query, realm_stats, server_online,
    soap, srp6_make, validate_credentials,
)
from soap import SoapError

bp = Blueprint("accounts", __name__)


@bp.route("/")
@login_required
def index():
    search = (request.args.get("q") or "").strip()
    show_bots = request.args.get("bots") == "1"

    sql = f"""
        SELECT a.id, a.username, a.email, a.joindate, a.last_login, a.last_ip,
               a.expansion, a.online, a.locked, a.failed_logins, a.mutetime,
               COALESCE(aa.gmlevel, 0) AS gmlevel,
               (SELECT COUNT(*) FROM {CHAR_DB}.characters c
                 WHERE c.account = a.id AND c.deleteDate IS NULL) AS chars
        FROM {AUTH_DB}.account a
        LEFT JOIN {AUTH_DB}.account_access aa ON aa.id = a.id AND aa.RealmID = -1
    """
    where, args = [], []
    if not show_bots:
        where.append("a.username NOT LIKE %s")
        args.append(f"{BOT_PREFIX}%")
    if search:
        where.append("(a.username LIKE %s OR a.email LIKE %s)")
        args += [f"%{search}%", f"%{search}%"]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY a.id ASC"

    accounts = query(sql, tuple(args))
    for acc in accounts:
        acc["ban"] = ban_state(acc["id"])

    return render_template("accounts.html", accounts=accounts, search=search,
                           stats=realm_stats(), show_bots=show_bots,
                           bot_prefix=BOT_PREFIX, nav="accounts")


@bp.route("/online")
@login_required
def online():
    show_bots = request.args.get("bots") == "1"
    sql = f"""
        SELECT c.guid, c.name, c.race, c.class, c.gender, c.level, c.money,
               c.map, c.zone, c.totaltime, c.account,
               a.username, a.last_ip, COALESCE(aa.gmlevel, 0) AS gmlevel,
               (a.username LIKE %s) AS is_bot
        FROM {CHAR_DB}.characters c
        JOIN {AUTH_DB}.account a ON a.id = c.account
        LEFT JOIN {AUTH_DB}.account_access aa ON aa.id = a.id AND aa.RealmID = -1
        WHERE c.online = 1
    """
    args = [f"{BOT_PREFIX}%"]
    if not show_bots:
        sql += " AND a.username NOT LIKE %s"
        args.append(f"{BOT_PREFIX}%")
    sql += " ORDER BY c.level DESC, c.name ASC"
    players = query(sql, tuple(args))

    counts = query(
        f"""SELECT SUM(a.username LIKE %s) AS bots,
                   SUM(a.username NOT LIKE %s) AS humans
            FROM {CHAR_DB}.characters c
            JOIN {AUTH_DB}.account a ON a.id = c.account
            WHERE c.online = 1""",
        (f"{BOT_PREFIX}%", f"{BOT_PREFIX}%"), one=True,
    )

    factions = {"Alliance": 0, "Horde": 0, "Neutral": 0}
    zones = {}
    for p in players:
        factions[gamedata.race_faction(p["race"])] += 1
        z = gamedata.zone_name(p["zone"])
        zones[z] = zones.get(z, 0) + 1
    top_zones = sorted(zones.items(), key=lambda kv: -kv[1])[:6]

    return render_template("online.html", players=players, show_bots=show_bots,
                           bots_online=int(counts["bots"] or 0),
                           humans_online=int(counts["humans"] or 0),
                           factions=factions, top_zones=top_zones,
                           stats=realm_stats(), world_up=server_online(),
                           nav="online")


@bp.route("/create", methods=["POST"])
@login_required
def create():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    email = (request.form.get("email") or "").strip()
    try:
        expansion = int(request.form.get("expansion", 2))
    except ValueError:
        expansion = 2
    if expansion not in EXPANSIONS:
        expansion = 2

    errors = validate_credentials(username, password)
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("accounts.index"))

    uname = username.upper()
    if query(f"SELECT id FROM {AUTH_DB}.account WHERE username=%s", (uname,), one=True):
        flash(f"Account '{uname}' already exists.", "error")
        return redirect(url_for("accounts.index"))

    salt, verifier = srp6_make(uname, password)
    execute(
        f"""INSERT INTO {AUTH_DB}.account (username, salt, verifier, email, reg_mail, expansion, joindate)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
        (uname, salt, verifier, email, email, expansion),
    )
    flash(f"Account '{uname}' created.", "ok")
    return redirect(url_for("accounts.index"))


@bp.route("/account/<int:account_id>")
@login_required
def detail(account_id):
    acc = query(
        f"""SELECT a.*, COALESCE(aa.gmlevel, 0) AS gmlevel
            FROM {AUTH_DB}.account a
            LEFT JOIN {AUTH_DB}.account_access aa ON aa.id = a.id AND aa.RealmID = -1
            WHERE a.id = %s""",
        (account_id,), one=True,
    )
    if not acc:
        abort(404)
    acc["ban"] = ban_state(account_id)
    acc["muted"] = acc["mutetime"] and acc["mutetime"] > int(time.time())
    chars = query(
        f"""SELECT c.guid, c.name, c.race, c.class, c.gender, c.level, c.money,
                   c.online, c.totaltime, c.map, c.zone,
                   g.name AS guild
            FROM {CHAR_DB}.characters c
            LEFT JOIN {CHAR_DB}.guild_member gm ON gm.guid = c.guid
            LEFT JOIN {CHAR_DB}.guild g ON g.guildid = gm.guildid
            WHERE c.account=%s AND c.deleteDate IS NULL
            ORDER BY c.level DESC, c.name ASC""",
        (account_id,),
    )
    return render_template("detail.html", acc=acc, chars=chars,
                           world_up=server_online(), nav="accounts")


@bp.route("/account/<int:account_id>/password", methods=["POST"])
@login_required
def set_password(account_id):
    acc = query(f"SELECT username FROM {AUTH_DB}.account WHERE id=%s", (account_id,), one=True)
    if not acc:
        abort(404)
    password = request.form.get("password") or ""
    errors = validate_credentials(acc["username"], password)
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("accounts.detail", account_id=account_id))

    salt, verifier = srp6_make(acc["username"], password)
    execute(
        f"UPDATE {AUTH_DB}.account SET salt=%s, verifier=%s, session_key=NULL WHERE id=%s",
        (salt, verifier, account_id),
    )
    flash(f"Password updated for '{acc['username']}'.", "ok")
    return redirect(url_for("accounts.detail", account_id=account_id))


@bp.route("/account/<int:account_id>/gmlevel", methods=["POST"])
@login_required
def set_gmlevel(account_id):
    try:
        level = int(request.form.get("gmlevel", 0))
    except ValueError:
        level = 0
    if level not in GM_LEVELS:
        abort(400, "Invalid GM level")
    if level == 0:
        execute(f"DELETE FROM {AUTH_DB}.account_access WHERE id=%s AND RealmID=-1", (account_id,))
    else:
        execute(
            f"""INSERT INTO {AUTH_DB}.account_access (id, gmlevel, RealmID, comment)
                VALUES (%s, %s, -1, 'set via web admin')
                ON DUPLICATE KEY UPDATE gmlevel=VALUES(gmlevel)""",
            (account_id, level),
        )
    flash(f"Access level set to {GM_LEVELS[level]}.", "ok")
    return redirect(url_for("accounts.detail", account_id=account_id))


@bp.route("/account/<int:account_id>/ban", methods=["POST"])
@login_required
def ban(account_id):
    reason = (request.form.get("reason") or "Banned via web admin").strip()[:255]
    duration = request.form.get("duration", "permanent")
    now = int(time.time())
    if duration == "permanent":
        unban_at = now
    else:
        try:
            unban_at = now + max(1, int(duration)) * 86400
        except ValueError:
            unban_at = now
    execute(f"UPDATE {AUTH_DB}.account_banned SET active=0 WHERE id=%s AND active=1", (account_id,))
    execute(
        f"""INSERT INTO {AUTH_DB}.account_banned (id, bandate, unbandate, bannedby, banreason, active)
            VALUES (%s, %s, %s, 'web admin', %s, 1)
            ON DUPLICATE KEY UPDATE unbandate=VALUES(unbandate),
                                    banreason=VALUES(banreason), active=1""",
        (account_id, now, unban_at, reason),
    )
    flash("Account banned.", "ok")
    return redirect(url_for("accounts.detail", account_id=account_id))


@bp.route("/account/<int:account_id>/unban", methods=["POST"])
@login_required
def unban(account_id):
    n = execute(f"UPDATE {AUTH_DB}.account_banned SET active=0 WHERE id=%s AND active=1",
                (account_id,))
    flash("Account unbanned." if n else "That account had no active ban.", "ok")
    return redirect(url_for("accounts.detail", account_id=account_id))


@bp.route("/account/<int:account_id>/expansion", methods=["POST"])
@login_required
def set_expansion(account_id):
    try:
        exp = int(request.form.get("expansion", 2))
    except ValueError:
        abort(400)
    if exp not in EXPANSIONS:
        abort(400, "Invalid expansion")
    execute(f"UPDATE {AUTH_DB}.account SET expansion=%s WHERE id=%s", (exp, account_id))
    flash(f"Expansion set to {EXPANSIONS[exp]}.", "ok")
    return redirect(url_for("accounts.detail", account_id=account_id))


# ------------------------------------------------- destructive operations

@bp.route("/character/<int:guid>/delete", methods=["POST"])
@login_required
def delete_character(guid):
    row = query(f"SELECT name, account FROM {CHAR_DB}.characters WHERE guid=%s",
                (guid,), one=True)
    if not row:
        abort(404)
    account_id, name = row["account"], row["name"]

    if (request.form.get("confirm") or "").strip().lower() != name.lower():
        flash("Deletion cancelled: the typed character name did not match.", "error")
        return redirect(url_for("accounts.detail", account_id=account_id))
    if not CHARNAME_RE.match(name):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("accounts.detail", account_id=account_id))

    try:
        out = soap.command(f"character erase {name}")
    except SoapError as exc:
        flash(f"Could not delete '{name}': {exc}", "error")
        return redirect(url_for("accounts.detail", account_id=account_id))

    if query(f"SELECT guid FROM {CHAR_DB}.characters WHERE guid=%s", (guid,), one=True):
        flash(f"The server did not delete '{name}'. It said: {out or 'no output'}", "error")
    else:
        flash(f"Character '{name}' deleted; the server returned mail and cleared "
              "guild, group and pet data.", "ok")
    return redirect(url_for("accounts.detail", account_id=account_id))


@bp.route("/account/<int:account_id>/delete", methods=["POST"])
@login_required
def delete(account_id):
    acc = query(f"SELECT username FROM {AUTH_DB}.account WHERE id=%s", (account_id,), one=True)
    if not acc:
        abort(404)
    username = acc["username"]

    if (request.form.get("confirm") or "").strip().upper() != username:
        flash("Deletion cancelled: the typed name did not match.", "error")
        return redirect(url_for("accounts.detail", account_id=account_id))
    if not USERNAME_RE.match(username):
        flash("Refusing to act on an unexpected account name.", "error")
        return redirect(url_for("accounts.detail", account_id=account_id))

    try:
        out = soap.command(f"account delete {username}")
    except SoapError as exc:
        flash(f"Could not delete '{username}': {exc}", "error")
        return redirect(url_for("accounts.detail", account_id=account_id))

    if query(f"SELECT id FROM {AUTH_DB}.account WHERE id=%s", (account_id,), one=True):
        flash(f"The server did not delete '{username}'. It said: {out or 'no output'}", "error")
        return redirect(url_for("accounts.detail", account_id=account_id))

    leftover = query(f"SELECT COUNT(*) AS n FROM {CHAR_DB}.characters WHERE account=%s",
                     (account_id,), one=True)["n"]
    if leftover:
        flash(f"Account '{username}' deleted, but {leftover} character row(s) remain.", "error")
    else:
        flash(f"Account '{username}' and all its characters were deleted.", "ok")
    return redirect(url_for("accounts.index"))
