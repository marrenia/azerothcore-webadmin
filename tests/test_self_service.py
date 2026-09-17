"""End-to-end-ish tests for PLAYER self-service account scoping.

Exercises the real Flask routes through the test client so the require_role
decorator, the session-refresh check, and the ownership check inside the view
are all actually running together - not just unit-tested in isolation.
core.query / bp_self.query are replaced with a small SQL-sniffing fake so no
real MySQL server is needed.
"""
import bp_self
import core


class RoutingFakeQuery:
    """Dispatches on a substring of the SQL text to canned results.

    This mirrors just enough of core.query()'s call shape ((sql, args, one))
    to drive both the require_role session-refresh path (which queries
    account.locked and account_access.gmlevel) and the bp_self route bodies
    (which query account / characters / gm_ticket) without a real database.
    """

    def __init__(self, characters_by_guid, owner_account_id=10):
        self.characters_by_guid = characters_by_guid
        self.owner_account_id = owner_account_id

    def __call__(self, sql, args=(), one=False):
        s = sql.lower()
        if "select locked from" in s:
            return {"locked": 0}
        if "account_access" in s and "gmlevel" in s:
            return {"gmlevel": 0}
        if "account_banned" in s:
            return None
        if "from" in s and ".account where id=" in s and "select id, username" in s:
            return {"id": self.owner_account_id, "username": "PLAYERONE",
                    "salt": b"s", "verifier": b"v"}
        if "select id, username, email" in s:
            return {"id": self.owner_account_id, "username": "PLAYERONE",
                    "email": "", "joindate": None, "last_login": None,
                    "last_ip": "1.2.3.4", "expansion": 2, "online": 0,
                    "locked": 0, "failed_logins": 0, "mutetime": 0}
        if "where guid=%s" in s and "select guid, name, race" in s:
            guid = args[0]
            return self.characters_by_guid.get(guid)
        if "gm_ticket" in s:
            return []
        raise AssertionError(f"unexpected query in test: {sql}")


def _login_session(client, account_id, role):
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["account_id"] = account_id
        sess["role"] = role
        sess["username"] = "PLAYERONE"
        sess["csrf"] = "tok"


def test_player_can_view_own_character(app, client, monkeypatch):
    fake = RoutingFakeQuery({
        99: {"guid": 99, "name": "Own", "race": 1, "class": 1, "gender": 0,
             "level": 10, "money": 0, "online": 0, "totaltime": 0, "map": 0,
             "zone": 0, "account": 10, "deleteDate": None},
    })
    monkeypatch.setattr(core, "query", fake)
    monkeypatch.setattr(bp_self, "query", fake)
    _login_session(client, account_id=10, role=core.ROLE_PLAYER)

    resp = client.get("/me/characters/99")
    assert resp.status_code == 200


def test_player_cannot_view_other_accounts_character(app, client, monkeypatch):
    fake = RoutingFakeQuery({
        100: {"guid": 100, "name": "NotYours", "race": 1, "class": 1, "gender": 0,
              "level": 10, "money": 0, "online": 0, "totaltime": 0, "map": 0,
              "zone": 0, "account": 999, "deleteDate": None},
    })
    monkeypatch.setattr(core, "query", fake)
    monkeypatch.setattr(bp_self, "query", fake)
    _login_session(client, account_id=10, role=core.ROLE_PLAYER)

    resp = client.get("/me/characters/100")
    assert resp.status_code == 403


def test_player_character_lookup_is_scoped_by_session_not_url(app, client, monkeypatch):
    """Changing the guid in the URL must never surface someone else's row,
    regardless of what account_id an attacker guesses - there is no
    account_id parameter in this URL at all, only the session's own."""
    fake = RoutingFakeQuery({42: None})  # character 42 does not belong to caller's set
    monkeypatch.setattr(core, "query", fake)
    monkeypatch.setattr(bp_self, "query", fake)
    _login_session(client, account_id=10, role=core.ROLE_PLAYER)

    resp = client.get("/me/characters/42")
    assert resp.status_code == 404


def test_bootstrap_admin_has_no_self_service_account(app, client, monkeypatch):
    fake = RoutingFakeQuery({})
    monkeypatch.setattr(core, "query", fake)
    monkeypatch.setattr(bp_self, "query", fake)
    with client.session_transaction() as sess:
        sess["authed"] = True
        sess["account_id"] = None
        sess["role"] = core.ROLE_ADMIN
        sess["username"] = "bootstrapadmin"

    resp = client.get("/me")
    assert resp.status_code == 403


def test_moderator_denied_admin_only_route(app, client, monkeypatch):
    fake = RoutingFakeQuery({})
    monkeypatch.setattr(core, "query", fake)
    _login_session(client, account_id=20, role=core.ROLE_MODERATOR)

    resp = client.get("/realms")
    assert resp.status_code == 403


def test_unauthenticated_request_redirects_to_login(client):
    resp = client.get("/me")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_post_without_csrf_token_is_rejected(app, client, monkeypatch):
    fake = RoutingFakeQuery({})
    monkeypatch.setattr(core, "query", fake)
    _login_session(client, account_id=10, role=core.ROLE_PLAYER)

    resp = client.post("/me/password", data={
        "current_password": "x", "new_password": "y",
    })  # deliberately no _csrf field
    assert resp.status_code == 400
