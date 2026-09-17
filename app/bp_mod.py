"""Moderation: account/character/IP bans, mutes, and GM tickets."""
import time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from core import (
    AUTH_DB, CHAR_DB, CHARNAME_RE, IP_RE, USERNAME_RE, login_required, query,
    realm_stats, safe_arg, server_online, soap,
)
from soap import SoapError

bp = Blueprint("mod", __name__)

DURATIONS = {
    "permanent": ("-1", "Permanent"),
    "1h": ("1h", "1 hour"),
    "1d": ("1d", "1 day"),
    "7d": ("7d", "7 days"),
    "30d": ("30d", "30 days"),
}


@bp.route("/moderation")
@login_required
def index():
    now = int(time.time())
    account_bans = query(
        f"""SELECT b.id, a.username, b.bandate, b.unbandate, b.bannedby, b.banreason
            FROM {AUTH_DB}.account_banned b
            JOIN {AUTH_DB}.account a ON a.id = b.id
            WHERE b.active = 1 AND (b.unbandate = b.bandate OR b.unbandate > %s)
            ORDER BY b.bandate DESC LIMIT 200""", (now,))
    char_bans = query(
        f"""SELECT b.guid, c.name, b.bandate, b.unbandate, b.bannedby, b.banreason
            FROM {CHAR_DB}.character_banned b
            LEFT JOIN {CHAR_DB}.characters c ON c.guid = b.guid
            WHERE b.active = 1 AND (b.unbandate = b.bandate OR b.unbandate > %s)
            ORDER BY b.bandate DESC LIMIT 200""", (now,))
    ip_bans = query(
        f"""SELECT ip, bandate, unbandate, bannedby, banreason
            FROM {AUTH_DB}.ip_banned
            WHERE unbandate = bandate OR unbandate > %s
            ORDER BY bandate DESC LIMIT 200""", (now,))
    muted = query(
        f"""SELECT id, username, mutetime, mutereason, muteby
            FROM {AUTH_DB}.account WHERE mutetime > %s
            ORDER BY mutetime DESC LIMIT 100""", (now,))

    for row in list(account_bans) + list(char_bans) + list(ip_bans):
        row["permanent"] = row["unbandate"] == row["bandate"]

    return render_template("moderation.html", account_bans=account_bans,
                           char_bans=char_bans, ip_bans=ip_bans, muted=muted,
                           durations=DURATIONS, stats=realm_stats(),
                           world_up=server_online(), now=now, nav="moderation")


def _duration_arg(key):
    return DURATIONS.get(key, DURATIONS["permanent"])[0]


@bp.route("/moderation/ban", methods=["POST"])
@login_required
def ban():
    kind = request.form.get("kind", "")
    target = (request.form.get("target") or "").strip()
    reason = (request.form.get("reason") or "Banned via web admin").strip()[:200]
    duration = _duration_arg(request.form.get("duration", "permanent"))

    if kind == "ip":
        if not IP_RE.match(target):
            flash("That does not look like an IPv4 address.", "error")
            return redirect(url_for("mod.index"))
    elif kind == "account":
        if not USERNAME_RE.match(target):
            flash("Account names are letters and numbers only.", "error")
            return redirect(url_for("mod.index"))
    elif kind == "character":
        if not CHARNAME_RE.match(target):
            flash("That does not look like a character name.", "error")
            return redirect(url_for("mod.index"))
    else:
        abort(400, "Unknown ban type")

    if not safe_arg(reason):
        flash("Reason cannot contain double quotes or line breaks.", "error")
        return redirect(url_for("mod.index"))

    try:
        out = soap.command(f'ban {kind} {target} {duration} "{reason}"')
        flash(f"Ban applied to {target}. {out}".strip(), "ok")
    except SoapError as exc:
        flash(f"Ban failed: {exc}", "error")
    return redirect(url_for("mod.index"))


