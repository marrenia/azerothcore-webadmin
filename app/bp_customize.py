"""Guarded AzerothCore world-data editor.

Writes are intentionally limited to a reviewed set of columns. All values are
bound parameters; identifiers are constants. Destructive actions require an
exact typed token. Audit records go to the systemd journal through Flask.
"""
import math
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from pymysql.err import MySQLError

from core import WORLD_DB, execute, login_required, query, server_online, soap
from soap import SoapError

bp = Blueprint("customize", __name__, url_prefix="/customize")

NPC_TYPES = {0:"None",1:"Beast",2:"Dragonkin",3:"Demon",4:"Elemental",5:"Giant",6:"Undead",7:"Humanoid",8:"Critter",9:"Mechanical",10:"Not specified",11:"Totem",12:"Non-combat pet",13:"Gas cloud"}
NPC_RANKS = {0:"Normal",1:"Elite",2:"Rare elite",3:"World boss",4:"Rare"}
GO_TYPES = {0:"Door",1:"Button",2:"Quest giver",3:"Chest",4:"Binder",5:"Generic",6:"Trap",7:"Chair",8:"Spell focus",9:"Text",10:"Goober",11:"Transport",12:"Area damage",13:"Camera",14:"Map object",15:"MO transport",16:"Duel arbiter",17:"Fishing node",18:"Ritual",19:"Mailbox",20:"Auction house",22:"Spellcaster",23:"Meeting stone",24:"Flag stand",25:"Fishing hole",26:"Flag drop",27:"Mini game",28:"Lottery kiosk",29:"Capture point",30:"Aura generator",31:"Dungeon difficulty",32:"Barber chair",33:"Destructible building",34:"Guild bank",35:"Trapdoor"}


def _int(name, minimum=0, maximum=4294967295, default=None):
    raw = request.form.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(name, minimum=-100000.0, maximum=100000.0, default=None):
    raw = request.form.get(name, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _text(name, maximum, required=False):
    value = (request.form.get(name) or "").strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} must be at most {maximum} characters")
    return value


def _confirm(expected):
    if (request.form.get("confirm") or "").strip() != expected:
        raise ValueError(f"type {expected} exactly to confirm")


def _audit(action, object_type, object_id, detail=""):
    current_app.logger.warning("WORLD_EDIT action=%s type=%s id=%s remote=%s detail=%s",
                               action, object_type, object_id,
                               request.remote_addr or "unknown", detail[:500])


def _done(message, endpoint="customize.index", **values):
    flash(message, "ok")
    return redirect(url_for(endpoint, **values))


def _fail(exc, endpoint="customize.index", **values):
    if isinstance(exc, ValueError):
        current_app.logger.warning("WORLD_EDIT rejected remote=%s reason=%s", request.remote_addr or "unknown", exc)
    else:
        current_app.logger.exception("world editor operation failed")
    flash(f"World edit failed: {exc}", "error")
    return redirect(url_for(endpoint, **values))


