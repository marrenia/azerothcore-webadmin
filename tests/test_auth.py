"""Unit tests for authentication, role derivation, session refresh and CSRF.

core.query / core.execute are monkeypatched so these run with no real MySQL
server: everything under test is our own logic, not PyMySQL or the schema.
"""
import hmac

import pytest
from werkzeug.exceptions import Forbidden

import core


class FakeQueries:
    """A tiny stand-in for core.query() driven by a list of canned results,
    consumed in call order. Keeps tests readable without a real cursor."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, sql, args=(), one=False):
        self.calls.append((sql, args, one))
        if not self._results:
            raise AssertionError(f"unexpected extra query: {sql}")
        return self._results.pop(0)


@pytest.fixture(autouse=True)
def _fixed_admin_cfg(monkeypatch):
    monkeypatch.setitem(core.ADMIN_CFG, "WEB_ADMIN_USER", "bootstrapadmin")
    monkeypatch.setitem(core.ADMIN_CFG, "WEB_ADMIN_PASS", "correct-horse-battery")


def test_authenticate_bootstrap_admin_ok():
    identity = core.authenticate("bootstrapadmin", "correct-horse-battery")
    assert identity == {"account_id": None, "role": core.ROLE_ADMIN, "username": "bootstrapadmin"}


def test_authenticate_bootstrap_admin_wrong_password(app, monkeypatch):
    # Username matches the bootstrap admin but the password doesn't, so this
    # falls through to the database-account path (a real "no such row" query).
    monkeypatch.setattr(core, "query", FakeQueries([None]))
    with app.app_context():
        identity = core.authenticate("bootstrapadmin", "wrong")
    assert identity is None


def test_authenticate_db_account_ok(monkeypatch):
    salt, verifier = core.srp6_make("PLAYERONE", "hunter2")
    fake = FakeQueries([
        {"id": 42, "username": "PLAYERONE", "salt": salt, "verifier": verifier},  # account lookup
        {"locked": 0},   # account_login_blocked -> locked check
        None,             # ban_state -> no active ban
        {"gmlevel": 1},  # account_role
    ])
    monkeypatch.setattr(core, "query", fake)
    identity = core.authenticate("playerone", "hunter2")
    assert identity == {"account_id": 42, "role": core.ROLE_MODERATOR, "username": "PLAYERONE"}


def test_authenticate_db_account_wrong_password(monkeypatch):
    salt, verifier = core.srp6_make("PLAYERONE", "hunter2")
    fake = FakeQueries([
        {"id": 42, "username": "PLAYERONE", "salt": salt, "verifier": verifier},
    ])
    monkeypatch.setattr(core, "query", fake)
    identity = core.authenticate("playerone", "WRONG")
    assert identity is None


def test_authenticate_db_account_locked_rejected(monkeypatch):
    salt, verifier = core.srp6_make("PLAYERONE", "hunter2")
    fake = FakeQueries([
        {"id": 42, "username": "PLAYERONE", "salt": salt, "verifier": verifier},
        {"locked": 1},  # locked -> reject before ever checking bans/role
    ])
    monkeypatch.setattr(core, "query", fake)
    identity = core.authenticate("playerone", "hunter2")
    assert identity is None


def test_authenticate_db_account_banned_rejected(monkeypatch):
    salt, verifier = core.srp6_make("PLAYERONE", "hunter2")
    fake = FakeQueries([
        {"id": 42, "username": "PLAYERONE", "salt": salt, "verifier": verifier},
        {"locked": 0},
        {"bandate": 1, "unbandate": 1, "banreason": "x", "bannedby": "y"},  # active ban
    ])
    monkeypatch.setattr(core, "query", fake)
    identity = core.authenticate("playerone", "hunter2")
    assert identity is None


def test_authenticate_unknown_account(monkeypatch):
    fake = FakeQueries([None])
    monkeypatch.setattr(core, "query", fake)
    identity = core.authenticate("nosuchuser", "whatever")
    assert identity is None


def test_authenticate_rejects_overlong_or_invalid_username(monkeypatch):
    fake = FakeQueries([])
    monkeypatch.setattr(core, "query", fake)
    assert core.authenticate("has spaces", "x") is None
    assert core.authenticate("x" * 100, "x") is None
    assert not fake.calls  # never even reached the database


@pytest.mark.parametrize("row,expected", [
    (None, core.ROLE_PLAYER),
    ({"gmlevel": 0}, core.ROLE_PLAYER),
    ({"gmlevel": 1}, core.ROLE_MODERATOR),
    ({"gmlevel": 2}, core.ROLE_GAMEMASTER),
    ({"gmlevel": 3}, core.ROLE_ADMIN),
    ({"gmlevel": 99}, core.ROLE_ADMIN),   # clamped, not trusted verbatim
    ({"gmlevel": -5}, core.ROLE_PLAYER),  # clamped
])
def test_account_role_mapping(monkeypatch, row, expected):
    monkeypatch.setattr(core, "query", FakeQueries([row]))
    assert core.account_role(7) == expected


def test_require_role_unauthenticated_redirects(app):
    view = core.require_role(core.ROLE_ADMIN)(lambda: "secret")
    with app.test_request_context("/anything"):
        resp = view()
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


def test_require_role_authenticated_insufficient_role_is_403(app, monkeypatch):
    monkeypatch.setattr(core, "_session_still_valid", lambda: True)
    view = core.require_role(core.ROLE_ADMIN)(lambda: "secret")
    with app.test_request_context("/anything"):
        from flask import session
        session["authed"] = True
        session["role"] = core.ROLE_PLAYER
        with pytest.raises(Forbidden):
            view()


def test_require_role_authenticated_sufficient_role_passes(app, monkeypatch):
    monkeypatch.setattr(core, "_session_still_valid", lambda: True)
    view = core.require_role(core.ROLE_PLAYER, core.ROLE_ADMIN)(lambda: "secret")
    with app.test_request_context("/anything"):
        from flask import session
        session["authed"] = True
        session["role"] = core.ROLE_PLAYER
        assert view() == "secret"


def test_require_role_kills_session_when_no_longer_valid(app, monkeypatch):
    monkeypatch.setattr(core, "_session_still_valid", lambda: False)
    view = core.require_role(core.ROLE_ADMIN)(lambda: "secret")
    with app.test_request_context("/anything"):
        from flask import session
        session["authed"] = True
        session["role"] = core.ROLE_ADMIN
        resp = view()
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert "authed" not in session


def test_session_refresh_rejects_now_locked_account(app, monkeypatch):
    monkeypatch.setattr(core, "account_login_blocked", lambda account_id: True)
    with app.test_request_context("/anything"):
        from flask import session
        session["account_id"] = 5
        session["_last_seen"] = None
        assert core._session_still_valid() is False


def test_session_refresh_ok_and_refreshes_role_for_active_account(app, monkeypatch):
    monkeypatch.setattr(core, "account_login_blocked", lambda account_id: False)
    monkeypatch.setattr(core, "account_role", lambda account_id: core.ROLE_GAMEMASTER)
    with app.test_request_context("/anything"):
        from flask import session
        session["account_id"] = 5
        session["role"] = core.ROLE_PLAYER
        assert core._session_still_valid() is True
        assert session["role"] == core.ROLE_GAMEMASTER  # picked up a promotion mid-session


def test_session_refresh_bootstrap_admin_has_no_account_to_revoke(app):
    with app.test_request_context("/anything"):
        from flask import session
        session["account_id"] = None
        assert core._session_still_valid() is True


def test_check_csrf_rejects_missing_token(app):
    with app.test_request_context("/anything", method="POST", data={}):
        from flask import session
        session["csrf"] = "expected-token"
        with pytest.raises(Exception) as exc_info:
            core.check_csrf()
        assert getattr(exc_info.value, "code", None) == 400


def test_check_csrf_rejects_wrong_token(app):
    with app.test_request_context("/anything", method="POST", data={"_csrf": "wrong"}):
        from flask import session
        session["csrf"] = "expected-token"
        with pytest.raises(Exception) as exc_info:
            core.check_csrf()
        assert getattr(exc_info.value, "code", None) == 400


def test_check_csrf_accepts_matching_token(app):
    with app.test_request_context("/anything", method="POST", data={"_csrf": "tok"}):
        from flask import session
        session["csrf"] = "tok"
        core.check_csrf()  # must not raise


def test_password_change_uses_constant_time_compare(monkeypatch):
    # hmac.compare_digest is what stands between this panel and a timing
    # attack on the SRP6 verifier; assert it is actually the function used.
    calls = []
    real_compare = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(core.hmac, "compare_digest", spy)
    salt, verifier = core.srp6_make("X", "pw")
    fake = FakeQueries([
        {"id": 1, "username": "X", "salt": salt, "verifier": verifier},
        {"locked": 0},
        None,
        {"gmlevel": 0},
    ])
    monkeypatch.setattr(core, "query", fake)
    core.authenticate("X", "pw")
    assert calls, "hmac.compare_digest was never used to check the verifier"
