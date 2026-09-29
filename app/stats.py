"""Read-only access to mod-player-statistics data.

The module (https://github.com/ShaneBair/mod-player-statistics) writes one
append-only, player-centric event table to the characters database. This file
turns it into leaderboards, per-character summaries and timelines.

Two rules from the module's own documentation are load-bearing here:

* The actor is always the player whose statistic is being recorded, and
  actor_is_bot separates Playerbots from humans without losing either.
* Total deaths are cutover-aware. Count PLAYER_DEATH only above the
  canonical_player_death_v1 cutoff, and the legacy PLAYER_KILLED_BY_CREATURE /
  PVP_KILL facts only at or below it. Never add every death-shaped event
  together: above the cutoff those rows are detail, not extra deaths. On a
  fresh install the cutoff is 0, so a naive count happens to look right there
  and is wrong on any upgraded one.

Every query is parameterised. Table and database names come from config
(core.CHAR_DB / core.WORLD_DB), never from request input.
"""
import json
import threading
import time

import pymysql

import core
from core import ADMIN_CFG, CHAR_DB, WORLD_DB

EVENTS = f"{CHAR_DB}.mod_player_stats_events"
MIGRATIONS = f"{CHAR_DB}.mod_player_stats_migrations"
DEATH_MIGRATION = "canonical_player_death_v1"

KILL_TYPES = ("CREATURE_KILL", "CREATURE_KILL_PET")

POPULATIONS = {"human": "Humans", "bot": "Playerbots", "all": "Everyone"}
WINDOWS = {"1d": ("24 hours", 1), "7d": ("7 days", 7), "30d": ("30 days", 30),
           "all": ("All time", None)}


def _cfg_choice(key, choices, default):
    value = (ADMIN_CFG.get(key) or default).strip().lower()
    return value if value in choices else default


DEFAULT_POPULATION = _cfg_choice("STATS_DEFAULT_POPULATION", POPULATIONS, "human")
DEFAULT_WINDOW = _cfg_choice("STATS_DEFAULT_WINDOW", WINDOWS, "all")
try:
    CACHE_SECONDS = max(0, int(ADMIN_CFG.get("STATS_CACHE_SECONDS", "60")))
except ValueError:
    CACHE_SECONDS = 60

# MySQL/MariaDB error codes for "table does not exist" and "access denied to
# table". Either means the module is not installed or the grant is missing.
_MISSING_CODES = {1146, 1142}


class StatsUnavailable(Exception):
    """mod-player-statistics tables are missing or not readable."""


# ---------------------------------------------------------------- cache
#
# Leaderboards aggregate over a table that grows without bound, so identical
# requests inside CACHE_SECONDS share one result. Per-process: every gunicorn
# worker keeps its own, which is fine for data that is a minute stale anyway.

_cache = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 256


def _cached(key, fn):
    if CACHE_SECONDS <= 0:
        return fn()
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fn()
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now + CACHE_SECONDS, value)
    return value


def _run(sql, args=(), one=False):
    # pymysql hands back rows as a tuple; normalise so callers can concatenate
    # and mutate result lists without caring which driver shape they got.
    try:
        rows = core.query(sql, args, one=one)
        return rows if one else list(rows or ())
    except (pymysql.err.ProgrammingError, pymysql.err.OperationalError) as exc:
        if exc.args and exc.args[0] in _MISSING_CODES:
            raise StatsUnavailable(str(exc)) from exc
        raise


# ---------------------------------------------------------------- filters

def normalise_filters(population, window):
    population = population if population in POPULATIONS else DEFAULT_POPULATION
    window = window if window in WINDOWS else DEFAULT_WINDOW
    return population, window


def _where(population, window, alias="e", bot_col="actor_is_bot"):
    """SQL fragment + args for the population and time-window filters."""
    parts, args = [], []
    if population == "human":
        parts.append(f"{alias}.{bot_col} = 0")
    elif population == "bot":
        parts.append(f"{alias}.{bot_col} = 1")
    days = WINDOWS[window][1]
    if days:
        parts.append(f"{alias}.event_time >= NOW() - INTERVAL %s DAY")
        args.append(days)
    return ("".join(f" AND {p}" for p in parts)), args


