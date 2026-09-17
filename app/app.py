#!/usr/bin/env python3
"""
AzerothCore realm admin panel.

Binds to loopback; a reverse proxy terminates TLS in front of it (see
deploy/). Account passwords use the same SRP6 parameters as the worldserver,
verified byte-for-byte against an account created by the server's own
console.

Destructive operations are delegated to the worldserver over loopback SOAP so
the server's own cleanup logic runs, rather than deleting rows underneath it.
"""
import os
import time

from flask import (
    Flask, abort, flash, redirect, render_template, request, session, url_for
)
from werkzeug.middleware.proxy_fix import ProxyFix

import gamedata
from core import (
    ADMIN_CFG, AUTH_DB, EXPANSIONS, GM_LEVELS, ROLE_ADMIN, ROLE_GAMEMASTER,
    ROLE_MODERATOR, ROLE_NAMES, ROLE_PLAYER, authenticate, check_csrf,
    close_db, csrf_token, query, server_online,
)
import bp_accounts
import bp_chars
import bp_customize
import bp_mod
import bp_realm
import bp_self
import bp_server
import bp_tracker
import bp_world

# Bounded so a hostile client cannot grow this dict without limit (SECURITY.md
# known-weaknesses #3 in earlier versions of this panel). Per-IP AND
# per-username buckets, so a distributed attempt against one account and a
# scan across many accounts from one address are both slowed down.
MAX_LOGIN_FAILURES = 8
LOCKOUT_SECONDS = 300
MAX_TRACKED_ENTRIES = 4096
_login_failures = {}


def _prune_login_failures():
    if len(_login_failures) <= MAX_TRACKED_ENTRIES:
        return
    now = time.time()
    for key, (_, until) in list(_login_failures.items()):
        if now >= until:
            _login_failures.pop(key, None)
    if len(_login_failures) > MAX_TRACKED_ENTRIES:
        # Still too large (all buckets active): drop the oldest half.
        for key in list(_login_failures)[: len(_login_failures) // 2]:
            _login_failures.pop(key, None)


def _locked_out(key):
    fails, until = _login_failures.get(key, (0, 0))
    return fails >= MAX_LOGIN_FAILURES and time.time() < until


def _record_failure(key):
    fails, _ = _login_failures.get(key, (0, 0))
    _login_failures[key] = (fails + 1, time.time() + LOCKOUT_SECONDS)
    _prune_login_failures()


# BEHIND_TLS_PROXY=1 (set by the systemd unit once the proxy is in front of
# this app) tells Flask to mark the session cookie Secure and to trust a
# single hop of X-Forwarded-For/Proto from the proxy. Without it, both stay
# off: an app bound only to loopback with no proxy has nothing valid to trust.
BEHIND_TLS_PROXY = os.environ.get("ACORE_WEBADMIN_BEHIND_TLS_PROXY", "0") == "1"

app = Flask(__name__)
app.secret_key = ADMIN_CFG["WEB_SECRET_KEY"]
if not app.secret_key or len(app.secret_key) < 32:
    raise RuntimeError(
        "WEB_SECRET_KEY is missing or too short in webadmin.env - "
        "refusing to start with a weak session-signing key."
    )
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=BEHIND_TLS_PROXY,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

if BEHIND_TLS_PROXY:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0)

app.register_blueprint(bp_accounts.bp)
app.register_blueprint(bp_server.bp)
app.register_blueprint(bp_chars.bp)
app.register_blueprint(bp_customize.bp)
app.register_blueprint(bp_mod.bp)
app.register_blueprint(bp_realm.bp)
app.register_blueprint(bp_self.bp)
app.register_blueprint(bp_tracker.bp)
app.register_blueprint(bp_world.bp)

app.teardown_appcontext(close_db)

app.jinja_env.globals.update(
    csrf_token=csrf_token, EXPANSIONS=EXPANSIONS, GM_LEVELS=GM_LEVELS,
    ROLE_NAMES=ROLE_NAMES, ROLE_PLAYER=ROLE_PLAYER, ROLE_MODERATOR=ROLE_MODERATOR,
    ROLE_GAMEMASTER=ROLE_GAMEMASTER, ROLE_ADMIN=ROLE_ADMIN,
    race_name=gamedata.race_name, race_faction=gamedata.race_faction,
    class_name=gamedata.class_name, class_color=gamedata.class_color,
    map_name=gamedata.map_name, zone_name=gamedata.zone_name,
)


