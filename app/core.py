"""Shared configuration, database access, auth and helpers for the admin panel."""
import functools
import hashlib
import hmac
import os
import re
import secrets
import time

import pymysql
from flask import abort, flash, g, redirect, request, session, url_for

from soap import SoapClient, SoapError


def load_env(path):
    values = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k] = v
    return values


# Where the credential files live. Overridable so the panel can be installed
# under a different prefix, or run twice on one host against two realms.
CONFIG_DIR = os.environ.get("ACORE_WEBADMIN_CONFIG_DIR", "/etc/acore")

DB_CFG = load_env(os.path.join(CONFIG_DIR, "webdb.env"))
ADMIN_CFG = load_env(os.path.join(CONFIG_DIR, "webadmin.env"))
SOAP_CFG = load_env(os.path.join(CONFIG_DIR, "soap.env"))

DB_HOST = DB_CFG.get("WEB_DB_HOST", "127.0.0.1")
DB_PORT = int(DB_CFG.get("WEB_DB_PORT", 3306))

# AzerothCore's stock database names. Installs that renamed them can override
# here rather than editing every query.
AUTH_DB = DB_CFG.get("AUTH_DB", "acore_auth")
CHAR_DB = DB_CFG.get("CHAR_DB", "acore_characters")
WORLD_DB = DB_CFG.get("WORLD_DB", "acore_world")

MAX_USERNAME = 16
MAX_PASSWORD = 16
USERNAME_RE = re.compile(r"^[A-Za-z0-9]+$")
CHARNAME_RE = re.compile(r"^[A-Za-z]{2,12}$")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

EXPANSIONS = {0: "Classic", 1: "The Burning Crusade", 2: "Wrath of the Lich King"}
GM_LEVELS = {0: "Player", 1: "Moderator", 2: "Game Master", 3: "Administrator"}

# ---------------------------------------------------------------- roles
#
# Role is derived from acore_auth.account_access.gmlevel (RealmID=-1, "all
# realms"), same numbering AzerothCore itself uses. No row, or gmlevel 0,
# means PLAYER. The bootstrap WEB_ADMIN identity (from webadmin.env) is not a
# database account at all; it is always ADMIN and carries no account_id.
ROLE_PLAYER = 0
ROLE_MODERATOR = 1
ROLE_GAMEMASTER = 2
ROLE_ADMIN = 3
ALL_ROLES = (ROLE_PLAYER, ROLE_MODERATOR, ROLE_GAMEMASTER, ROLE_ADMIN)
STAFF_ROLES = (ROLE_MODERATOR, ROLE_GAMEMASTER, ROLE_ADMIN)
ROLE_NAMES = GM_LEVELS

# How long a session may go without a request before it is treated as expired,
# independent of the absolute PERMANENT_SESSION_LIFETIME.
SESSION_IDLE_SECONDS = 30 * 60

BOT_PREFIX = ADMIN_CFG.get("BOT_ACCOUNT_PREFIX", "rndbot").upper()

soap = SoapClient(SOAP_CFG)


# ---------------------------------------------------------------- srp6

SRP6_N = int("894B645E89E1535BBDAD5B8B290650530801B18EBFBF5E8FAB3C82872A3E9BB7", 16)
SRP6_g = 7


def srp6_make(username, password, salt=None):
    if salt is None:
        salt = secrets.token_bytes(32)
    h1 = hashlib.sha1(f"{username.upper()}:{password.upper()}".encode()).digest()
    h2 = hashlib.sha1(salt + h1).digest()
    x = int.from_bytes(h2, byteorder="little")
    return salt, pow(SRP6_g, x, SRP6_N).to_bytes(32, byteorder="little")


# ---------------------------------------------------------------- db

def get_db():
    if "db" not in g:
        g.db = pymysql.connect(
            host=DB_HOST, port=DB_PORT,
            user=DB_CFG["WEB_DB_USER"], password=DB_CFG["WEB_DB_PASS"],
            charset="utf8mb4", autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    with get_db().cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, args)
        affected = cur.rowcount
    db.commit()
    return affected


# ---------------------------------------------------------------- auth