def death_cutoff():
    """The canonical_player_death_v1 cutoff event id, or None if absent.

    Absent means the module's migration row was never written - its README
    describes this as the safe failure when the fresh schema is imported onto
    a populated table. Callers then count canonical PLAYER_DEATH only and say
    that legacy deaths are excluded, rather than guessing.
    """
    row = _run(f"SELECT cutoff_event_id FROM {MIGRATIONS} WHERE migration_key = %s",
               (DEATH_MIGRATION,), one=True)
    return int(row["cutoff_event_id"]) if row else None


def _death_facts_sql(cutoff, population, window, guid=None):
    """UNION of cutover-aware death facts, one row per death.

    Mirrors query 5b in the module's examples/dashboard_queries.sql. Legacy PvP
    deaths store the victim in the target fields, so that branch maps target_*
    to the actor columns. The population/window filters are applied per branch
    against the column that means "the dead character".
    """
    branches, args = [], []

    def add(select_sql, extra_where, extra_args, bot_col, guid_col):
        filt, fargs = _where(population, window, bot_col=bot_col)
        g_sql, g_args = "", []
        if guid is not None:
            g_sql, g_args = f" AND e.{guid_col} = %s", [guid]
        branches.append(f"{select_sql} FROM {EVENTS} e WHERE {extra_where}{filt}{g_sql}")
        args.extend(extra_args + fargs + g_args)

    add("SELECT e.actor_guid AS guid, e.actor_is_bot AS is_bot",
        "e.event_type = 'PLAYER_DEATH' AND e.id > %s", [cutoff or 0],
        "actor_is_bot", "actor_guid")
    if cutoff:
        add("SELECT e.actor_guid AS guid, e.actor_is_bot AS is_bot",
            "e.event_type = 'PLAYER_KILLED_BY_CREATURE' AND e.id <= %s", [cutoff],
            "actor_is_bot", "actor_guid")
        add("SELECT e.target_guid AS guid, e.target_is_bot AS is_bot",
            "e.event_type = 'PVP_KILL' AND e.id <= %s", [cutoff],
            "target_is_bot", "target_guid")
    return " UNION ALL ".join(branches), args


# ---------------------------------------------------------------- leaderboards

def _actor_board(event_types, population, window, limit, value_sql="COUNT(*)",
                 extra_where="", extra_args=()):
    filt, args = _where(population, window)
    placeholders = ", ".join(["%s"] * len(event_types))
    return _run(
        f"""SELECT e.actor_guid AS guid, MAX(e.actor_is_bot) AS is_bot,
                   c.name, c.race, c.class, c.level, {value_sql} AS value
            FROM {EVENTS} e
            LEFT JOIN {CHAR_DB}.characters c ON c.guid = e.actor_guid
            WHERE e.event_type IN ({placeholders}){extra_where}{filt}
            GROUP BY e.actor_guid, c.name, c.race, c.class, c.level
            HAVING value > 0
            ORDER BY value DESC, e.actor_guid ASC
            LIMIT %s""",
        tuple(event_types) + tuple(extra_args) + tuple(args) + (limit,),
    )


def _death_board(population, window, limit):
    cutoff = death_cutoff()
    union_sql, args = _death_facts_sql(cutoff, population, window)
    rows = _run(
        f"""SELECT d.guid, MAX(d.is_bot) AS is_bot, c.name, c.race, c.class, c.level,
                   COUNT(*) AS value
            FROM ({union_sql}) d
            LEFT JOIN {CHAR_DB}.characters c ON c.guid = d.guid
            GROUP BY d.guid, c.name, c.race, c.class, c.level
            ORDER BY value DESC, d.guid ASC
            LIMIT %s""",
        tuple(args) + (limit,),
    )
    return rows, cutoff