@bp.route("")
@login_required
def index():
    section = request.args.get("section", "creatures")
    q = (request.args.get("q") or "").strip()[:100]
    rows = []
    try:
        if q:
            like = f"%{q}%"
            if section == "creatures":
                if q.isdigit():
                    rows = query(f"SELECT entry,name,subname,minlevel,maxlevel,type,`rank` AS npc_rank,npcflag FROM {WORLD_DB}.creature_template WHERE entry=%s OR name LIKE %s ORDER BY entry LIMIT 100", (int(q), like))
                else:
                    rows = query(f"SELECT entry,name,subname,minlevel,maxlevel,type,`rank` AS npc_rank,npcflag FROM {WORLD_DB}.creature_template WHERE name LIKE %s OR subname LIKE %s ORDER BY entry LIMIT 100", (like, like))
            elif section == "spawns":
                if q.isdigit():
                    rows = query(f"SELECT c.guid,c.id,t.name,c.map,c.zoneId,c.position_x,c.position_y,c.position_z,c.spawntimesecs FROM {WORLD_DB}.creature c JOIN {WORLD_DB}.creature_template t ON t.entry=c.id WHERE c.guid=%s OR c.id=%s ORDER BY c.guid LIMIT 100", (int(q), int(q)))
            elif section == "vendors":
                if q.isdigit():
                    rows = query(f"SELECT v.entry,t.name,COUNT(*) items FROM {WORLD_DB}.npc_vendor v LEFT JOIN {WORLD_DB}.creature_template t ON t.entry=v.entry WHERE v.entry=%s GROUP BY v.entry,t.name", (int(q),))
                else:
                    rows = query(f"SELECT v.entry,t.name,COUNT(*) items FROM {WORLD_DB}.npc_vendor v JOIN {WORLD_DB}.creature_template t ON t.entry=v.entry WHERE t.name LIKE %s GROUP BY v.entry,t.name ORDER BY t.name LIMIT 100", (like,))
            elif section == "objects":
                if q.isdigit():
                    rows = query(f"SELECT entry,name,type,displayId,size FROM {WORLD_DB}.gameobject_template WHERE entry=%s OR name LIKE %s ORDER BY entry LIMIT 100", (int(q), like))
                else:
                    rows = query(f"SELECT entry,name,type,displayId,size FROM {WORLD_DB}.gameobject_template WHERE name LIKE %s ORDER BY entry LIMIT 100", (like,))
            elif section == "teleports":
                rows = query(f"SELECT id,name,map,position_x,position_y,position_z,orientation FROM {WORLD_DB}.game_tele WHERE name LIKE %s OR id=%s ORDER BY name LIMIT 100", (like, int(q) if q.isdigit() else 0))
        counts = {
            "creatures": query(f"SELECT COUNT(*) n FROM {WORLD_DB}.creature_template", one=True)["n"],
            "spawns": query(f"SELECT COUNT(*) n FROM {WORLD_DB}.creature", one=True)["n"],
            "vendors": query(f"SELECT COUNT(DISTINCT entry) n FROM {WORLD_DB}.npc_vendor", one=True)["n"],
            "objects": query(f"SELECT COUNT(*) n FROM {WORLD_DB}.gameobject_template", one=True)["n"],
            "teleports": query(f"SELECT COUNT(*) n FROM {WORLD_DB}.game_tele", one=True)["n"],
        }
    except MySQLError as exc:
        flash(f"World database read failed: {exc}", "error")
        counts = {}
    return render_template("customize/index.html", section=section, q=q, rows=rows,
                           counts=counts, npc_types=NPC_TYPES, go_types=GO_TYPES,
                           nav="customize")


@bp.route("/creature/<int:entry>")
@login_required
def creature(entry):
    npc = query(f"SELECT * FROM {WORLD_DB}.creature_template WHERE entry=%s", (entry,), one=True)
    if not npc:
        flash("Creature template not found.", "error")
        return redirect(url_for("customize.index"))
    spawns = query(f"SELECT guid,map,zoneId,areaId,position_x,position_y,position_z,orientation,spawntimesecs,wander_distance,MovementType,phaseMask,spawnMask,Comment FROM {WORLD_DB}.creature WHERE id=%s ORDER BY guid LIMIT 500", (entry,))
    vendor = query(f"SELECT v.slot,v.item,i.name item_name,v.maxcount,v.incrtime,v.ExtendedCost FROM {WORLD_DB}.npc_vendor v LEFT JOIN {WORLD_DB}.item_template i ON i.entry=v.item WHERE v.entry=%s ORDER BY v.slot,v.item", (entry,))
    return render_template("customize/creature.html", npc=npc, spawns=spawns,
                           vendor=vendor, npc_types=NPC_TYPES, npc_ranks=NPC_RANKS,
                           nav="customize", world_up=server_online())


@bp.route("/creature/<int:entry>/update", methods=["POST"])
@login_required
def creature_update(entry):
    try:
        values = (_text("name",100,True), _text("subname",100),
                  _int("minlevel",1,255), _int("maxlevel",1,255),
                  _int("type",0,13), _int("rank",0,4), _int("faction",0,65535),
                  _int("npcflag"), _float("speed_walk",0.05,20), _float("speed_run",0.05,20),
                  _float("HealthModifier",0.01,100000), _float("DamageModifier",0.01,100000),
                  _int("unit_flags"), _int("flags_extra"), _text("AIName",64), _text("ScriptName",64), entry)
        if values[2] > values[3]:
            raise ValueError("minlevel cannot exceed maxlevel")
        affected = execute(f"UPDATE {WORLD_DB}.creature_template SET name=%s,subname=%s,minlevel=%s,maxlevel=%s,type=%s,`rank`=%s,faction=%s,npcflag=%s,speed_walk=%s,speed_run=%s,HealthModifier=%s,DamageModifier=%s,unit_flags=%s,flags_extra=%s,AIName=%s,ScriptName=%s WHERE entry=%s", values)
        if affected != 1:
            raise ValueError("creature template was not found or did not change")
        _audit("update", "creature_template", entry, f"name={values[0]}")
        if request.form.get("reload") == "1":
            try:
                soap.command("reload creature_template", timeout=120)
            except SoapError as exc:
                flash(f"Saved, but live reload failed: {exc}", "error")
                return redirect(url_for("customize.creature", entry=entry))
        return _done("Creature template saved." + (" Live template cache reloaded." if request.form.get("reload") == "1" else ""), "customize.creature", entry=entry)
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.creature", entry=entry)


