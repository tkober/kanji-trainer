-- Run against the kanji_trainer database (NOT postgres) after
-- create_users_and_db.sql:
--
--   docker exec -i postgres-core psql -U postgres -d kanji_trainer < grant_privileges.sql
--
-- The tables themselves are created by the backend on startup, connecting as
-- kanji_trainer_owner. The app role never runs DDL — its access to those
-- tables comes from the default privileges below, so the backend issues no
-- GRANT.

ALTER SCHEMA public OWNER TO kanji_trainer_owner;

GRANT USAGE ON SCHEMA public
  TO kanji_trainer_app;

ALTER DEFAULT PRIVILEGES
  FOR ROLE kanji_trainer_owner
  IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE
  ON TABLES
  TO kanji_trainer_app;

ALTER DEFAULT PRIVILEGES
  FOR ROLE kanji_trainer_owner
  IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE
  ON SEQUENCES
  TO kanji_trainer_app;