def _creature_board(event_types, population, window, limit):
    filt, args = _where(population, window)
    placeholders = ", ".join(["%s"] * len(event_types))
    rows = _run(
        f"""SELECT e.target_entry AS entry, COUNT(*) AS value
            FROM {EVENTS} e
            WHERE e.event_type IN ({placeholders}) AND e.target_entry > 0{filt}
            GROUP BY e.target_entry
            ORDER BY value DESC, e.target_entry ASC
            LIMIT %s""",
        tuple(event_types) + tuple(args) + (limit,),
    )
    names = creature_names([r["entry"] for r in rows])
    for r in rows:
        r["name"] = names.get(r["entry"])
    return rows


BOARDS = (
    # key, title, unit
    ("npc_kills", "NPC kills", "kills"),
    ("pvp_kills", "PvP kills", "kills"),
    ("deaths", "Deaths", "deaths"),
    ("quests", "Quests completed", "quests"),
    ("achievements", "Achievements", "earned"),
    ("levels", "Levels gained", "levels"),
)


def leaderboards(population, window, limit=10):
    population, window = normalise_filters(population, window)

    def build():
        deaths, cutoff = _death_board(population, window, limit)
        return {
            "npc_kills": _actor_board(KILL_TYPES, population, window, limit),
            "pvp_kills": _actor_board(("PVP_KILL",), population, window, limit),
            "deaths": deaths,
            "quests": _actor_board(("QUEST_COMPLETE",), population, window, limit),
            "achievements": _actor_board(("ACHIEVEMENT",), population, window, limit),
            # A level can go down (e.g. a GM command), so only count gains.
            "levels": _actor_board(("LEVEL_CHANGE",), population, window, limit,
                                   value_sql="SUM(GREATEST(e.value2 - e.value1, 0))"),
            "most_killed": _creature_board(KILL_TYPES, population, window, limit),
            "deadliest": _creature_board(("PLAYER_KILLED_BY_CREATURE",), population,
                                         window, limit),
            "death_cutoff": cutoff,
        }
    return _cached(("boards", population, window, limit), build)


def totals(population, window):
    population, window = normalise_filters(population, window)

    def build():
        filt, args = _where(population, window)
        by_type = {r["event_type"]: int(r["n"]) for r in _run(
            f"""SELECT e.event_type, COUNT(*) AS n FROM {EVENTS} e
                WHERE 1=1{filt} GROUP BY e.event_type""", tuple(args))}
        active = _run(
            f"SELECT COUNT(DISTINCT e.actor_guid) AS n FROM {EVENTS} e WHERE 1=1{filt}",
            tuple(args), one=True)
        cutoff = death_cutoff()
        union_sql, dargs = _death_facts_sql(cutoff, population, window)
        deaths = _run(f"SELECT COUNT(*) AS n FROM ({union_sql}) d", tuple(dargs), one=True)
        return {
            "npc_kills": sum(by_type.get(t, 0) for t in KILL_TYPES),
            "pvp_kills": by_type.get("PVP_KILL", 0),
            "deaths": int(deaths["n"] or 0),
            "quests": by_type.get("QUEST_COMPLETE", 0),
            "achievements": by_type.get("ACHIEVEMENT", 0),
            "active_characters": int(active["n"] or 0),
            "events": sum(by_type.values()),
        }
    return _cached(("totals", population, window), build)


def daily_activity(population, days=14):
    """Per-day NPC kills, deaths and quests for the last `days` days, oldest first."""
    population, _ = normalise_filters(population, "all")

    def build():
        filt, args = _where(population, "all")
        rows = _run(
            f"""SELECT DATE(e.event_time) AS day,
                       SUM(e.event_type IN ('CREATURE_KILL', 'CREATURE_KILL_PET')) AS kills,
                       SUM(e.event_type = 'QUEST_COMPLETE') AS quests,
                       SUM(e.event_type = 'PLAYER_DEATH') AS deaths
                FROM {EVENTS} e
                WHERE e.event_time >= CURDATE() - INTERVAL %s DAY{filt}
                GROUP BY DATE(e.event_time)
                ORDER BY day ASC""",
            (days - 1,) + tuple(args))
        peak = max((int(r["kills"] or 0) for r in rows), default=0)
        for r in rows:
            for k in ("kills", "quests", "deaths"):
                r[k] = int(r[k] or 0)
            r["pct"] = round(100 * r["kills"] / peak) if peak else 0
        return rows
    return _cached(("daily", population, days), build)


