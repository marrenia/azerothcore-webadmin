# World Customizer

Targets the installed AzerothCore Playerbot schema at core revision `06234df3d5ab`.

## Safety model

- All user values are parameterized; SQL identifiers are fixed constants.
- Only reviewed columns are editable. Gameobject Data0–Data23 remain read-only.
- Destructive actions require exact `DELETE <id>` confirmation.
- Every write emits a `WORLD_EDIT` event to the `acore-webadmin` systemd journal.
- Spawn coordinates are database-backed and cannot use an in-game GM position. Spawn edits require a world/map restart to affect already-loaded maps.
- Templates, vendors, and teleports use allow-listed SOAP reload commands when requested.

## Required grants

Keep all existing grants and add only:

```sql
GRANT SELECT, UPDATE ON acore_world.creature_template TO 'acoreweb'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON acore_world.creature TO 'acoreweb'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON acore_world.npc_vendor TO 'acoreweb'@'localhost';
GRANT SELECT ON acore_world.item_template TO 'acoreweb'@'localhost';
GRANT SELECT, UPDATE ON acore_world.gameobject_template TO 'acoreweb'@'localhost';
GRANT SELECT, INSERT, DELETE ON acore_world.gameobject TO 'acoreweb'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON acore_world.game_tele TO 'acoreweb'@'localhost';
```

The creature-template clone uses `INSERT`; grant it only if cloning is enabled:

```sql
GRANT INSERT ON acore_world.creature_template TO 'acoreweb'@'localhost';
```

## Rollback

Restore the deployment backup, remove the grants listed above (without touching pre-existing grants), restart only `acore-webadmin.service`, and verify the previous route set.
