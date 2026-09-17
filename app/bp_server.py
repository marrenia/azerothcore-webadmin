"""Server control, broadcasting, and the raw command console."""
import re

from flask import (
    Blueprint, flash, redirect, render_template, request, session, url_for
)

from core import (
    AUTH_DB, CHAR_DB, login_required, query, realm_stats, server_info, soap,
)
from soap import SoapError

bp = Blueprint("server", __name__)

# Commands the console refuses outright. These either hand out a shell-like
# capability or are near-guaranteed to be a mistake from a web form.
CONSOLE_DENY = (
    "account delete",      # use the Accounts tab, which confirms by name
    "character erase",     # same
    "character deleted purge",
    "server exit",         # leaves the realm down with no auto-restart
)


def _shutdown_style_command(kind, delay, reason):
    """Build a server shutdown/restart command."""
    delay = max(0, int(delay))
    cmd = f"server {kind} {delay}"
    if reason:
        cmd += f" {reason}"
    return cmd


@bp.route("/server")
@login_required
def dashboard():
    info = server_info()
    realm = query(f"SELECT * FROM {AUTH_DB}.realmlist LIMIT 1", one=True)
    current_motd = ""
    if info:
        try:
            raw = soap.command("server motd", timeout=6)
            # Show just the enUS line; the server prints one per locale.
            for line in raw.splitlines():
                if line.strip().startswith("enUS:"):
                    current_motd = line.split(":", 1)[1].strip()
                    break
            else:
                current_motd = raw.strip()
        except SoapError:
            current_motd = ""
    return render_template("server.html", info=info, realm=realm,
                           current_motd=current_motd, locales=MOTD_LOCALES,
                           stats=realm_stats(), nav="server")


# `server set motd` takes a locale before the text; without one the first word
# is parsed as the locale and the command fails.
MOTD_LOCALES = ("enUS", "koKR", "frFR", "deDE", "zhCN", "zhWE", "esES", "esMX", "ruRU")


@bp.route("/server/motd", methods=["POST"])
@login_required
def set_motd():
    motd = (request.form.get("motd") or "").strip()
    locale = request.form.get("locale", "enUS")
    if locale not in MOTD_LOCALES:
        locale = "enUS"
    if not motd:
        flash("Message of the day cannot be empty.", "error")
        return redirect(url_for("server.dashboard"))
    if len(motd) > 255:
        flash("Message of the day is limited to 255 characters.", "error")
        return redirect(url_for("server.dashboard"))
    try:
        soap.command(f"server set motd {locale} {motd}")
        flash(f"Message of the day updated ({locale}).", "ok")
    except SoapError as exc:
        flash(f"Could not set the MOTD: {exc}", "error")
    return redirect(url_for("server.dashboard"))


@bp.route("/server/broadcast", methods=["POST"])
@login_required
def broadcast():
    text = (request.form.get("message") or "").strip()
    kind = request.form.get("kind", "announce")
    if kind not in ("announce", "notify", "gmannounce", "gmnotify"):
        kind = "announce"
    if not text:
        flash("Message cannot be empty.", "error")
        return redirect(url_for("server.dashboard"))
    if len(text) > 255:
        flash("Message is limited to 255 characters.", "error")
        return redirect(url_for("server.dashboard"))
    try:
        soap.command(f"{kind} {text}")
        flash(f"Broadcast sent ({kind}).", "ok")
    except SoapError as exc:
        flash(f"Broadcast failed: {exc}", "error")
    return redirect(url_for("server.dashboard"))


@bp.route("/server/saveall", methods=["POST"])
@login_required
def saveall():
    try:
        out = soap.command("saveall", timeout=60)
        flash(f"All players saved. {out}".strip(), "ok")
    except SoapError as exc:
        flash(f"Save failed: {exc}", "error")
    return redirect(url_for("server.dashboard"))


@bp.route("/server/power", methods=["POST"])
@login_required
def power():
    action = request.form.get("action", "")
    reason = (request.form.get("reason") or "").strip()[:120]
    try:
        delay = int(request.form.get("delay", 60))
    except ValueError:
        delay = 60
    delay = max(0, min(delay, 86400))

    if action == "restart":
        if (request.form.get("confirm") or "").strip().upper() != "RESTART":
            flash("Restart cancelled: type RESTART to confirm.", "error")
            return redirect(url_for("server.dashboard"))
        cmd = _shutdown_style_command("restart", delay, reason)
        note = (f"Restart scheduled in {delay}s. systemd will bring the world "
                "back up automatically.")
    elif action == "shutdown":
        if (request.form.get("confirm") or "").strip().upper() != "SHUTDOWN":
            flash("Shutdown cancelled: type SHUTDOWN to confirm.", "error")
            return redirect(url_for("server.dashboard"))
        cmd = _shutdown_style_command("shutdown", delay, reason)
        note = (f"Shutdown scheduled in {delay}s. The world will stay DOWN until you "
                "start it again (systemctl start acore-worldserver).")
    elif action == "cancel":
        cmd = "server shutdown cancel"
        note = "Any pending shutdown or restart was cancelled."
    else:
        flash("Unknown action.", "error")
        return redirect(url_for("server.dashboard"))

    try:
        out = soap.command(cmd)
        flash(f"{note} {out}".strip(), "ok")
    except SoapError as exc:
        flash(f"Command failed: {exc}", "error")
    return redirect(url_for("server.dashboard"))


@bp.route("/server/closed", methods=["POST"])
@login_required
def set_closed():
    state = "on" if request.form.get("state") == "on" else "off"
    try:
        soap.command(f"server set closed {state}")
        flash("Realm is now closed to non-GM logins." if state == "on"
              else "Realm is open to all logins.", "ok")
    except SoapError as exc:
        flash(f"Could not change realm state: {exc}", "error")
    return redirect(url_for("server.dashboard"))


# ---------------------------------------------------------------- console

@bp.route("/console", methods=["GET", "POST"])
@login_required
def console():
    history = session.get("console_history", [])

    if request.method == "POST":
        cmd = (request.form.get("command") or "").strip()
        if not cmd:
            flash("Enter a command.", "error")
            return redirect(url_for("server.console"))
        if len(cmd) > 500:
            flash("Command is too long.", "error")
            return redirect(url_for("server.console"))

        lowered = re.sub(r"\s+", " ", cmd.lower())
        blocked = next((d for d in CONSOLE_DENY if lowered.startswith(d)), None)
        if blocked:
            flash(
                f"'{blocked}' is blocked here. Destructive deletes live on the "
                "Accounts tab, which confirms the exact name first.", "error",
            )
            return redirect(url_for("server.console"))

        try:
            out = soap.command(cmd, timeout=45)
            entry = {"cmd": cmd, "out": out or "(no output)", "ok": True}
        except SoapError as exc:
            entry = {"cmd": cmd, "out": str(exc), "ok": False}

        history = ([entry] + history)[:25]
        session["console_history"] = history
        session.modified = True
        return redirect(url_for("server.console"))

    return render_template("console.html", history=history, nav="console",
                           denied=CONSOLE_DENY)


@bp.route("/console/clear", methods=["POST"])
@login_required
def console_clear():
    session.pop("console_history", None)
    return redirect(url_for("server.console"))