@bp.route("/creature/<int:entry>/clone", methods=["POST"])
@login_required
def creature_clone(entry):
    try:
        new_entry = _int("new_entry",1)
        _confirm(f"CLONE {new_entry}")
        exists = query(f"SELECT entry FROM {WORLD_DB}.creature_template WHERE entry=%s", (new_entry,), one=True)
        if exists:
            raise ValueError("the destination entry already exists")
        affected = execute(f"INSERT INTO {WORLD_DB}.creature_template SELECT %s,difficulty_entry_1,difficulty_entry_2,difficulty_entry_3,KillCredit1,KillCredit2,name,subname,IconName,gossip_menu_id,minlevel,maxlevel,exp,faction,npcflag,speed_walk,speed_run,speed_swim,speed_flight,detection_range,rank,dmgschool,DamageModifier,BaseAttackTime,RangeAttackTime,BaseVariance,RangeVariance,unit_class,unit_flags,unit_flags2,dynamicflags,family,type,type_flags,lootid,pickpocketloot,skinloot,PetSpellDataId,VehicleId,mingold,maxgold,AIName,MovementType,HoverHeight,HealthModifier,ManaModifier,ArmorModifier,ExperienceModifier,RacialLeader,movementId,RegenHealth,CreatureImmunitiesId,flags_extra,ScriptName,VerifiedBuild FROM {WORLD_DB}.creature_template WHERE entry=%s", (new_entry, entry))
        if affected != 1:
            raise ValueError("source template not found")
        _audit("clone", "creature_template", new_entry, f"source={entry}")
        return _done("Creature type cloned. Review every field before spawning it.", "customize.creature", entry=new_entry)
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.creature", entry=entry)


@bp.route("/creature/<int:entry>/spawn", methods=["POST"])
@login_required
def spawn_add(entry):
    try:
        guid = query(f"SELECT COALESCE(MAX(guid),0)+1 n FROM {WORLD_DB}.creature", one=True)["n"]
        values = (guid, entry, _int("map",0,65535), _int("zoneId",0,65535), _int("areaId",0,65535),
                  _int("spawnMask",1,255,1), _int("phaseMask",1,4294967295,1),
                  _float("position_x"), _float("position_y"), _float("position_z"), _float("orientation",-1000,1000),
                  _int("spawntimesecs",1,604800,120), _float("wander_distance",0,10000,0),
                  _int("MovementType",0,2,0), _text("Comment",65535))
        execute(f"INSERT INTO {WORLD_DB}.creature (guid,id,map,zoneId,areaId,spawnMask,phaseMask,position_x,position_y,position_z,orientation,spawntimesecs,wander_distance,MovementType,Comment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", values)
        _audit("create", "creature_spawn", guid, f"entry={entry} map={values[2]}")
        return _done(f"Spawn {guid} created in the database. It appears after a world/map restart.", "customize.creature", entry=entry)
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.creature", entry=entry)


@bp.route("/spawn/<int:guid>/update", methods=["POST"])
@login_required
def spawn_update(guid):
    row = query(f"SELECT id FROM {WORLD_DB}.creature WHERE guid=%s", (guid,), one=True)
    if not row:
        flash("Spawn not found.", "error")
        return redirect(url_for("customize.index", section="spawns"))
    entry = row["id"]
    try:
        values = (_int("map",0,65535), _int("zoneId",0,65535), _int("areaId",0,65535),
                  _int("spawnMask",1,255), _int("phaseMask",1), _float("position_x"), _float("position_y"),
                  _float("position_z"), _float("orientation",-1000,1000), _int("spawntimesecs",1,604800),
                  _float("wander_distance",0,10000), _int("MovementType",0,2), _text("Comment",65535), guid)
        execute(f"UPDATE {WORLD_DB}.creature SET map=%s,zoneId=%s,areaId=%s,spawnMask=%s,phaseMask=%s,position_x=%s,position_y=%s,position_z=%s,orientation=%s,spawntimesecs=%s,wander_distance=%s,MovementType=%s,Comment=%s WHERE guid=%s", values)
        _audit("update", "creature_spawn", guid, f"entry={entry}")
        return _done("Spawn location saved. It takes effect after a world/map restart.", "customize.creature", entry=entry)
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.creature", entry=entry)


