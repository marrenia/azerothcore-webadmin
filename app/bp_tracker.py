"""Live player tracker."""
import time

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, url_for
)

import tracker
from core import (
    AUTH_DB, CHAR_DB, BOT_PREFIX, CHARNAME_RE, login_required, query,
    realm_stats, server_online, soap,
)
from soap import SoapError

bp = Blueprint("tracker", __name__)

REFRESH_CHOICES = (0, 5, 10, 30, 60)


def _char(guid):
    row = query(
        f"""SELECT c.guid, c.name, c.race, c.class, c.level, c.online, c.map, c.zone,
                   c.position_x, c.position_y, c.position_z, c.account,
                   a.username, (a.username LIKE %s) AS is_bot
            FROM {CHAR_DB}.characters c
            JOIN {AUTH_DB}.account a ON a.id = c.account
            WHERE c.guid = %s AND c.deleteDate IS NULL""",
        (f"{BOT_PREFIX}%", guid), one=True,
    )
    if not row:
        abort(404)
    return row


@bp.route("/tracker")
@login_required
def index():
    search = (request.args.get("q") or "").strip()
    show_bots = request.args.get("bots") == "1"

    sql = f"""
        SELECT c.guid, c.name, c.race, c.class, c.level, c.online, c.map, c.zone,
               c.account, a.username, (a.username LIKE %s) AS is_bot
        FROM {CHAR_DB}.characters c
        JOIN {AUTH_DB}.account a ON a.id = c.account
        WHERE c.deleteDate IS NULL AND c.online = 1
    """
    args = [f"{BOT_PREFIX}%"]
    if not show_bots:
        sql += " AND a.username NOT LIKE %s"
        args.append(f"{BOT_PREFIX}%")
    if search:
        sql += " AND (c.name LIKE %s OR a.username LIKE %s)"
        args += [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY c.name ASC LIMIT 400"
    candidates = query(sql, tuple(args))

    watched = tracker.watch_list()
    for w in watched:
        latest = tracker.history(w["guid"], limit=2)
        w["latest"] = latest[0] if latest else None
        w["headline"] = tracker.derive_activity(
            latest[0] if latest else None,
            latest[1] if len(latest) > 1 else None)[0] if latest else "Not sampled yet"
        w["samples"] = tracker.sample_count(w["guid"])

    return render_template("tracker.html", candidates=candidates, watched=watched,
                           search=search, show_bots=show_bots, stats=realm_stats(),
                           world_up=server_online(), nav="tracker")


@bp.route("/tracker/<int:guid>")
@login_required
def watch(guid):
    c = _char(guid)
    try:
        refresh = int(request.args.get("refresh", 10))
    except ValueError:
        refresh = 10
    if refresh not in REFRESH_CHOICES:
        refresh = 10

    info, sample_error = None, None
    if CHARNAME_RE.match(c["name"]):
        try:
            raw = soap.command(f"pinfo {c['name']}", timeout=12)
            info = tracker.parse_pinfo(raw)
            tracker.record(guid, c["name"], info)
        except SoapError as exc:
            sample_error = str(exc)
    else:
        sample_error = "Character name is not in an expected format."

    hist = tracker.history(guid, limit=80)
    cur = hist[0] if hist else None
    prev = hist[1] if len(hist) > 1 else None
    headline, details = tracker.derive_activity(cur, prev)

    # Position comes from the last database save, not from memory.
    pos_age = None
    saved = query(
        f"""SELECT position_x, position_y, position_z, map, zone
            FROM {CHAR_DB}.characters WHERE guid=%s""", (guid,), one=True)

    trail = []
    seen = None
    for row in hist:
        label = row["area"] or row["zone"] or "unknown"
        if label != seen:
            trail.append(row)
            seen = label

    return render_template("tracker_watch.html", c=c, info=info, hist=hist,
                           headline=headline, details=details, trail=trail[:20],
                           saved=saved, pos_age=pos_age, refresh=refresh,
                           refresh_choices=REFRESH_CHOICES,
                           sample_error=sample_error,
                           watching=any(w["guid"] == guid for w in tracker.watch_list()),
                           world_up=server_online(), nav="tracker")


@bp.route("/tracker/<int:guid>/watch", methods=["POST"])
@login_required
def toggle_watch(guid):
    c = _char(guid)
    if request.form.get("action") == "remove":
        tracker.watch_remove(guid)
        flash(f"Stopped watching {c['name']}.", "ok")
        return redirect(url_for("tracker.index"))
    tracker.watch_add(guid, c["name"])
    flash(f"Watching {c['name']} - open the tracker to sample it.", "ok")
    return redirect(url_for("tracker.watch", guid=guid))


@bp.route("/tracker/<int:guid>/sync", methods=["POST"])
@login_required
def sync_positions(guid):
    """Force a world save so the stored coordinates become current."""
    try:
        soap.command("saveall", timeout=60)
        flash("World saved - stored coordinates are now current.", "ok")
    except SoapError as exc:
        flash(f"Could not save the world: {exc}", "error")
    return redirect(url_for("tracker.watch", guid=guid))


@bp.route("/tracker/prune", methods=["POST"])
@login_required
def prune():
    n = tracker.prune(days=7)
    flash(f"Removed {n} sample(s) older than 7 days.", "ok")
    return redirect(url_for("tracker.index"))
