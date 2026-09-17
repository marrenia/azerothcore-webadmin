"""Static WotLK lookup tables for presentation (races, classes, maps, zones)."""

RACES = {
    1: ("Human", "Alliance"), 2: ("Orc", "Horde"), 3: ("Dwarf", "Alliance"),
    4: ("Night Elf", "Alliance"), 5: ("Undead", "Horde"), 6: ("Tauren", "Horde"),
    7: ("Gnome", "Alliance"), 8: ("Troll", "Horde"), 10: ("Blood Elf", "Horde"),
    11: ("Draenei", "Alliance"),
}

# Name plus the canonical Blizzard class colour.
CLASSES = {
    1: ("Warrior", "#C79C6E"), 2: ("Paladin", "#F58CBA"), 3: ("Hunter", "#ABD473"),
    4: ("Rogue", "#FFF569"), 5: ("Priest", "#FFFFFF"), 6: ("Death Knight", "#C41F3B"),
    7: ("Shaman", "#0070DE"), 8: ("Mage", "#69CCF0"), 9: ("Warlock", "#9482C9"),
    11: ("Druid", "#FF7D0A"),
}

MAPS = {
    0: "Eastern Kingdoms", 1: "Kalimdor", 30: "Alterac Valley", 33: "Shadowfang Keep",
    34: "Stockade", 36: "Deadmines", 43: "Wailing Caverns", 47: "Razorfen Kraul",
    48: "Blackfathom Deeps", 70: "Uldaman", 90: "Gnomeregan", 109: "Sunken Temple",
    129: "Razorfen Downs", 189: "Scarlet Monastery", 209: "Zul'Farrak",
    229: "Blackrock Spire", 230: "Blackrock Depths", 249: "Onyxia's Lair",
    269: "Black Morass", 289: "Scholomance", 309: "Zul'Gurub", 329: "Stratholme",
    349: "Maraudon", 369: "Deeprun Tram", 389: "Ragefire Chasm", 409: "Molten Core",
    429: "Dire Maul", 449: "Alliance PvP Barracks", 450: "Horde PvP Barracks",
    469: "Blackwing Lair", 489: "Warsong Gulch", 509: "Ruins of Ahn'Qiraj",
    529: "Arathi Basin", 530: "Outland", 531: "Temple of Ahn'Qiraj",
    532: "Karazhan", 533: "Naxxramas", 534: "The Battle for Mount Hyjal",
    540: "Shattered Halls", 542: "Blood Furnace", 543: "Hellfire Ramparts",
    544: "Magtheridon's Lair", 545: "Steamvault", 546: "Underbog", 547: "Slave Pens",
    548: "Serpentshrine Cavern", 550: "Tempest Keep", 552: "Arcatraz",
    553: "Botanica", 554: "Mechanar", 555: "Shadow Labyrinth",
    556: "Sethekk Halls", 557: "Mana-Tombs", 558: "Auchenai Crypts",
    559: "Nagrand Arena", 560: "Old Hillsbrad", 562: "Blade's Edge Arena",
    564: "Black Temple", 565: "Gruul's Lair", 566: "Eye of the Storm",
    568: "Zul'Aman", 571: "Northrend", 572: "Ruins of Lordaeron",
    574: "Utgarde Keep", 575: "Utgarde Pinnacle", 576: "The Nexus",
    578: "The Oculus", 580: "Sunwell Plateau", 585: "Magisters' Terrace",
    595: "Culling of Stratholme", 598: "Sunwell Fix", 599: "Halls of Stone",
    600: "Drak'Tharon Keep", 601: "Azjol-Nerub", 602: "Halls of Lightning",
    603: "Ulduar", 604: "Gundrak", 605: "Slave Pens (Heroic)",
    607: "Strand of the Ancients", 608: "Violet Hold", 609: "Death Knight Start",
    615: "Obsidian Sanctum", 616: "Eye of Eternity", 617: "Dalaran Sewers",
    618: "Ring of Valor", 619: "Ahn'kahet", 624: "Vault of Archavon",
    628: "Isle of Conquest", 631: "Icecrown Citadel", 632: "Forge of Souls",
    649: "Trial of the Crusader", 650: "Trial of the Champion",
    658: "Pit of Saron", 668: "Halls of Reflection", 724: "Ruby Sanctum",
}

ZONES = {
    1: "Dun Morogh", 3: "Badlands", 4: "Blasted Lands", 8: "Swamp of Sorrows",
    10: "Duskwood", 11: "Wetlands", 12: "Elwynn Forest", 14: "Durotar",
    15: "Dustwallow Marsh", 16: "Azshara", 17: "The Barrens", 28: "Western Plaguelands",
    33: "Stranglethorn Vale", 36: "Alterac Mountains", 38: "Loch Modan",
    40: "Westfall", 41: "Deadwind Pass", 44: "Redridge Mountains",
    45: "Arathi Highlands", 46: "Burning Steppes", 47: "The Hinterlands",
    51: "Searing Gorge", 65: "Dragonblight", 66: "Zul'Drak", 67: "The Storm Peaks",
    85: "Tirisfal Glades", 130: "Silverpine Forest", 139: "Eastern Plaguelands",
    141: "Teldrassil", 148: "Darkshore", 210: "Icecrown", 215: "Mulgore",
    267: "Hillsbrad Foothills", 331: "Ashenvale", 357: "Feralas",
    361: "Felwood", 394: "Grizzly Hills", 400: "Thousand Needles",
    405: "Desolace", 406: "Stonetalon Mountains", 440: "Tanaris",
    490: "Un'Goro Crater", 493: "Moonglade", 495: "Howling Fjord",
    618: "Winterspring", 1377: "Silithus", 1497: "Undercity",
    1519: "Stormwind City", 1537: "Ironforge", 1637: "Orgrimmar",
    1638: "Thunder Bluff", 1657: "Darnassus", 2017: "Stratholme",
    3430: "Eversong Woods", 3433: "Ghostlands", 3483: "Hellfire Peninsula",
    3487: "Silvermoon City", 3518: "Nagrand", 3519: "Terokkar Forest",
    3520: "Shadowmoon Valley", 3521: "Zangarmarsh", 3522: "Blade's Edge Mountains",
    3523: "Netherstorm", 3524: "Azuremyst Isle", 3525: "Bloodmyst Isle",
    3557: "The Exodar", 3703: "Shattrath City", 4080: "Isle of Quel'Danas",
    4197: "Wintergrasp", 4395: "Dalaran", 4742: "Hrothgar's Landing",
}


def race_name(rid):
    return RACES.get(rid, (f"Race {rid}", "Neutral"))[0]


def race_faction(rid):
    return RACES.get(rid, (None, "Neutral"))[1]


def class_name(cid):
    return CLASSES.get(cid, (f"Class {cid}", "#cccccc"))[0]


def class_color(cid):
    return CLASSES.get(cid, (None, "#cccccc"))[1]


def map_name(mid):
    return MAPS.get(mid, f"Map {mid}")


def zone_name(zid):
    return ZONES.get(zid, f"Zone {zid}")