@bp.route("/spawn/<int:guid>/delete", methods=["POST"])
@login_required
def spawn_delete(guid):
    row = query(f"SELECT id FROM {WORLD_DB}.creature WHERE guid=%s", (guid,), one=True)
    if not row:
        flash("Spawn not found.", "error")
        return redirect(url_for("customize.index", section="spawns"))
    entry = row["id"]
    try:
        _confirm(f"DELETE {guid}")
        execute(f"DELETE FROM {WORLD_DB}.creature WHERE guid=%s", (guid,))
        _audit("delete", "creature_spawn", guid, f"entry={entry}")
        return _done("Spawn deleted from the database. A world/map restart clears any live copy.", "customize.creature", entry=entry)
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.creature", entry=entry)


@bp.route("/creature/<int:entry>/vendor", methods=["POST"])
@login_required
def vendor_save(entry):
    try:
        item = _int("item",1)
        values = (entry, _int("slot",-32768,32767,0), item, _int("maxcount"), _int("incrtime"), _int("ExtendedCost"), None)
        if not query(f"SELECT entry FROM {WORLD_DB}.item_template WHERE entry=%s", (item,), one=True):
            raise ValueError("item template does not exist")
        execute(f"INSERT INTO {WORLD_DB}.npc_vendor (entry,slot,item,maxcount,incrtime,ExtendedCost,VerifiedBuild) VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE slot=VALUES(slot),maxcount=VALUES(maxcount),incrtime=VALUES(incrtime)", values)
        _audit("upsert", "npc_vendor", entry, f"item={item}")
        try:
            soap.command("reload npc_vendor", timeout=120)
        except SoapError as exc:
            flash(f"Vendor saved, but live reload failed: {exc}", "error")
            return redirect(url_for("customize.creature", entry=entry))
        return _done("Vendor item saved and vendor cache reloaded.", "customize.creature", entry=entry)
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.creature", entry=entry)


@bp.route("/creature/<int:entry>/vendor/delete", methods=["POST"])
@login_required
def vendor_delete(entry):
    try:
        item = _int("item",1); cost = _int("ExtendedCost")
        _confirm(f"DELETE {item}")
        execute(f"DELETE FROM {WORLD_DB}.npc_vendor WHERE entry=%s AND item=%s AND ExtendedCost=%s", (entry,item,cost))
        _audit("delete", "npc_vendor", entry, f"item={item} cost={cost}")
        try:
            soap.command("reload npc_vendor", timeout=120)
        except SoapError as exc:
            flash(f"Vendor item deleted, but live reload failed: {exc}", "error")
            return redirect(url_for("customize.creature", entry=entry))
        return _done("Vendor item deleted and vendor cache reloaded.", "customize.creature", entry=entry)
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.creature", entry=entry)


@bp.route("/teleport", methods=["POST"])
@login_required
def teleport_save():
    try:
        tele_id = _int("id",1)
        values = (_float("position_x"),_float("position_y"),_float("position_z"),_float("orientation",-1000,1000),_int("map",0,65535),_text("name",100,True),tele_id)
        if query(f"SELECT id FROM {WORLD_DB}.game_tele WHERE id=%s", (tele_id,), one=True):
            execute(f"UPDATE {WORLD_DB}.game_tele SET position_x=%s,position_y=%s,position_z=%s,orientation=%s,map=%s,name=%s WHERE id=%s", values)
            action="update"
        else:
            execute(f"INSERT INTO {WORLD_DB}.game_tele (position_x,position_y,position_z,orientation,map,name,id) VALUES (%s,%s,%s,%s,%s,%s,%s)", values)
            action="create"
        _audit(action,"game_tele",tele_id,f"name={values[5]}")
        try: soap.command("reload game_tele", timeout=120)
        except SoapError as exc:
            flash(f"Teleport saved, but live reload failed: {exc}", "error")
            return redirect(url_for("customize.index",section="teleports",q=str(tele_id)))
        return _done("Teleport saved and reloaded.", "customize.index", section="teleports", q=str(tele_id))
    except (ValueError, MySQLError) as exc:
        return _fail(exc, "customize.index", section="teleports")


