"""World administration: guilds, arena teams, teleports and data reloads.

Only commands AzerothCore marks Console::Yes appear here. Spawning creatures
and gameobjects is deliberately absent: those commands place the object at the
GM's own in-world position and are flagged Console::No, so they cannot work
from a web panel at all.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from core import (
    AUTH_DB, CHAR_DB, CHARNAME_RE, login_required, query, realm_stats,
    safe_arg, server_online, soap, WORLD_DB,
)
from soap import SoapError

bp = Blueprint("world", __name__)

# The reload targets worth a button. Everything else is reachable via Console.
COMMON_RELOADS = [
    ("config", "Server config"),
    ("creature_template", "Creature templates"),
    ("item_template", "Item templates"),
    ("quest_template", "Quest templates"),
    ("gossip_menu", "Gossip menus"),
    ("game_tele", "Teleport locations"),
    ("auctions", "Auctions"),
    ("autobroadcast", "Auto broadcasts"),
    ("acore_string", "Server strings"),
    ("gm_tickets", "GM tickets"),
    ("disables", "Disables"),
    ("graveyard_zone", "Graveyards"),
]

GUILD_RANKS = {0: "Guild Master", 1: "Officer", 2: "Veteran", 3: "Member", 4: "Initiate"}


@bp.route("/world")
@login_required
def index():
    guilds = query(
        f"""SELECT g.guildid, g.name, g.createdate,
                   (SELECT COUNT(*) FROM {CHAR_DB}.guild_member m
                     WHERE m.guildid = g.guildid) AS members,
                   c.name AS leader
            FROM {CHAR_DB}.guild g
            LEFT JOIN {CHAR_DB}.characters c ON c.guid = g.leaderguid
            ORDER BY members DESC LIMIT 100"""
    )
    teles = query(
        f"SELECT name FROM {WORLD_DB}.game_tele ORDER BY name LIMIT 2000")
    return render_template("world.html", guilds=guilds, teles=teles,
                           reloads=COMMON_RELOADS, stats=realm_stats(),
                           world_up=server_online(), nav="world")


def _back(result, ok_msg):
    flash(f"{ok_msg}{(' - ' + result) if result else ''}", "ok")
    return redirect(url_for("world.index"))


# ---------------------------------------------------------------- guilds

@bp.route("/world/guild/create", methods=["POST"])
@login_required
def guild_create():
    leader = (request.form.get("leader") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not CHARNAME_RE.match(leader):
        flash("Leader must be a character name.", "error")
        return redirect(url_for("world.index"))
    if not name or len(name) > 24 or not safe_arg(name):
        flash("Guild name must be 1-24 characters with no quotes.", "error")
        return redirect(url_for("world.index"))
    try:
        out = soap.command(f'guild create {leader} "{name}"')
    except SoapError as exc:
        flash(f"Could not create guild: {exc}", "error")
        return redirect(url_for("world.index"))
    return _back(out, f"Guild '{name}' created under {leader}.")


@bp.route("/world/guild/rename", methods=["POST"])
@login_required
def guild_rename():
    old = (request.form.get("old") or "").strip()
    new = (request.form.get("new") or "").strip()
    if not old or not new or not safe_arg(old) or not safe_arg(new) or len(new) > 24:
        flash("Both guild names are required, max 24 characters, no quotes.", "error")
        return redirect(url_for("world.index"))
    try:
        out = soap.command(f'guild rename "{old}" "{new}"')
    except SoapError as exc:
        flash(f"Could not rename guild: {exc}", "error")
        return redirect(url_for("world.index"))
    return _back(out, f"Guild '{old}' renamed to '{new}'.")


@bp.route("/world/guild/delete", methods=["POST"])
@login_required
def guild_delete():
    name = (request.form.get("name") or "").strip()
    if (request.form.get("confirm") or "").strip() != name:
        flash("Deletion cancelled: the typed guild name did not match.", "error")
        return redirect(url_for("world.index"))
    if not name or not safe_arg(name):
        flash("Invalid guild name.", "error")
        return redirect(url_for("world.index"))
    try:
        out = soap.command(f'guild delete "{name}"')
    except SoapError as exc:
        flash(f"Could not delete guild: {exc}", "error")
        return redirect(url_for("world.index"))
    return _back(out, f"Guild '{name}' disbanded.")


@bp.route("/world/guild/member", methods=["POST"])
@login_required
def guild_member():
    action = request.form.get("action", "invite")
    player = (request.form.get("player") or "").strip()
    guild = (request.form.get("guild") or "").strip()
    if not CHARNAME_RE.match(player):
        flash("Player must be a character name.", "error")
        return redirect(url_for("world.index"))

    if action == "invite":
        if not guild or not safe_arg(guild):
            flash("Guild name required.", "error")
            return redirect(url_for("world.index"))
        cmd, msg = f'guild invite {player} "{guild}"', f"{player} invited to {guild}."
    elif action == "uninvite":
        cmd, msg = f"guild uninvite {player}", f"{player} removed from their guild."
    elif action == "rank":
        try:
            rank = int(request.form.get("rank", 4))
        except ValueError:
            rank = 4
        if rank not in GUILD_RANKS:
            flash("Invalid rank.", "error")
            return redirect(url_for("world.index"))
        cmd, msg = f"guild rank {player} {rank}", f"{player} set to {GUILD_RANKS[rank]}."
    else:
        flash("Unknown guild action.", "error")
        return redirect(url_for("world.index"))

    try:
        out = soap.command(cmd)
    except SoapError as exc:
        flash(f"Guild command failed: {exc}", "error")
        return redirect(url_for("world.index"))
    return _back(out, msg)


# ---------------------------------------------------------------- arena

@bp.route("/world/arena", methods=["POST"])
@login_required
def arena():
    action = request.form.get("action", "info")
    name = (request.form.get("name") or "").strip()
    captain = (request.form.get("captain") or "").strip()
    try:
        arena_type = int(request.form.get("type", 2))
    except ValueError:
        arena_type = 2
    if arena_type not in (2, 3, 5):
        arena_type = 2

    if action == "create":
        if not CHARNAME_RE.match(captain) or not name or not safe_arg(name):
            flash("Arena team needs a captain character and a name.", "error")
            return redirect(url_for("world.index"))
        cmd, msg = (f'arena create {captain} "{name}" {arena_type}',
                    f"Arena team '{name}' ({arena_type}v{arena_type}) created.")
    elif action == "disband":
        if (request.form.get("confirm") or "").strip() != name:
            flash("Disband cancelled: the typed team name did not match.", "error")
            return redirect(url_for("world.index"))
        cmd, msg = f'arena disband "{name}"', f"Arena team '{name}' disbanded."
    elif action == "rename":
        new = (request.form.get("new") or "").strip()
        if not new or not safe_arg(new) or not safe_arg(name):
            flash("Both team names are required.", "error")
            return redirect(url_for("world.index"))
        cmd, msg = f'arena rename "{name}" "{new}"', f"Arena team renamed to '{new}'."
    elif action == "lookup":
        if not safe_arg(name):
            flash("Invalid search term.", "error")
            return redirect(url_for("world.index"))
        cmd, msg = f'arena lookup {name}', "Arena lookup:"
    else:
        flash("Unknown arena action.", "error")
        return redirect(url_for("world.index"))

    try:
        out = soap.command(cmd)
    except SoapError as exc:
        flash(f"Arena command failed: {exc}", "error")
        return redirect(url_for("world.index"))
    return _back(out, msg)


# ---------------------------------------------------------------- reload

@bp.route("/world/reload", methods=["POST"])
@login_required
def reload_table():
    target = (request.form.get("target") or "").strip()
    allowed = {t for t, _ in COMMON_RELOADS}
    if target not in allowed:
        flash("Pick one of the listed reload targets, or use the Console "
              "for anything else.", "error")
        return redirect(url_for("world.index"))
    try:
        out = soap.command(f"reload {target}", timeout=120)
    except SoapError as exc:
        flash(f"Reload failed: {exc}", "error")
        return redirect(url_for("world.index"))
    return _back(out, f"Reloaded {target}.")
