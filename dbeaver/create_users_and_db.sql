-- One-off bootstrap for the kanji_trainer database on a shared Postgres
-- server. Run as the postgres superuser, substituting the ${...} placeholders
-- with the values from the deployment's .env:
--
--   docker exec -i postgres-core psql -U postgres < create_users_and_db.sql
--
-- Then run grant_privileges.sql against the new database.
--
-- Note: paste the passwords WITHOUT the surrounding quotes, even if the .env
-- writes them as DB_PASSWORD="…". Docker Compose strips those quotes before
-- the container sees the value, so a role created with them can never be
-- logged into.

-- Create Roles
CREATE ROLE kanji_trainer_owner
  WITH LOGIN
  PASSWORD '${DB_OWNER_PASSWORD}';

CREATE ROLE kanji_trainer_app
  WITH LOGIN
  PASSWORD '${DB_PASSWORD}';

-- Create Database
CREATE DATABASE kanji_trainer
  OWNER kanji_trainer_owner;

-- Allow app user to connect
GRANT CONNECT ON DATABASE kanji_trainer
  TO kanji_trainer_app;
