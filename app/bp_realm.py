"""Realm list editor.

The realmlist row is what the 3.3.5 client is handed after it authenticates:
`address`/`port` is where the client is told to connect for the world server.
Getting it wrong does not produce an error at the authserver - the client
simply cannot reach the world and sits at "Logging in to game server".

The authserver re-reads this table on a timer (RealmsStateUpdateDelay in
authserver.conf, 20s by default), so edits apply without a restart.
"""
import re
import socket

from flask import Blueprint, flash, redirect, render_template, request, url_for

from core import AUTH_DB, execute, login_required, query

bp = Blueprint("realm", __name__)

# realmlist.icon - what the client shows next to the realm name.
REALM_ICONS = {0: "Normal", 1: "PvP", 6: "RP", 8: "RP PvP"}

# realmlist.flag bits. OFFLINE hides the realm from the list entirely, which is
# the usual way to take a realm out of rotation without deleting the row.
REALM_FLAGS = {
    0x01: "Version mismatch",
    0x02: "Offline (hidden from the realm list)",
    0x04: "Specify build",
    0x20: "Recommended",
    0x40: "New players",
    0x80: "Full",
}

# realmlist.timezone - the client's region grouping, not a clock offset.
REALM_TIMEZONES = {
    0: "Development", 1: "United States", 2: "Oceanic", 3: "Latin America",
    4: "Tournament", 5: "Korea", 6: "Tournament", 7: "English",
    8: "German", 9: "French", 10: "Spanish", 11: "Russian",
    12: "Tournament", 13: "Taiwan", 14: "Tournament", 15: "China",
    16: "CN1", 17: "CN2", 18: "CN3", 19: "CN4", 20: "CN5",
    21: "CN6", 22: "CN7", 23: "CN8", 24: "Tournament", 25: "Test",
    26: "Tournament", 27: "QA", 28: "CN9", 29: "Test2", 30: "CN10",
    31: "CTC", 32: "CNC", 33: "CN14", 34: "Test3", 35: "CN17",
}

SECURITY_LEVELS = {0: "Everyone", 1: "Moderator+", 2: "Game Master+", 3: "Administrator only"}