def coverage():
    """Oldest event overall and per population, so pruning is visible, not hidden.

    Hosts often prune bot rows (they dominate the table). Showing where the data
    starts keeps an "all time" bot leaderboard from being mistaken for lifetime.
    """
    def build():
        row = _run(
            f"""SELECT MIN(event_time) AS oldest,
                       MIN(CASE WHEN actor_is_bot = 0 THEN event_time END) AS oldest_human,
                       MIN(CASE WHEN actor_is_bot = 1 THEN event_time END) AS oldest_bot
                FROM {EVENTS}""", one=True)
        return row or {}
    return _cached(("coverage",), build)


# ---------------------------------------------------------------- characters

def character_summary(guid):
    """Lifetime totals, favourite prey, nemesis and recent timeline for one character."""
    by_type = {r["event_type"]: int(r["n"]) for r in _run(
        f"""SELECT event_type, COUNT(*) AS n FROM {EVENTS}
            WHERE actor_guid = %s GROUP BY event_type""", (guid,))}
    cutoff = death_cutoff()
    union_sql, args = _death_facts_sql(cutoff, "all", "all", guid=guid)
    deaths = _run(f"SELECT COUNT(*) AS n FROM ({union_sql}) d", tuple(args), one=True)
    levels = _run(
        f"""SELECT COALESCE(SUM(GREATEST(value2 - value1, 0)), 0) AS n FROM {EVENTS}
            WHERE actor_guid = %s AND event_type = 'LEVEL_CHANGE'""", (guid,), one=True)
    prey = _run(
        f"""SELECT target_entry AS entry, COUNT(*) AS value FROM {EVENTS}
            WHERE actor_guid = %s AND event_type IN ('CREATURE_KILL', 'CREATURE_KILL_PET')
              AND target_entry > 0
            GROUP BY target_entry ORDER BY value DESC, target_entry ASC LIMIT 5""", (guid,))
    nemesis = _run(
        f"""SELECT target_entry AS entry, COUNT(*) AS value FROM {EVENTS}
            WHERE actor_guid = %s AND event_type = 'PLAYER_KILLED_BY_CREATURE'
              AND target_entry > 0
            GROUP BY target_entry ORDER BY value DESC, target_entry ASC LIMIT 3""", (guid,))
    names = creature_names([r["entry"] for r in prey + nemesis])
    for r in prey + nemesis:
        r["name"] = names.get(r["entry"])
    return {
        "npc_kills": sum(by_type.get(t, 0) for t in KILL_TYPES),
        "pvp_kills": by_type.get("PVP_KILL", 0),
        "deaths": int(deaths["n"] or 0),
        "quests": by_type.get("QUEST_COMPLETE", 0),
        "achievements": by_type.get("ACHIEVEMENT", 0),
        "levels": int(levels["n"] or 0),
        "events": sum(by_type.values()),
        "prey": prey,
        "nemesis": nemesis,
        "timeline": timeline(guid),
        "legacy_deaths_excluded": cutoff is None,
    }


def timeline(guid, limit=40):
    rows = _run(
        f"""SELECT id, event_time, event_type, target_type, target_entry, target_guid,
                   target_is_bot, value1, value2, map_id, zone_id, area_id, source
            FROM {EVENTS} WHERE actor_guid = %s
            ORDER BY id DESC LIMIT %s""", (guid, limit))
    creatures = creature_names([r["target_entry"] for r in rows
                                if r["event_type"] in KILL_TYPES + ("PLAYER_KILLED_BY_CREATURE",)])
    quests = quest_names([r["target_entry"] for r in rows if r["event_type"] == "QUEST_COMPLETE"])
    victims = character_names([r["target_guid"] for r in rows if r["event_type"] == "PVP_KILL"])
    for r in rows:
        r["text"] = describe(r, creatures, quests, victims)
    return rows


