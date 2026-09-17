"""
Player tracker: live position and activity sampling.

What is and is not live, precisely:

  * Map / zone / area, alive state, level, XP, money, session length and latency
    come from the server's `pinfo` command, which reads the player object in
    memory. These are LIVE - a teleport shows up on the next sample.

  * Exact x/y/z coordinates are NOT exposed by any console-capable command
    (`gps` is flagged Console::No). They only exist in the database, written on
    periodic save, so they lag. The UI labels them with their save age, and a
    'sync' action runs `saveall` to force them current.

  * The bot AI's internal strategy is not reachable over SOAP either - the
    playerbots stats command logs to the server log rather than returning
    output. So "what it is doing" is DERIVED by diffing consecutive samples
    (zone changed, XP gained, money changed, died, levelled), not read directly.
"""
import os
import re
import sqlite3
import time

STATE_DIR = os.environ.get("ACORE_STATE_DIR", "/var/lib/acore-webadmin")
DB_PATH = os.path.join(STATE_DIR, "tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guid      INTEGER NOT NULL,
    name      TEXT    NOT NULL,
    ts        INTEGER NOT NULL,
    online    INTEGER NOT NULL,
    alive     INTEGER,
    level     INTEGER,
    xp        INTEGER,
    xp_max    INTEGER,
    money     INTEGER,
    map       TEXT,
    zone      TEXT,
    area      TEXT,
    online_for TEXT,
    latency   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_guid_ts ON samples (guid, ts DESC);
CREATE TABLE IF NOT EXISTS watched (
    guid    INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    added   INTEGER NOT NULL
);
"""


def connect():
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- parsing

_MONEY_RE = re.compile(r"Money:\s*(?:(\d+)g)?\s*(?:(\d+)s)?\s*(?:(\d+)c)?")


def parse_pinfo(raw):
    """Turn `pinfo` output into a dict. Missing fields come back as None."""
    out = {
        "online": False, "alive": None, "level": None, "xp": None, "xp_max": None,
        "money": None, "map": None, "zone": None, "area": None,
        "online_for": None, "played": None, "latency": None, "account": None,
        "race": None, "gmlevel": None, "phase": None, "last_ip": None,
    }
    if not raw:
        return out

    m = re.search(r"Level:\s*(\d+)\s*\((\d+)/(\d+)\s*XP", raw)
    if m:
        out["level"] = int(m.group(1))
        out["xp"] = int(m.group(2))
        out["xp_max"] = int(m.group(3))
    else:
        m = re.search(r"Level:\s*(\d+)", raw)
        if m:
            out["level"] = int(m.group(1))

    m = re.search(r"Alive \?:\s*(\w+)", raw)
    if m:
        out["alive"] = m.group(1).strip().lower() == "yes"

    m = _MONEY_RE.search(raw)
    if m and any(m.groups()):
        g, s, c = (int(x) if x else 0 for x in m.groups())
        out["money"] = g * 10000 + s * 100 + c

    m = re.search(r"Map:\s*([^,]+?)(?:,\s*Zone:\s*([^,]+?))?(?:,\s*Area:\s*(.+?))?\s*$",
                  raw, re.M)
    if m:
        out["map"] = (m.group(1) or "").strip() or None
        out["zone"] = (m.group(2) or "").strip() or None
        out["area"] = (m.group(3) or "").strip() or None

    m = re.search(r"Online for:\s*(.+?)\s*$", raw, re.M)
    if m:
        out["online_for"] = m.group(1).strip()
        out["online"] = True

    m = re.search(r"Played time:\s*(.+?)\s*$", raw, re.M)
    if m:
        out["played"] = m.group(1).strip()

    m = re.search(r"Latency:\s*(\d+)\s*ms", raw)
    if m:
        out["latency"] = int(m.group(1))

    m = re.search(r"Account:\s*(\S+)", raw)
    if m:
        out["account"] = m.group(1).rstrip(",")

    m = re.search(r"Race:\s*(.+?)\s*$", raw, re.M)
    if m:
        out["race"] = m.group(1).strip()

    m = re.search(r"GMLevel:\s*(\d+)", raw)
    if m:
        out["gmlevel"] = int(m.group(1))

    m = re.search(r"Phase:\s*(\d+)", raw)
    if m:
        out["phase"] = int(m.group(1))

    m = re.search(r"Last IP:\s*(\S+)", raw)
    if m:
        out["last_ip"] = m.group(1)

    return out


# ---------------------------------------------------------------- storage

def record(guid, name, info):
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO samples
               (guid, name, ts, online, alive, level, xp, xp_max, money,
                map, zone, area, online_for, latency)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (guid, name, int(time.time()), 1 if info["online"] else 0,
             None if info["alive"] is None else (1 if info["alive"] else 0),
             info["level"], info["xp"], info["xp_max"], info["money"],
             info["map"], info["zone"], info["area"], info["online_for"],
             info["latency"]),
        )
        conn.commit()
    finally:
        conn.close()


def history(guid, limit=60):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM samples WHERE guid=? ORDER BY ts DESC LIMIT ?",
            (guid, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def prune(days=7):
    conn = connect()
    try:
        cutoff = int(time.time()) - days * 86400
        n = conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,)).rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def watch_add(guid, name):
    conn = connect()
    try:
        conn.execute("INSERT OR REPLACE INTO watched (guid, name, added) VALUES (?,?,?)",
                     (guid, name, int(time.time())))
        conn.commit()
    finally:
        conn.close()


def watch_remove(guid):
    conn = connect()
    try:
        conn.execute("DELETE FROM watched WHERE guid=?", (guid,))
        conn.commit()
    finally:
        conn.close()


def watch_list():
    conn = connect()
    try:
        return [dict(r) for r in
                conn.execute("SELECT * FROM watched ORDER BY name").fetchall()]
    finally:
        conn.close()


def sample_count(guid):
    conn = connect()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM samples WHERE guid=?",
                            (guid,)).fetchone()["n"]
    finally:
        conn.close()


# ---------------------------------------------------------------- activity

def derive_activity(cur, prev):
    """Infer what the character is doing from two consecutive samples.

    Returns (headline, list_of_detail_strings). This is inference from
    sampling, not a read of the bot's AI state - the UI says so.
    """
    if not cur or not cur.get("online"):
        return "Offline", []

    details = []
    if cur.get("alive") is False:
        return "Dead", ["Waiting to release or be resurrected"]

    if not prev or not prev.get("online"):
        return ("In world", [f"Just seen in {cur.get('area') or cur.get('zone') or 'unknown'}"])

    moved_zone = prev.get("zone") != cur.get("zone")
    moved_area = prev.get("area") != cur.get("area")

    xp_gain = 0
    if cur.get("xp") is not None and prev.get("xp") is not None:
        if cur.get("level") == prev.get("level"):
            xp_gain = cur["xp"] - prev["xp"]
        elif (cur.get("level") or 0) > (prev.get("level") or 0):
            xp_gain = cur["xp"]

    money_delta = 0
    if cur.get("money") is not None and prev.get("money") is not None:
        money_delta = cur["money"] - prev["money"]

    levelled = (cur.get("level") or 0) > (prev.get("level") or 0)
    died = prev.get("alive") is True and cur.get("alive") is False
    revived = prev.get("alive") is False and cur.get("alive") is True

    if levelled:
        details.append(f"Levelled up to {cur['level']}")
    if xp_gain > 0:
        details.append(f"+{xp_gain:,} XP since last sample")
    if money_delta > 0:
        details.append(f"+{money_delta // 10000}g {(money_delta % 10000) // 100}s earned")
    elif money_delta < 0:
        d = -money_delta
        details.append(f"-{d // 10000}g {(d % 10000) // 100}s spent")
    if revived:
        details.append("Back on their feet")

    if died:
        headline = "Died"
    elif moved_zone:
        headline = f"Travelling - {prev.get('zone') or '?'} to {cur.get('zone') or '?'}"
    elif xp_gain > 0 and moved_area:
        headline = f"Questing around {cur.get('area') or cur.get('zone')}"
    elif xp_gain > 0:
        headline = f"Fighting in {cur.get('area') or cur.get('zone')}"
    elif moved_area:
        headline = f"Moving through {cur.get('area') or cur.get('zone')}"
    elif money_delta != 0:
        headline = f"Trading or looting in {cur.get('area') or cur.get('zone')}"
    else:
        headline = f"Idle in {cur.get('area') or cur.get('zone') or 'unknown'}"

    return headline, details