@app.after_request
def security_headers(resp):
    """Defence-in-depth headers. CSP is intentionally strict: this app ships
    no external assets and no inline event handlers."""
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; script-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    )
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = (
        "geolocation=(), camera=(), microphone=(), payment=()"
    )
    if BEHIND_TLS_PROXY:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if session.get("authed"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


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


GLOBAL_RATE_WINDOW = 60
GLOBAL_RATE_MAX = 120  # generous: the busiest legitimate flow is the console, one POST per click
_request_counts = {}


@app.before_request
def global_rate_limit():
    """Coarse abuse guard: bounds expensive unauthenticated and authenticated
    POST traffic per source address. Not a substitute for a proxy-level
    limiter under real internet exposure - see docs/SECURITY.md."""
    if request.method != "POST":
        return
    ip = request.remote_addr or "unknown"
    now = time.time()
    window_start, count = _request_counts.get(ip, (now, 0))
    if now - window_start > GLOBAL_RATE_WINDOW:
        window_start, count = now, 0
    count += 1
    _request_counts[ip] = (window_start, count)
    if len(_request_counts) > MAX_TRACKED_ENTRIES:
        for key, (ws, _) in list(_request_counts.items()):
            if now - ws > GLOBAL_RATE_WINDOW:
                _request_counts.pop(key, None)
    if count > GLOBAL_RATE_MAX:
        abort(429, "Too many requests - slow down.")


@app.before_request
def csrf_protect():
    if request.method == "POST":
        # Reject anything that isn't a plain HTML form post before it reaches
        # a view: this app has no JSON API and no file uploads, so any other
        # content type is either a mistake or a cross-origin attempt that
        # cannot carry our CSRF token.
        ctype = (request.content_type or "").split(";", 1)[0].strip().lower()
        if ctype not in ("application/x-www-form-urlencoded", "multipart/form-data"):
            abort(400, "Unsupported content type.")
        check_csrf()


@app.context_processor
def inject_nav():
    """Nav chrome. Must never raise: error pages render through this too."""
    if not session.get("authed"):
        return {"active_realm": None, "world_up": None, "role": None, "session_account_id": None}
    try:
        realm = query(f"SELECT name, address, port FROM {AUTH_DB}.realmlist LIMIT 1", one=True)
    except Exception:
        realm = None
    try:
        up = server_online()
    except Exception:
        up = False
    return {
        "active_realm": realm, "world_up": up,
        "role": session.get("role", ROLE_PLAYER),
        "session_account_id": session.get("account_id"),
    }


def _default_landing(role):
    if role in (ROLE_ADMIN, ROLE_GAMEMASTER):
        return url_for("accounts.index")
    if role == ROLE_MODERATOR:
        return url_for("mod.tickets")
    return url_for("self.index")


def _safe_next(role):
    """Only ever redirect to a same-app relative path, never an external URL."""
    target = request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return _default_landing(role)


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = request.remote_addr or "unknown"
    if _locked_out(f"ip:{ip}"):
        return render_template("login.html", locked=LOCKOUT_SECONDS), 429

    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        user_key = f"user:{user.strip().lower()[:64]}"
        if _locked_out(user_key):
            return render_template("login.html", locked=LOCKOUT_SECONDS), 429

        identity = authenticate(user, pw)
        if identity:
            session.clear()  # prevent session fixation across the privilege boundary
            session["authed"] = True
            session["account_id"] = identity["account_id"]
            session["role"] = identity["role"]
            session["username"] = identity["username"]
            session["_last_seen"] = int(time.time())
            session.permanent = True
            _login_failures.pop(f"ip:{ip}", None)
            _login_failures.pop(user_key, None)
            return redirect(_safe_next(identity["role"]))

        _record_failure(f"ip:{ip}")
        _record_failure(user_key)
        flash("Invalid credentials.", "error")
    return render_template("login.html", locked=0)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/livez")
def livez():
    """Process-liveness probe; deliberately avoids external dependencies."""
    return {"status": "ok"}, 200


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


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403,
                           message="Your role does not have access to that."), 403


@app.errorhandler(413)
def too_large(_e):
    return render_template("error.html", code=413,
                           message="That request was too large."), 413


@app.errorhandler(429)
def rate_limited(e):
    return render_template("error.html", code=429,
                           message=getattr(e, "description", "Too many requests.")), 429


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500,
                           message="Something went wrong handling that request."), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8090, debug=False)
