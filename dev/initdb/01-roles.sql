-- Runs once, when the local Postgres volume is first created.
--
-- It reproduces the deployment's role split (see deploy/kanji_trainer/
-- bootstrap/) so that a request path which quietly issues DDL fails here in
-- development, not the first time it reaches postgres-core.

CREATE ROLE kanji_trainer_owner WITH LOGIN PASSWORD 'kanji_trainer';
CREATE ROLE kanji_trainer_app WITH LOGIN PASSWORD 'kanji_trainer';

GRANT CONNECT ON DATABASE kanji_trainer TO kanji_trainer_app;

ALTER SCHEMA public OWNER TO kanji_trainer_owner;
GRANT USAGE ON SCHEMA public TO kanji_trainer_app;

-- The tables are created by the backend on startup as the owner role. The app
-- role's access to them comes from these default privileges, so the backend
-- itself never issues a GRANT.
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
