-- Least-privilege grants for the acore-webadmin database account.
--
-- Placeholders ({{DB_USER}}, {{AUTH_DB}}, {{CHAR_DB}}, {{WORLD_DB}}) are substituted
-- by deploy/install.sh. To apply by hand, replace them yourself.
--
-- Every grant is table-scoped. The account deliberately has:
--   * no access at all to acore_playerbots
--   * no DROP, CREATE, ALTER or GRANT anywhere
--   * read-only access to character data, except via the worldserver over SOAP
--
-- Auth: the panel manages accounts, access levels and bans directly.
GRANT SELECT, INSERT, UPDATE, DELETE ON `{{AUTH_DB}}`.`account`        TO '{{DB_USER}}'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON `{{AUTH_DB}}`.`account_access` TO '{{DB_USER}}'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON `{{AUTH_DB}}`.`account_banned` TO '{{DB_USER}}'@'localhost';
GRANT SELECT                          ON `{{AUTH_DB}}`.`ip_banned`     TO '{{DB_USER}}'@'localhost';

-- Realm list: the Realms tab edits these rows.
GRANT SELECT, INSERT, UPDATE, DELETE ON `{{AUTH_DB}}`.`realmlist`      TO '{{DB_USER}}'@'localhost';

-- Characters: read-only. Every mutation goes through the worldserver over SOAP so
-- the server's own cleanup and cache invalidation run.
GRANT SELECT ON `{{CHAR_DB}}`.`characters`       TO '{{DB_USER}}'@'localhost';
GRANT SELECT ON `{{CHAR_DB}}`.`character_banned` TO '{{DB_USER}}'@'localhost';
GRANT SELECT ON `{{CHAR_DB}}`.`guild`            TO '{{DB_USER}}'@'localhost';
GRANT SELECT ON `{{CHAR_DB}}`.`guild_member`     TO '{{DB_USER}}'@'localhost';
GRANT SELECT ON `{{CHAR_DB}}`.`gm_ticket`        TO '{{DB_USER}}'@'localhost';

-- World: only the tables the Customize tab edits. Omit this whole block if you do
-- not want world editing; the rest of the panel works without it.
GRANT SELECT, INSERT, UPDATE         ON `{{WORLD_DB}}`.`creature_template`   TO '{{DB_USER}}'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON `{{WORLD_DB}}`.`creature`            TO '{{DB_USER}}'@'localhost';
GRANT SELECT, UPDATE                 ON `{{WORLD_DB}}`.`gameobject_template` TO '{{DB_USER}}'@'localhost';
GRANT SELECT, INSERT, DELETE         ON `{{WORLD_DB}}`.`gameobject`          TO '{{DB_USER}}'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON `{{WORLD_DB}}`.`npc_vendor`          TO '{{DB_USER}}'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE ON `{{WORLD_DB}}`.`game_tele`           TO '{{DB_USER}}'@'localhost';
GRANT SELECT                         ON `{{WORLD_DB}}`.`item_template`       TO '{{DB_USER}}'@'localhost';
