"""Character browser, per-character GM tools, mail, and deleted-character restore."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from core import (
    AUTH_DB, CHAR_DB, BOT_PREFIX, CHARNAME_RE, ROLE_ADMIN, ROLE_GAMEMASTER,
    ROLE_MODERATOR, query, realm_stats, require_role, safe_arg, server_online,
    soap, WORLD_DB,
)
from soap import SoapError

bp = Blueprint("chars", __name__)

MAX_LEVEL = 80


def _char_or_404(guid):
    row = query(
        f"""SELECT c.*, a.username, g.name AS guild
            FROM {CHAR_DB}.characters c
            JOIN {AUTH_DB}.account a ON a.id = c.account
            LEFT JOIN {CHAR_DB}.guild_member gm ON gm.guid = c.guid
            LEFT JOIN {CHAR_DB}.guild g ON g.guildid = gm.guildid
            WHERE c.guid = %s""",
        (guid,), one=True,
    )
    if not row:
        abort(404)
    return row


@bp.route("/characters")
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def index():
    search = (request.args.get("q") or "").strip()
    show_bots = request.args.get("bots") == "1"
    sql = f"""
        SELECT c.guid, c.name, c.race, c.class, c.level, c.money, c.online,
               c.totaltime, c.map, c.zone, c.account, a.username,
               g.name AS guild, (a.username LIKE %s) AS is_bot
        FROM {CHAR_DB}.characters c
        JOIN {AUTH_DB}.account a ON a.id = c.account
        LEFT JOIN {CHAR_DB}.guild_member gm ON gm.guid = c.guid
        LEFT JOIN {CHAR_DB}.guild g ON g.guildid = gm.guildid
        WHERE c.deleteDate IS NULL
    """
    args = [f"{BOT_PREFIX}%"]
    if not show_bots:
        sql += " AND a.username NOT LIKE %s"
        args.append(f"{BOT_PREFIX}%")
    if search:
        sql += " AND (c.name LIKE %s OR a.username LIKE %s)"
        args += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY c.level DESC, c.name ASC LIMIT 300"

    chars = query(sql, tuple(args))
    return render_template("characters.html", chars=chars, search=search,
                           show_bots=show_bots, stats=realm_stats(),
                           world_up=server_online(), nav="characters")


@bp.route("/character/<int:guid>")
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def detail(guid):
    c = _char_or_404(guid)
    teles = query(f"SELECT name FROM {WORLD_DB}.game_tele ORDER BY name LIMIT 2000")
    return render_template("character.html", c=c, world_up=server_online(),
                           max_level=MAX_LEVEL, teles=teles, nav="characters")


@bp.route("/character/<int:guid>/teleport", methods=["POST"])
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def teleport(guid):
    c = _char_or_404(guid)
    dest = (request.form.get("destination") or "").strip()
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    # Only accept a destination the server actually knows about.
    known = query(f"SELECT name FROM {WORLD_DB}.game_tele WHERE name=%s", (dest,), one=True)
    if not known:
        flash(f"'{dest}' is not a known teleport location.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not c["online"]:
        flash(f"{c['name']} must be online to be teleported.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    return _run(guid, f"tele name {c['name']} {known['name']}",
                f"Teleported {c['name']} to {known['name']}.")


@bp.route("/character/<int:guid>/item", methods=["POST"])
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def send_item(guid):
    c = _char_or_404(guid)
    try:
        item_id = int(request.form.get("item_id", 0))
        count = int(request.form.get("count", 1))
    except ValueError:
        flash("Item ID and count must be numbers.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if item_id <= 0 or not 1 <= count <= 100:
        flash("Item ID must be positive and count between 1 and 100.", "error")
        return redirect(url_for("chars.detail", guid=guid))

    subject = (request.form.get("subject") or "A gift").strip()[:64]
    body = (request.form.get("body") or "Sent by the realm admin.").strip()[:500]
    if not safe_arg(subject) or not safe_arg(body):
        flash("Subject and body cannot contain double quotes or line breaks.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))

    return _run(guid, f'send items {c["name"]} "{subject}" "{body}" {item_id}:{count}',
                f"Sent {count} x item {item_id} to {c['name']} by mail.")


@bp.route("/character/<int:guid>/combatstop", methods=["POST"])
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def combatstop(guid):
    c = _char_or_404(guid)
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not c["online"]:
        flash(f"{c['name']} is not online.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    return _run(guid, f"combatstop {c['name']}", f"Dropped {c['name']} out of combat.")


def _run(guid, cmd, ok_msg):
    try:
        out = soap.command(cmd)
        flash(f"{ok_msg}{(' - ' + out) if out else ''}", "ok")
    except SoapError as exc:
        flash(f"Command failed: {exc}", "error")
    return redirect(url_for("chars.detail", guid=guid))


@bp.route("/character/<int:guid>/level", methods=["POST"])
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def set_level(guid):
    c = _char_or_404(guid)
    try:
        level = int(request.form.get("level", 0))
    except ValueError:
        flash("Level must be a number.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not 1 <= level <= MAX_LEVEL:
        flash(f"Level must be between 1 and {MAX_LEVEL}.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    return _run(guid, f"character level {c['name']} {level}",
                f"Set {c['name']} to level {level}.")


@bp.route("/character/<int:guid>/rename", methods=["POST"])
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def rename(guid):
    c = _char_or_404(guid)
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    return _run(guid, f"character rename {c['name']}",
                f"{c['name']} will be asked to pick a new name at next login.")


@bp.route("/character/<int:guid>/customize", methods=["POST"])
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def customize(guid):
    c = _char_or_404(guid)
    kind = request.form.get("kind", "customize")
    if kind not in ("customize", "changefaction", "changerace"):
        abort(400)
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    label = {"customize": "appearance change", "changefaction": "faction change",
             "changerace": "race change"}[kind]
    return _run(guid, f"character {kind} {c['name']}",
                f"{c['name']} will be offered a {label} at next login.")


@bp.route("/character/<int:guid>/kick", methods=["POST"])
@require_role(ROLE_MODERATOR, ROLE_GAMEMASTER, ROLE_ADMIN)
def kick(guid):
    c = _char_or_404(guid)
    if not c["online"]:
        flash(f"{c['name']} is not online.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    reason = (request.form.get("reason") or "").strip()[:120]
    cmd = f"kick {c['name']}" + (f" {reason}" if reason else "")
    return _run(guid, cmd, f"Kicked {c['name']}.")


@bp.route("/character/<int:guid>/mail", methods=["POST"])
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def send_mail(guid):
    c = _char_or_404(guid)
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    money = (request.form.get("money") or "").strip()

    if not subject or not body:
        flash("Mail needs both a subject and a body.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if len(subject) > 64 or len(body) > 500:
        flash("Subject max 64 characters, body max 500.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not safe_arg(subject) or not safe_arg(body):
        flash("Subject and body cannot contain double quotes or line breaks.", "error")
        return redirect(url_for("chars.detail", guid=guid))
    if not CHARNAME_RE.match(c["name"]):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.detail", guid=guid))

    # The command takes quoted subject and body.
    if money:
        try:
            copper = int(money) * 10000
        except ValueError:
            flash("Gold must be a whole number.", "error")
            return redirect(url_for("chars.detail", guid=guid))
        if not 0 < copper <= in_copper_limit():
            flash("Gold must be between 1 and 999999.", "error")
            return redirect(url_for("chars.detail", guid=guid))
        cmd = f'send money {c["name"]} "{subject}" "{body}" {copper}'
        msg = f"Sent {money}g to {c['name']}."
    else:
        cmd = f'send mail {c["name"]} "{subject}" "{body}"'
        msg = f"Mail sent to {c['name']}."
    return _run(guid, cmd, msg)


def in_copper_limit():
    return 999999 * 10000


# ---------------------------------------------------------------- deleted

@bp.route("/deleted")
@require_role(ROLE_GAMEMASTER, ROLE_ADMIN)
def deleted():
    rows = query(
        f"""SELECT c.guid, c.deleteInfos_Name AS name, c.deleteInfos_Account AS account,
                   c.deleteDate, c.level, c.race, c.class,
                   a.username
            FROM {CHAR_DB}.characters c
            LEFT JOIN {AUTH_DB}.account a ON a.id = c.deleteInfos_Account
            WHERE c.deleteDate IS NOT NULL
            ORDER BY c.deleteDate DESC LIMIT 200"""
    )
    return render_template("deleted.html", rows=rows, stats=realm_stats(),
                           world_up=server_online(), nav="characters")


@bp.route("/deleted/<int:guid>/restore", methods=["POST"])
@require_role(ROLE_ADMIN)
def restore(guid):
    row = query(
        f"""SELECT deleteInfos_Name AS name FROM {CHAR_DB}.characters
            WHERE guid=%s AND deleteDate IS NOT NULL""", (guid,), one=True)
    if not row:
        abort(404)
    name = row["name"] or ""
    if not CHARNAME_RE.match(name):
        flash("That deleted character has no usable name to restore by.", "error")
        return redirect(url_for("chars.deleted"))
    try:
        out = soap.command(f"character deleted restore {name}")
    except SoapError as exc:
        flash(f"Restore failed: {exc}", "error")
        return redirect(url_for("chars.deleted"))

    still = query(f"SELECT deleteDate FROM {CHAR_DB}.characters WHERE guid=%s",
                  (guid,), one=True)
    if still and still["deleteDate"] is None:
        flash(f"Restored '{name}'.", "ok")
    else:
        flash(f"Server did not restore '{name}'. It said: {out or 'no output'}", "error")
    return redirect(url_for("chars.deleted"))


@bp.route("/deleted/<int:guid>/purge", methods=["POST"])
@require_role(ROLE_ADMIN)
def purge(guid):
    row = query(
        f"""SELECT deleteInfos_Name AS name FROM {CHAR_DB}.characters
            WHERE guid=%s AND deleteDate IS NOT NULL""", (guid,), one=True)
    if not row:
        abort(404)
    name = row["name"] or ""
    if (request.form.get("confirm") or "").strip().lower() != name.lower():
        flash("Purge cancelled: the typed name did not match.", "error")
        return redirect(url_for("chars.deleted"))
    if not CHARNAME_RE.match(name):
        flash("Refusing to act on an unexpected character name.", "error")
        return redirect(url_for("chars.deleted"))
    try:
        soap.command(f"character deleted delete {name}")
    except SoapError as exc:
        flash(f"Purge failed: {exc}", "error")
        return redirect(url_for("chars.deleted"))
    if query(f"SELECT guid FROM {CHAR_DB}.characters WHERE guid=%s", (guid,), one=True):
        flash(f"Server did not purge '{name}'.", "error")
    else:
        flash(f"'{name}' permanently purged.", "ok")
    return redirect(url_for("chars.deleted"))