NAME_RE = re.compile(r"^[A-Za-z0-9 '\-]{1,32}$")
# Hostname or dotted-quad. The client resolves names, but a space or a scheme
# here is always a mistake (people paste "http://host" surprisingly often).
ADDRESS_RE = re.compile(r"^[A-Za-z0-9._\-]{1,255}$")
MASK_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _local_addresses():
    """Addresses of this host, to help pick the right one in the form.

    Purely advisory: it is how we surface "you probably meant the tailnet IP"
    without hardcoding anyone's network.
    """
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if not addr.startswith("127.") and addr != "::1":
                found.add(addr)
    except OSError:
        pass
    # getaddrinfo misses interfaces that have no matching hostname record.
    for probe in ("100.100.100.100", "8.8.8.8"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 53))
            found.add(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    return sorted(found)


def _int_field(form, key, default, low, high):
    try:
        value = int(form.get(key, default))
    except (TypeError, ValueError):
        return default, f"{key} must be a whole number."
    if not low <= value <= high:
        return default, f"{key} must be between {low} and {high}."
    return value, None


def _validate(form, realm_id=None):
    """Return (values, errors) for a realm create/update form."""
    errors = []
    name = (form.get("name") or "").strip()
    address = (form.get("address") or "").strip()
    local_address = (form.get("localAddress") or "").strip() or address
    mask = (form.get("localSubnetMask") or "").strip() or "255.255.255.255"

    if not NAME_RE.match(name):
        errors.append("Realm name must be 1-32 characters: letters, numbers, "
                      "spaces, apostrophes or hyphens.")
    if not ADDRESS_RE.match(address):
        errors.append("Address must be a hostname or IP - no scheme, port or spaces.")
    if not ADDRESS_RE.match(local_address):
        errors.append("Local address must be a hostname or IP.")
    if not MASK_RE.match(mask):
        errors.append("Local subnet mask must be a dotted-quad such as 255.255.255.0.")

    port, err = _int_field(form, "port", 8085, 1, 65535)
    if err:
        errors.append(err)
    icon, err = _int_field(form, "icon", 0, 0, 255)
    if err:
        errors.append(err)
    elif icon not in REALM_ICONS:
        errors.append("Realm type must be Normal, PvP, RP or RP PvP.")
    timezone, err = _int_field(form, "timezone", 1, 0, 255)
    if err:
        errors.append(err)
    security, err = _int_field(form, "allowedSecurityLevel", 0, 0, 3)
    if err:
        errors.append(err)
    gamebuild, err = _int_field(form, "gamebuild", 12340, 0, 999999)
    if err:
        errors.append(err)

    flag = 0
    for bit in REALM_FLAGS:
        if form.get(f"flag_{bit}"):
            flag |= bit

    # idx_name is UNIQUE; catch it here so the user gets a sentence, not a 500.
    clash = query(
        f"SELECT id FROM {AUTH_DB}.realmlist WHERE name=%s"
        + (" AND id<>%s" if realm_id else ""),
        (name, realm_id) if realm_id else (name,), one=True,
    )
    if clash:
        errors.append(f"Another realm is already named {name!r}.")

    values = {
        "name": name, "address": address, "localAddress": local_address,
        "localSubnetMask": mask, "port": port, "icon": icon, "flag": flag,
        "timezone": timezone, "allowedSecurityLevel": security,
        "gamebuild": gamebuild,
    }
    return values, errors


@bp.route("/realms")
@login_required
def index():
    realms = query(f"SELECT * FROM {AUTH_DB}.realmlist ORDER BY id")
    # Jinja has no bitwise operator, so decode the flag bitmask here.
    for realm in realms:
        realm["flag_bits"] = [bit for bit in REALM_FLAGS if int(realm["flag"]) & bit]
    return render_template(
        "realms.html", realms=realms, icons=REALM_ICONS, flags=REALM_FLAGS,
        timezones=REALM_TIMEZONES, security=SECURITY_LEVELS,
        local_addresses=_local_addresses(), nav="realms",
    )


@bp.route("/realms/<int:realm_id>/save", methods=["POST"])
@login_required
def save(realm_id):
    realm = query(f"SELECT * FROM {AUTH_DB}.realmlist WHERE id=%s", (realm_id,), one=True)
    if not realm:
        flash("That realm no longer exists.", "error")
        return redirect(url_for("realm.index"))

    values, errors = _validate(request.form, realm_id=realm_id)
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("realm.index"))

    execute(
        f"""UPDATE {AUTH_DB}.realmlist SET name=%s, address=%s, localAddress=%s,
            localSubnetMask=%s, port=%s, icon=%s, flag=%s, timezone=%s,
            allowedSecurityLevel=%s, gamebuild=%s WHERE id=%s""",
        (values["name"], values["address"], values["localAddress"],
         values["localSubnetMask"], values["port"], values["icon"],
         values["flag"], values["timezone"], values["allowedSecurityLevel"],
         values["gamebuild"], realm_id),
    )

    note = ""
    if (values["address"], values["port"]) != (realm["address"], realm["port"]):
        note = (f" Clients must now use {values['address']}:{values['port']} - "
                "the authserver picks this up within ~20s, no restart needed.")
    flash(f"Realm {values['name']} updated.{note}", "ok")
    return redirect(url_for("realm.index"))


@bp.route("/realms/create", methods=["POST"])
@login_required
def create():
    values, errors = _validate(request.form)
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("realm.index"))

    execute(
        f"""INSERT INTO {AUTH_DB}.realmlist
            (name, address, localAddress, localSubnetMask, port, icon, flag,
             timezone, allowedSecurityLevel, population, gamebuild)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)""",
        (values["name"], values["address"], values["localAddress"],
         values["localSubnetMask"], values["port"], values["icon"],
         values["flag"], values["timezone"], values["allowedSecurityLevel"],
         values["gamebuild"]),
    )
    flash(
        f"Realm {values['name']} created. It will not accept logins until a "
        "worldserver runs with a matching RealmID in worldserver.conf.", "ok",
    )
    return redirect(url_for("realm.index"))


@bp.route("/realms/<int:realm_id>/delete", methods=["POST"])
@login_required
def delete(realm_id):
    realm = query(f"SELECT * FROM {AUTH_DB}.realmlist WHERE id=%s", (realm_id,), one=True)
    if not realm:
        flash("That realm no longer exists.", "error")
        return redirect(url_for("realm.index"))

    # An empty realmlist means every client gets an empty realm list and nobody
    # can play. Refuse rather than let the panel brick the login flow.
    total = query(f"SELECT COUNT(*) AS n FROM {AUTH_DB}.realmlist", one=True)["n"]
    if total <= 1:
        flash("Refusing to delete the only realm - clients would see an empty "
              "realm list and could not log in.", "error")
        return redirect(url_for("realm.index"))

    if (request.form.get("confirm") or "").strip() != realm["name"]:
        flash("Delete cancelled: type the realm name exactly to confirm.", "error")
        return redirect(url_for("realm.index"))

    execute(f"DELETE FROM {AUTH_DB}.realmlist WHERE id=%s", (realm_id,))
    flash(f"Realm {realm['name']} deleted. Characters in acore_characters are "
          "untouched; re-creating a realm with the same id restores access.", "ok")
    return redirect(url_for("realm.index"))