@bp.route("/teleport/<int:tele_id>/delete", methods=["POST"])
@login_required
def teleport_delete(tele_id):
    try:
        _confirm(f"DELETE {tele_id}")
        execute(f"DELETE FROM {WORLD_DB}.game_tele WHERE id=%s",(tele_id,))
        _audit("delete","game_tele",tele_id)
        try: soap.command("reload game_tele", timeout=120)
        except SoapError as exc:
            flash(f"Teleport deleted, but live reload failed: {exc}", "error")
            return redirect(url_for("customize.index",section="teleports"))
        return _done("Teleport deleted and cache reloaded.","customize.index",section="teleports")
    except (ValueError, MySQLError) as exc:
        return _fail(exc,"customize.index",section="teleports")


@bp.route("/object/<int:entry>")
@login_required
def gameobject(entry):
    obj=query(f"SELECT * FROM {WORLD_DB}.gameobject_template WHERE entry=%s",(entry,),one=True)
    if not obj:
        flash("Gameobject template not found.","error")
        return redirect(url_for("customize.index",section="objects"))
    spawns=query(f"SELECT guid,map,zoneId,areaId,position_x,position_y,position_z,orientation,spawntimesecs,state,phaseMask,spawnMask,Comment FROM {WORLD_DB}.gameobject WHERE id=%s ORDER BY guid LIMIT 500",(entry,))
    return render_template("customize/object.html",obj=obj,spawns=spawns,go_types=GO_TYPES,nav="customize")

@bp.route("/object/<int:entry>/update",methods=["POST"])
@login_required
def gameobject_update(entry):
    try:
        values=(_int("type",0,35),_int("displayId"),_text("name",100,True),_float("size",0.01,1000),_text("AIName",64),_text("ScriptName",64),entry)
        affected=execute(f"UPDATE {WORLD_DB}.gameobject_template SET type=%s,displayId=%s,name=%s,size=%s,AIName=%s,ScriptName=%s WHERE entry=%s",values)
        if affected!=1: raise ValueError("gameobject template was not found or did not change")
        _audit("update","gameobject_template",entry,f"name={values[2]}")
        if request.form.get("reload")=="1":
            try: soap.command("reload gameobject_template",timeout=120)
            except SoapError as exc:
                flash(f"Saved, but live reload failed: {exc}","error")
                return redirect(url_for("customize.gameobject",entry=entry))
        return _done("Gameobject template saved.","customize.gameobject",entry=entry)
    except (ValueError,MySQLError) as exc:
        return _fail(exc,"customize.gameobject",entry=entry)


@bp.route("/object/<int:entry>/spawn",methods=["POST"])
@login_required
def gameobject_spawn_add(entry):
    try:
        guid=query(f"SELECT COALESCE(MAX(guid),0)+1 n FROM {WORLD_DB}.gameobject",one=True)["n"]
        values=(guid,entry,_int("map",0,65535),_int("zoneId",0,65535),_int("areaId",0,65535),_int("spawnMask",1,255,1),_int("phaseMask",1,4294967295,1),_float("position_x"),_float("position_y"),_float("position_z"),_float("orientation",-1000,1000),_int("spawntimesecs",0,604800,0),_int("state",0,255,0),_text("Comment",65535))
        execute(f"INSERT INTO {WORLD_DB}.gameobject (guid,id,map,zoneId,areaId,spawnMask,phaseMask,position_x,position_y,position_z,orientation,spawntimesecs,state,Comment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",values)
        _audit("create","gameobject_spawn",guid,f"entry={entry}")
        return _done(f"Gameobject spawn {guid} created. It appears after a world/map restart.","customize.gameobject",entry=entry)
    except (ValueError,MySQLError) as exc:
        return _fail(exc,"customize.gameobject",entry=entry)


@bp.route("/object-spawn/<int:guid>/delete",methods=["POST"])
@login_required
def gameobject_spawn_delete(guid):
    row=query(f"SELECT id FROM {WORLD_DB}.gameobject WHERE guid=%s",(guid,),one=True)
    if not row:
        flash("Gameobject spawn not found.","error")
        return redirect(url_for("customize.index",section="objects"))
    entry=row["id"]
    try:
        _confirm(f"DELETE {guid}")
        execute(f"DELETE FROM {WORLD_DB}.gameobject WHERE guid=%s",(guid,))
        _audit("delete","gameobject_spawn",guid,f"entry={entry}")
        return _done("Gameobject spawn deleted. A world/map restart clears any live copy.","customize.gameobject",entry=entry)
    except (ValueError,MySQLError) as exc:
        return _fail(exc,"customize.gameobject",entry=entry)
