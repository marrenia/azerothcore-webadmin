"""Leaderboards and server statistics, fed by mod-player-statistics.

Leaderboards are open to every signed-in role: they show character names and
counts that players can already see in game. The live roster of which humans
are online, with their locations, is staff-only.
"""
from flask import Blueprint, current_app, render_template, request

import stats
from core import ALL_ROLES, STAFF_ROLES, current_role, require_role, soap
from soap import SoapError

bp = Blueprint("stats", __name__)


def humans_online():
    """(players, error) from the module's `playerstats online` console command.

    The command filters Playerbots itself from in-memory sessions, so this is
    the real set of human-controlled characters.
    """
    try:
        raw = soap.command("playerstats online", timeout=6)
    except SoapError:
        return None, "The worldserver is not responding."
    players = stats.parse_online(raw)
    if players is None:
        return None, "Unexpected reply from `playerstats online` - is mod-player-statistics loaded?"
    return players, None


def character_stats(guid):
    """(summary, error) for embedding in a character page. Never raises.

    The character pages carry GM tools, so a statistics failure must cost only
    the statistics section, never the whole page.
    """
    try:
        return stats.character_summary(guid), None
    except stats.StatsUnavailable:
        return None, stats_missing_message()
    except Exception:  # noqa: BLE001 - deliberately broad, see docstring
        current_app.logger.exception("statistics failed for character %s", guid)
        return None, "Statistics could not be loaded right now."


def stats_missing_message():
    return ("Statistics are unavailable: the mod-player-statistics tables are missing "
            "or this panel's database account cannot read them. See docs/INSTALL.md.")


@bp.route("/stats")
@require_role(*ALL_ROLES)
def index():
    population, window = stats.normalise_filters(
        request.args.get("pop"), request.args.get("window"))
    staff = current_role() in STAFF_ROLES
    online, online_error = humans_online()
    try:
        data = {
            "boards": stats.leaderboards(population, window),
            "totals": stats.totals(population, window),
            "daily": stats.daily_activity(population),
            "coverage": stats.coverage(),
        }
        error = None
    except stats.StatsUnavailable:
        data, error = None, stats_missing_message()
    return render_template(
        "stats.html", data=data, error=error,
        population=population, window=window,
        populations=stats.POPULATIONS, windows=stats.WINDOWS,
        board_defs=stats.BOARDS,
        online=online, online_error=online_error, staff=staff,
        nav="stats",
    )
