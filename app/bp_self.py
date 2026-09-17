"""Player self-service: an account's own details, characters and tickets.

Every query here is scoped by the caller's own session account_id - never by
a value taken from the request - so one player cannot browse or act on
another's data by editing a URL or a hidden form field. Nothing here uses
SOAP: it is read access plus a single self password-change, all served
directly off the least-privilege database account.
"""
import hmac
import time

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from core import (
    ALL_ROLES, AUTH_DB, CHAR_DB, ban_state, current_account_id, execute,
    query, require_role, srp6_make, validate_credentials,
)

bp = Blueprint("self", __name__, url_prefix="/me")


def _own_account_id():
    """The caller's own account id, or None for the bootstrap WEB_ADMIN identity."""
    return current_account_id()


def _require_own_account():
    account_id = _own_account_id()
    if account_id is None:
        abort(403, "The bootstrap admin identity has no database account to manage here.")
    return account_id


@bp.route("")
@require_role(*ALL_ROLES)
def index():
    account_id = _require_own_account()
    acc = query(
        f"""SELECT id, username, email, joindate, last_login, last_ip,
                   expansion, online, locked, failed_logins, mutetime
            FROM {AUTH_DB}.account WHERE id=%s""",
        (account_id,), one=True,
    )
    if not acc:
        abort(404)
    acc["ban"] = ban_state(account_id)
    acc["muted"] = acc["mutetime"] and acc["mutetime"] > int(time.time())
    return render_template("me.html", acc=acc, nav="me")


@bp.route("/password", methods=["POST"])
@require_role(*ALL_ROLES)
def change_password():
    account_id = _require_own_account()
    acc = query(f"SELECT username, salt, verifier FROM {AUTH_DB}.account WHERE id=%s",
               (account_id,), one=True)
    if not acc:
        abort(404)

    current = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""

    _, computed = srp6_make(acc["username"], current, salt=acc["salt"])
    if not hmac.compare_digest(computed, bytes(acc["verifier"])):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("self.index"))

    errors = validate_credentials(acc["username"], new_password)
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("self.index"))

    salt, verifier = srp6_make(acc["username"], new_password)
    execute(
        f"UPDATE {AUTH_DB}.account SET salt=%s, verifier=%s, session_key=NULL WHERE id=%s",
        (salt, verifier, account_id),
    )
    flash("Password updated. Use it next time you log in.", "ok")
    return redirect(url_for("self.index"))


@bp.route("/characters")
@require_role(*ALL_ROLES)
def characters():
    account_id = _require_own_account()
    chars = query(
        f"""SELECT guid, name, race, class, gender, level, money, online,
                   totaltime, map, zone
            FROM {CHAR_DB}.characters WHERE account=%s AND deleteDate IS NULL
            ORDER BY level DESC, name ASC""",
        (account_id,),
    )
    deleted = query(
        f"""SELECT guid, deleteInfos_Name AS name, deleteDate, level, race, class
            FROM {CHAR_DB}.characters WHERE account=%s AND deleteDate IS NOT NULL
            ORDER BY deleteDate DESC""",
        (account_id,),
    )
    return render_template("me_characters.html", chars=chars, deleted=deleted, nav="me")


@bp.route("/characters/<int:guid>")
@require_role(*ALL_ROLES)
def character_detail(guid):
    account_id = _require_own_account()
    c = query(
        f"""SELECT guid, name, race, class, gender, level, money, online,
                   totaltime, map, zone, account, deleteDate
            FROM {CHAR_DB}.characters WHERE guid=%s""",
        (guid,), one=True,
    )
    if not c:
        abort(404)
    if c["account"] != account_id:
        # Strict ownership scoping: this is not this caller's character.
        abort(403)
    return render_template("me_character.html", c=c, nav="me")


@bp.route("/tickets")
@require_role(*ALL_ROLES)
def tickets():
    account_id = _require_own_account()
    rows = query(
        f"""SELECT t.id, t.type, t.playerGuid, t.name, t.description, t.createTime,
                   t.lastModifiedTime, t.completed, t.escalated, t.comment, t.response
            FROM {CHAR_DB}.gm_ticket t
            WHERE t.playerGuid IN (
                SELECT guid FROM {CHAR_DB}.characters WHERE account=%s
            )
            ORDER BY t.createTime DESC LIMIT 200""",
        (account_id,),
    )
    return render_template("me_tickets.html", rows=rows, nav="me")