def account_role(account_id):
    """Role for a database account: gmlevel via account_access, RealmID=-1.

    No row, or gmlevel 0, is PLAYER. Out-of-range values are clamped rather
    than trusted verbatim, in case a hand-edited row holds something odd.
    """
    row = query(
        f"SELECT gmlevel FROM {AUTH_DB}.account_access WHERE id=%s AND RealmID=-1",
        (account_id,), one=True,
    )
    level = int(row["gmlevel"]) if row else 0
    return max(ROLE_PLAYER, min(ROLE_ADMIN, level))


def account_login_blocked(account_id):
    """True when a database account must not be allowed to hold a session.

    Covers both the login check and the per-request session-refresh check, so
    a lock or ban applied mid-session takes effect on the account's very next
    request rather than only at its next login.
    """
    row = query(f"SELECT locked FROM {AUTH_DB}.account WHERE id=%s", (account_id,), one=True)
    if not row or row["locked"]:
        return True
    return ban_state(account_id) is not None


def authenticate(username, password):
    """Check credentials against the bootstrap admin, then a database account.

    Returns a dict with account_id/role/username on success, or None. Always
    does the same shape of work on both paths and never reveals which check
    failed, so a caller cannot use response timing or content to tell "wrong
    password" from "no such account".
    """
    username = (username or "")
    password = (password or "")

    if (hmac.compare_digest(username, ADMIN_CFG["WEB_ADMIN_USER"])
            and hmac.compare_digest(password, ADMIN_CFG["WEB_ADMIN_PASS"])):
        return {"account_id": None, "role": ROLE_ADMIN, "username": ADMIN_CFG["WEB_ADMIN_USER"]}

    uname = username.strip().upper()
    if not uname or not USERNAME_RE.match(uname) or len(uname) > MAX_USERNAME:
        return None
    acc = query(
        f"SELECT id, username, salt, verifier FROM {AUTH_DB}.account WHERE username=%s",
        (uname,), one=True,
    )
    if not acc or not acc["salt"] or not acc["verifier"]:
        return None
    _, computed = srp6_make(uname, password, salt=acc["salt"])
    if not hmac.compare_digest(computed, bytes(acc["verifier"])):
        return None
    if account_login_blocked(acc["id"]):
        return None
    return {"account_id": acc["id"], "role": account_role(acc["id"]), "username": acc["username"]}


def _session_still_valid():
    """Per-request re-check: idle timeout, plus locked/banned for DB accounts.

    Runs on every request through a protected route (require_role wraps all
    of them), so a lock or ban applied to a logged-in account is enforced on
    its very next click, not just at the next login.
    """
    now = int(time.time())
    last_seen = session.get("_last_seen")
    if last_seen is not None and now - int(last_seen) > SESSION_IDLE_SECONDS:
        return False
    session["_last_seen"] = now

    account_id = session.get("account_id")
    if account_id is None:
        return True  # bootstrap WEB_ADMIN identity - no database row to revoke
    if account_login_blocked(account_id):
        return False
    session["role"] = account_role(account_id)
    return True


def require_role(*roles):
    """Default-deny route guard. Every protected route must carry this.

    Unauthenticated requests are redirected to the login page (there is
    nothing sensitive to leak by doing so). Authenticated requests whose role
    is not in `roles` get a flat 403 - never a redirect, so a logged-in
    low-privilege user cannot be bounced into a login loop that implies their
    session is invalid.
    """
    allowed = set(roles)

    def decorator(view):
        @functools.wraps(view)
        def wrapped(*a, **kw):
            if not session.get("authed"):
                return redirect(url_for("login", next=request.path))
            if not _session_still_valid():
                session.clear()
                flash("Your session ended: sign in again.", "error")
                return redirect(url_for("login"))
            if session.get("role", ROLE_PLAYER) not in allowed:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return decorator


def current_account_id():
    return session.get("account_id")


