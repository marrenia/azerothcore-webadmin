"""Tests for the mod-player-statistics integration (stats.py / bp_stats.py).

The load-bearing rule is cutover-aware death counting, taken from the module's
own documentation: PLAYER_DEATH only above the canonical_player_death_v1
cutoff, legacy PLAYER_KILLED_BY_CREATURE / PVP_KILL only at or below it, and
legacy PvP deaths attributed to the *victim* (target_*) columns.
"""
import datetime
import re

import pymysql
import pytest

import bp_stats
import core
import stats
from core import ROLE_ADMIN, ROLE_PLAYER


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(stats, "CACHE_SECONDS", 0)


# ---------------------------------------------------------------- filters / SQL shape

def test_where_filters_population_and_window():
    sql, args = stats._where("human", "7d")
    assert "e.actor_is_bot = 0" in sql and "INTERVAL %s DAY" in sql and args == [7]
    sql, args = stats._where("bot", "all")
    assert "e.actor_is_bot = 1" in sql and args == []
    sql, args = stats._where("all", "all")
    assert sql == "" and args == []


def test_unknown_filters_fall_back_to_defaults():
    assert stats.normalise_filters("nonsense", "99d") == (
        stats.DEFAULT_POPULATION, stats.DEFAULT_WINDOW)


def test_death_facts_fresh_install_counts_only_canonical_deaths():
    sql, args = stats._death_facts_sql(0, "all", "all")
    assert sql.count("SELECT") == 1
    assert "PLAYER_DEATH" in sql and "e.id > %s" in sql
    assert "PVP_KILL" not in sql and "PLAYER_KILLED_BY_CREATURE" not in sql
    assert args == [0]


def test_death_facts_upgraded_install_adds_legacy_branches_below_cutoff():
    sql, args = stats._death_facts_sql(500, "all", "all")
    branches = sql.split(" UNION ALL ")
    assert len(branches) == 3
    canonical, creature, pvp = branches
    assert "PLAYER_DEATH" in canonical and "e.id > %s" in canonical
    assert "PLAYER_KILLED_BY_CREATURE" in creature and "e.id <= %s" in creature
    # Legacy PvP deaths belong to the victim, stored in the target columns.
    assert "PVP_KILL" in pvp and "e.target_guid AS guid" in pvp and "e.id <= %s" in pvp
    assert args == [500, 500, 500]


def test_death_facts_filter_pvp_victims_by_target_bot_flag():
    sql, _ = stats._death_facts_sql(500, "human", "all")
    canonical, creature, pvp = sql.split(" UNION ALL ")
    assert "e.actor_is_bot = 0" in canonical and "e.actor_is_bot = 0" in creature
    assert "e.target_is_bot = 0" in pvp and "actor_is_bot" not in pvp.split("WHERE")[1]


def test_death_facts_missing_migration_row_counts_canonical_only():
    sql, args = stats._death_facts_sql(None, "all", "all")
    assert sql.count("SELECT") == 1 and args == [0]


def test_death_facts_scoped_to_one_character():
    sql, args = stats._death_facts_sql(500, "all", "7d", guid=42)
    canonical, creature, pvp = sql.split(" UNION ALL ")
    assert "e.actor_guid = %s" in canonical and "e.target_guid = %s" in pvp
    # Per branch: cutoff, window days, guid.
    assert args == [500, 7, 42, 500, 7, 42, 500, 7, 42]


# ---------------------------------------------------------------- parsing / text

def test_parse_online_reads_module_json():
    raw = ('PLAYERSTATS_ONLINE_V1 {"generatedAt":1,"players":[{"characterGuid":4242,'
           '"characterName":"Aeloria","level":4}]}\r\n')
    players = stats.parse_online(raw)
    assert players == [{"characterGuid": 4242, "characterName": "Aeloria", "level": 4}]


@pytest.mark.parametrize("raw", [
    "", "There is no such command.", "PLAYERSTATS_ONLINE_V1 {not json",
    'PLAYERSTATS_ONLINE_V1 {"players": "nope"}',
])
def test_parse_online_rejects_unexpected_output(raw):
    assert stats.parse_online(raw) is None


def test_parse_online_empty_roster_is_not_an_error():
    assert stats.parse_online('PLAYERSTATS_ONLINE_V1 {"players":[]}') == []


