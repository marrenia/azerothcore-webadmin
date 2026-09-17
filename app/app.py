#!/usr/bin/env python3
"""
AzerothCore realm admin panel.

Binds to the tailnet interface only; see acore-webadmin.service.

Account passwords use the same SRP6 parameters as the worldserver, verified
byte-for-byte against an account created by the server's own console.

Destructive operations are delegated to the worldserver over loopback SOAP so
the server's own cleanup logic runs, rather than deleting rows underneath it.
"""
import hmac
import time

from flask import (
    Flask, flash, redirect, render_template, request, session, url_for
)

import gamedata
from core import (
    ADMIN_CFG, AUTH_DB, EXPANSIONS, GM_LEVELS, check_csrf, close_db,
    csrf_token, query, server_online,
)
import bp_accounts
import bp_chars
import bp_customize
import bp_mod
import bp_realm
import bp_server
import bp_tracker
import bp_world

MAX_LOGIN_FAILURES = 8
LOCKOUT_SECONDS = 300
_login_failures = {}

app = Flask(__name__)
app.secret_key = ADMIN_CFG["WEB_SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
)

app.register_blueprint(bp_accounts.bp)
app.register_blueprint(bp_server.bp)
app.register_blueprint(bp_chars.bp)
app.register_blueprint(bp_customize.bp)
app.register_blueprint(bp_mod.bp)
app.register_blueprint(bp_realm.bp)
app.register_blueprint(bp_tracker.bp)
app.register_blueprint(bp_world.bp)

app.teardown_appcontext(close_db)

app.jinja_env.globals.update(
    csrf_token=csrf_token, EXPANSIONS=EXPANSIONS, GM_LEVELS=GM_LEVELS,
    race_name=gamedata.race_name, race_faction=gamedata.race_faction,
    class_name=gamedata.class_name, class_color=gamedata.class_color,
    map_name=gamedata.map_name, zone_name=gamedata.zone_name,
)


@app.template_filter("ts")
def _ts(value):
    """Render a unix timestamp as a readable UTC time."""
    if not value:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(value)))
    except (ValueError, OSError):
        return str(value)


@app.template_filter("until")
def _until(value):
    if not value:
        return "—"
    delta = int(value) - int(time.time())
    if delta <= 0:
        return "expired"
    days, rem = divmod(delta, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@app.before_request
def csrf_protect():
    if request.method == "POST":
        check_csrf()


@app.context_processor
def inject_nav():
    """Nav chrome. Must never raise: error pages render through this too."""
    if not session.get("authed"):
        return {"active_realm": None, "world_up": None}
    try:
        realm = query(f"SELECT name, address, port FROM {AUTH_DB}.realmlist LIMIT 1", one=True)
    except Exception:
        realm = None
    try:
        up = server_online()
    except Exception:
        up = False
    return {"active_realm": realm, "world_up": up}


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = request.remote_addr or "unknown"
    fails, until = _login_failures.get(ip, (0, 0))
    if fails >= MAX_LOGIN_FAILURES and time.time() < until:
        return render_template("login.html", locked=int(until - time.time())), 429

    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if (hmac.compare_digest(user, ADMIN_CFG["WEB_ADMIN_USER"])
                and hmac.compare_digest(pw, ADMIN_CFG["WEB_ADMIN_PASS"])):
            session.clear()
            session["authed"] = True
            session.permanent = True
            _login_failures.pop(ip, None)
            return redirect(request.args.get("next") or url_for("accounts.index"))
        _login_failures[ip] = (fails + 1, time.time() + LOCKOUT_SECONDS)
        flash("Invalid credentials.", "error")
    return render_template("login.html", locked=0)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    try:
        query("SELECT 1")
        return {"status": "ok", "worldserver": "up" if server_online() else "down"}, 200
    except Exception:
        return {"status": "db-unavailable"}, 503


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404,
                           message="That page or record does not exist."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400,
                           message=getattr(e, "description", "Bad request.")), 400


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500,
                           message="Something went wrong handling that request."), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8090, debug=False)