def current_role():
    return session.get("role", ROLE_PLAYER)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("_csrf", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(400, "CSRF token mismatch - reload the page and try again.")


# ---------------------------------------------------------------- server

def server_info():
    """Parsed output of `server info`, or None when the server is down.

    Cached per request: several views and the nav all want this, and each call
    is a SOAP round-trip to the worldserver.
    """
    if "server_info" in g:
        return g.server_info
    try:
        raw = soap.command("server info", timeout=6)
    except SoapError:
        g.server_info = None
        return None
    info = {"raw": raw, "revision": "", "players": None, "max_players": None,
            "uptime": "", "latency": "", "characters": None}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("AzerothCore rev."):
            info["revision"] = line
        m = re.search(r"Connected players:\s*(\d+)", line)
        if m:
            info["players"] = int(m.group(1))
        m = re.search(r"Characters in world:\s*(\d+)", line)
        if m:
            info["characters"] = int(m.group(1))
        m = re.search(r"Server uptime:\s*(.+)", line)
        if m:
            info["uptime"] = m.group(1).strip()
        m = re.search(r"latency:\s*(.+)", line, re.I)
        if m:
            info["latency"] = m.group(1).strip()
    g.server_info = info
    return info


def server_online():
    return server_info() is not None


# ---------------------------------------------------------------- helpers

def validate_credentials(username, password, require_password=True):
    errors = []
    if not username or not USERNAME_RE.match(username):
        errors.append("Username must be letters and numbers only.")
    elif len(username) > MAX_USERNAME:
        errors.append(f"Username must be at most {MAX_USERNAME} characters.")
    if require_password or password:
        if not password:
            errors.append("Password is required.")
        elif len(password) > MAX_PASSWORD:
            errors.append(
                f"Password must be at most {MAX_PASSWORD} characters "
                "(the 3.3.5 client cannot send more)."
            )
    return errors


def ban_state(account_id):
    row = query(
        f"""SELECT bandate, unbandate, banreason, bannedby FROM {AUTH_DB}.account_banned
            WHERE id=%s AND active=1 ORDER BY bandate DESC LIMIT 1""",
        (account_id,), one=True,
    )
    if not row:
        return None
    permanent = row["unbandate"] == row["bandate"]
    if not permanent and row["unbandate"] <= int(time.time()):
        return None
    row["permanent"] = permanent
    return row


def realm_stats():
    totals = query(
        f"""SELECT SUM(username NOT LIKE %s) AS humans,
                   SUM(username LIKE %s) AS bots,
                   SUM(online = 1 AND username NOT LIKE %s) AS humans_online
            FROM {AUTH_DB}.account""",
        (f"{BOT_PREFIX}%",) * 3, one=True,
    )
    chars = query(
        f"""SELECT COUNT(*) AS total, SUM(online = 1) AS online
            FROM {CHAR_DB}.characters WHERE deleteDate IS NULL""", one=True,
    )
    deleted = query(
        f"SELECT COUNT(*) AS n FROM {CHAR_DB}.characters WHERE deleteDate IS NOT NULL",
        one=True)["n"]
    gm = query(
        f"SELECT COUNT(*) AS n FROM {AUTH_DB}.account_access WHERE RealmID=-1 AND gmlevel>0",
        one=True)["n"]
    banned = query(
        f"""SELECT COUNT(DISTINCT id) AS n FROM {AUTH_DB}.account_banned
            WHERE active=1 AND (unbandate = bandate OR unbandate > UNIX_TIMESTAMP())""",
        one=True)["n"]
    tickets = query(
        f"SELECT COUNT(*) AS n FROM {CHAR_DB}.gm_ticket WHERE completed=0", one=True)["n"]
    return {
        "accounts": int(totals["humans"] or 0),
        "bots": int(totals["bots"] or 0),
        "accounts_online": int(totals["humans_online"] or 0),
        "characters": int(chars["total"] or 0),
        "chars_online": int(chars["online"] or 0),
        "deleted": deleted,
        "gm": gm,
        "banned": banned,
        "tickets": tickets,
    }


def safe_arg(text):
    """True when text is safe to embed in a quoted SOAP command argument.

    The command string is a single line parsed by the server, so a double quote
    or newline would change its shape. Reject rather than silently mangle.
    """
    return not any(ch in text for ch in ('"', "\n", "\r", "\t"))


def flash_soap(result, ok_message):
    """Uniform flash handling for a SOAP command result."""
    from flask import flash
    text = (result or "").strip()
    flash(f"{ok_message}{(' - ' + text) if text else ''}", "ok")