def _row(event_type, **kw):
    base = {"event_type": event_type, "target_entry": 0, "target_guid": 0,
            "value1": 0, "value2": 0}
    base.update(kw)
    return base


def test_describe_uses_names_and_falls_back_to_ids():
    assert stats.describe(_row("CREATURE_KILL", target_entry=299, value1=5),
                          {299: "Diseased Young Wolf"}, {}, {}) == \
        "Killed Diseased Young Wolf (level 5)"
    assert stats.describe(_row("PLAYER_KILLED_BY_CREATURE", target_entry=7, value1=9),
                          {}, {}, {}) == "Killed by creature #7 (level 9)"
    assert stats.describe(_row("QUEST_COMPLETE", target_entry=783), {}, {783: "A Threat Within"}, {}) == \
        "Completed A Threat Within"
    assert stats.describe(_row("PVP_KILL", target_guid=5, value1=10), {}, {}, {}) == \
        "Killed character #5 in PvP (level 10)"
    assert stats.describe(_row("LEVEL_CHANGE", value1=3, value2=4), {}, {}, {}) == "Level 3 → 4"
    assert stats.describe(_row("MONEY_CHANGE", value1=-12345), {}, {}, {}) == "Money −1g 23s 45c"


def test_money_formatting():
    assert stats.money(5) == "5c"
    assert stats.money(105) == "1s 5c"
    assert stats.money(10000) == "1g 0s 0c"


# ---------------------------------------------------------------- degradation

def _missing_table(*_a, **_kw):
    raise pymysql.err.ProgrammingError(1146, "Table 'acore_characters.mod_player_stats_events' doesn't exist")


def test_missing_module_raises_stats_unavailable(monkeypatch):
    monkeypatch.setattr(core, "query", _missing_table)
    with pytest.raises(stats.StatsUnavailable):
        stats.death_cutoff()


def test_names_degrade_to_empty_when_world_grant_missing(monkeypatch):
    def denied(*_a, **_kw):
        raise pymysql.err.OperationalError(1142, "SELECT command denied")
    monkeypatch.setattr(core, "query", denied)
    assert stats.creature_names([1, 2]) == {}


def test_character_stats_never_raises(app, monkeypatch):
    monkeypatch.setattr(core, "query", _missing_table)
    with app.app_context():
        summary, error = bp_stats.character_stats(1)
    assert summary is None and "mod-player-statistics" in error

    def boom(*_a, **_kw):
        raise RuntimeError("anything else")
    monkeypatch.setattr(core, "query", boom)
    with app.app_context():
        summary, error = bp_stats.character_stats(1)
    assert summary is None and error


# ---------------------------------------------------------------- routes

class FakeDB:
    """Answers the session-refresh queries plus every stats query shape."""

    def __init__(self, gmlevel=0, missing=False, no_nemesis=False):
        self.gmlevel = gmlevel
        self.missing = missing
        self.no_nemesis = no_nemesis

    def __call__(self, sql, args=(), one=False):
        result = self._answer(sql, args, one)
        # Mirror pymysql's DictCursor exactly: a list when there are rows, but
        # an empty *tuple* when there are none. Faking lists everywhere once hid
        # a real `list + ()` TypeError (a character with prey but no nemesis).
        if isinstance(result, list):
            return result if result else ()
        return result

    def _answer(self, sql, args=(), one=False):
        s = " ".join(sql.lower().split())
        if "select locked from" in s:
            return {"locked": 0}
        if "account_access" in s and "gmlevel" in s:
            return {"gmlevel": self.gmlevel}
        if "account_banned" in s:
            return None
        if "realmlist" in s:
            return {"name": "Test", "address": "127.0.0.1", "port": 8085}
        if "mod_player_stats" in s and self.missing:
            _missing_table()
        if "mod_player_stats_migrations" in s:
            return {"cutoff_event_id": 0}
        if "min(event_time)" in s:
            t = datetime.datetime(2026, 9, 1)
            return {"oldest": t, "oldest_human": t, "oldest_bot": t}
        # Most specific shapes first: the per-type totals query also contains
        # "count(*) as n", so it must be matched before the scalar-count rule.
        # Leaderboards alias the table as "e."; per-character queries do not.
        if re.search(r"group by (e\.)?event_type", s):
            return [{"event_type": "CREATURE_KILL", "n": 4}]
        if "group by date(" in s:
            return [{"day": datetime.date(2026, 9, 29), "kills": 4, "quests": 1, "deaths": 0}]
        if "coalesce(sum(greatest" in s:
            return {"n": 2}
        if "count(*) as n" in s or "count(distinct" in s:
            return {"n": 3}
        if "as id, name" in s:
            return [{"id": 299, "name": "Diseased Young Wolf"}]
        if self.no_nemesis and "player_killed_by_creature" in s and "group by" in s:
            return []
        if re.search(r"group by (e\.)?target_entry", s):
            return [{"entry": 299, "value": 4}]
        if "order by id desc" in s:
            return [{"id": 1, "event_time": datetime.datetime(2026, 9, 29, 18, 0),
                     "event_type": "CREATURE_KILL", "target_type": 2, "target_entry": 299,
                     "target_guid": 0, "target_is_bot": 0, "value1": 5, "value2": 0,
                     "map_id": 0, "zone_id": 12, "area_id": 0, "source": "direct"}]
        if "group by" in s:
            return [{"guid": 4242, "is_bot": 0, "name": "Aeloria", "race": 1,
                     "class": 2, "level": 4, "value": 4}]
        raise AssertionError(f"unexpected query in test: {sql}")