def describe(r, creatures, quests, victims):
    """One-line human description of an event row."""
    t, entry = r["event_type"], r["target_entry"]
    creature = creatures.get(entry) or f"creature #{entry}"
    if t == "CREATURE_KILL":
        return f"Killed {creature} (level {r['value1']})"
    if t == "CREATURE_KILL_PET":
        return f"Pet killed {creature} (level {r['value1']})"
    if t == "PLAYER_KILLED_BY_CREATURE":
        return f"Killed by {creature} (level {r['value1']})"
    if t == "PLAYER_DEATH":
        return "Died"
    if t == "PVP_KILL":
        victim = victims.get(r["target_guid"]) or f"character #{r['target_guid']}"
        return f"Killed {victim} in PvP (level {r['value1']})"
    if t == "LEVEL_CHANGE":
        return f"Level {r['value1']} → {r['value2']}"
    if t == "QUEST_COMPLETE":
        return f"Completed {quests.get(entry) or f'quest #{entry}'}"
    if t == "ACHIEVEMENT":
        return f"Earned achievement #{entry}"
    if t == "LOOT_ITEM":
        return f"Looted item #{entry} ×{r['value1']}"
    if t == "XP_GAIN":
        return f"Gained {r['value1']} XP"
    if t == "MONEY_CHANGE":
        delta = int(r["value1"] or 0)
        sign = "+" if delta >= 0 else "−"
        return f"Money {sign}{money(abs(delta))}"
    return t.replace("_", " ").capitalize()


def money(copper):
    gold, rest = divmod(int(copper), 10000)
    silver, cop = divmod(rest, 100)
    parts = [f"{gold}g"] if gold else []
    if silver or gold:
        parts.append(f"{silver}s")
    parts.append(f"{cop}c")
    return " ".join(parts)


# ---------------------------------------------------------------- names

def _names(sql, ids):
    """Best-effort id -> name lookup. Missing world grants degrade to ids.

    The world database is optional for this panel (see deploy/grants.sql), so a
    denied or missing table must not break a page - it just means numbers.
    """
    ids = sorted({int(i) for i in ids if i})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    try:
        rows = core.query(sql.format(ph=placeholders), tuple(ids))
    except (pymysql.err.ProgrammingError, pymysql.err.OperationalError):
        return {}
    return {int(r["id"]): r["name"] for r in rows}


def creature_names(entries):
    return _names(f"SELECT entry AS id, name FROM {WORLD_DB}.creature_template "
                  "WHERE entry IN ({ph})", entries)


def quest_names(ids):
    return _names(f"SELECT ID AS id, LogTitle AS name FROM {WORLD_DB}.quest_template "
                  "WHERE ID IN ({ph})", ids)


def character_names(guids):
    return _names(f"SELECT guid AS id, name FROM {CHAR_DB}.characters "
                  "WHERE guid IN ({ph})", guids)


# ---------------------------------------------------------------- live roster

ONLINE_PREFIX = "PLAYERSTATS_ONLINE_V1 "


def parse_online(raw):
    """Parse `playerstats online` output into a list of human-controlled players.

    The module prints one line: the literal prefix, a space, then compact JSON.
    It filters Playerbots itself from in-memory sessions, so this is the real
    count of humans - not an account-name heuristic, and not characters.online,
    which the module's author warns against using for current control.
    Returns None when the output is not in the expected shape.
    """
    for line in (raw or "").splitlines():
        line = line.strip()
        if line.startswith(ONLINE_PREFIX):
            try:
                data = json.loads(line[len(ONLINE_PREFIX):])
            except ValueError:
                return None
            players = data.get("players")
            return players if isinstance(players, list) else None
    return None