@bp.route("/moderation/unban", methods=["POST"])
@login_required
def unban():
    kind = request.form.get("kind", "")
    target = (request.form.get("target") or "").strip()
    valid = (
        (kind == "ip" and IP_RE.match(target))
        or (kind == "account" and USERNAME_RE.match(target))
        or (kind == "character" and CHARNAME_RE.match(target))
    )
    if not valid:
        abort(400, "Invalid unban target")
    try:
        out = soap.command(f"unban {kind} {target}")
        flash(f"Unbanned {target}. {out}".strip(), "ok")
    except SoapError as exc:
        flash(f"Unban failed: {exc}", "error")
    return redirect(url_for("mod.index"))


@bp.route("/moderation/mute", methods=["POST"])
@login_required
def mute():
    target = (request.form.get("target") or "").strip()
    reason = (request.form.get("reason") or "Muted via web admin").strip()[:200]
    try:
        minutes = int(request.form.get("minutes", 60))
    except ValueError:
        minutes = 60
    minutes = max(1, min(minutes, 60 * 24 * 30))
    if not CHARNAME_RE.match(target):
        flash("Mute takes a character name.", "error")
        return redirect(url_for("mod.index"))
    if not safe_arg(reason):
        flash("Reason cannot contain double quotes or line breaks.", "error")
        return redirect(url_for("mod.index"))
    try:
        out = soap.command(f'mute {target} {minutes} "{reason}"')
        flash(f"Muted {target} for {minutes} minutes. {out}".strip(), "ok")
    except SoapError as exc:
        flash(f"Mute failed: {exc}", "error")
    return redirect(url_for("mod.index"))


@bp.route("/moderation/unmute", methods=["POST"])
@login_required
def unmute():
    target = (request.form.get("target") or "").strip()
    if not CHARNAME_RE.match(target):
        flash("Unmute takes a character name.", "error")
        return redirect(url_for("mod.index"))
    try:
        out = soap.command(f"unmute {target}")
        flash(f"Unmuted {target}. {out}".strip(), "ok")
    except SoapError as exc:
        flash(f"Unmute failed: {exc}", "error")
    return redirect(url_for("mod.index"))


# ---------------------------------------------------------------- tickets

@bp.route("/tickets")
@login_required
def tickets():
    show_closed = request.args.get("closed") == "1"
    sql = f"""
        SELECT t.id, t.type, t.playerGuid, t.name, t.description, t.createTime,
               t.lastModifiedTime, t.mapId, t.completed, t.escalated, t.viewed,
               t.comment, t.response, t.assignedTo, c.level, c.race, c.class
        FROM {CHAR_DB}.gm_ticket t
        LEFT JOIN {CHAR_DB}.characters c ON c.guid = t.playerGuid
    """
    sql += "" if show_closed else " WHERE t.completed = 0"
    sql += " ORDER BY t.createTime DESC LIMIT 200"
    rows = query(sql)
    open_count = query(
        f"SELECT COUNT(*) AS n FROM {CHAR_DB}.gm_ticket WHERE completed=0", one=True)["n"]
    return render_template("tickets.html", rows=rows, show_closed=show_closed,
                           open_count=open_count, stats=realm_stats(),
                           world_up=server_online(), nav="tickets")


@bp.route("/tickets/<int:ticket_id>/close", methods=["POST"])
@login_required
def close_ticket(ticket_id):
    try:
        out = soap.command(f"ticket close {ticket_id}")
        flash(f"Ticket #{ticket_id} closed. {out}".strip(), "ok")
    except SoapError as exc:
        flash(f"Could not close ticket: {exc}", "error")
    return redirect(url_for("mod.tickets"))


@bp.route("/tickets/<int:ticket_id>/comment", methods=["POST"])
@login_required
def comment_ticket(ticket_id):
    text = (request.form.get("comment") or "").strip()[:400]
    if not text:
        flash("Comment cannot be empty.", "error")
        return redirect(url_for("mod.tickets"))
    if not safe_arg(text):
        flash("Comment cannot contain double quotes or line breaks.", "error")
        return redirect(url_for("mod.tickets"))
    try:
        soap.command(f'ticket comment {ticket_id} "{text}"')
        flash(f"Comment added to ticket #{ticket_id}.", "ok")
    except SoapError as exc:
        flash(f"Could not comment: {exc}", "error")
    return redirect(url_for("mod.tickets"))