def _login(client, role, account_id=10):
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["account_id"] = account_id
        sess["role"] = role
        sess["username"] = "TESTER"
        sess["csrf"] = "tok"


def _soap_roster(monkeypatch):
    monkeypatch.setattr(core.soap, "command", lambda cmd, timeout=6: (
        'PLAYERSTATS_ONLINE_V1 {"players":[{"characterGuid":4242,"characterName":"Aeloria",'
        '"accountLogin":"SECRETLOGIN","raceId":1,"classId":2,"level":4,"zoneId":12,'
        '"location":"Elwynn Forest"}]}'))


def test_stats_requires_login(client):
    resp = client.get("/stats")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_player_sees_leaderboards_but_not_roster(client, monkeypatch):
    monkeypatch.setattr(core, "query", FakeDB(gmlevel=0))
    _soap_roster(monkeypatch)
    _login(client, ROLE_PLAYER)
    resp = client.get("/stats")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Leaderboards" in body and "Aeloria" in body and "Diseased Young Wolf" in body
    # The roster (account logins, locations) is staff-only; players get a count.
    assert "SECRETLOGIN" not in body and "Elwynn Forest" not in body
    # Players must not be handed links into the staff character tools.
    assert "/character/4242" not in body


def test_staff_sees_roster(client, monkeypatch):
    monkeypatch.setattr(core, "query", FakeDB(gmlevel=3))
    _soap_roster(monkeypatch)
    _login(client, ROLE_ADMIN)
    body = client.get("/stats").get_data(as_text=True)
    assert "SECRETLOGIN" in body and "Elwynn Forest" in body and "/character/4242" in body


def test_stats_page_explains_missing_module_instead_of_500(client, monkeypatch):
    monkeypatch.setattr(core, "query", FakeDB(gmlevel=3, missing=True))
    _soap_roster(monkeypatch)
    _login(client, ROLE_ADMIN)
    resp = client.get("/stats")
    assert resp.status_code == 200
    assert "mod-player-statistics tables are missing" in resp.get_data(as_text=True)


def test_bad_filter_values_do_not_error(client, monkeypatch):
    monkeypatch.setattr(core, "query", FakeDB(gmlevel=0))
    _soap_roster(monkeypatch)
    _login(client, ROLE_PLAYER)
    assert client.get("/stats?pop=%27%3Bdrop&window=forever").status_code == 200


def test_character_summary_with_prey_but_no_nemesis(monkeypatch):
    """Regression: pymysql returns rows as a list but no rows as (), so a
    character with kills but no creature deaths hit `list + ()`. Found on live
    data; verified this test fails against the pre-fix code."""
    monkeypatch.setattr(core, "query", FakeDB(no_nemesis=True))
    summary = stats.character_summary(4242)
    assert summary["prey"] and summary["prey"][0]["name"] == "Diseased Young Wolf"
    assert summary["nemesis"] == [] and isinstance(summary["timeline"], list)
