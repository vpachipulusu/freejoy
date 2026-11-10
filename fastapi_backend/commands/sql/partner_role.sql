-- The restricted database role of the partner API (ADR-013, FREEJ-57).
--
-- The partner surface connects as this role (PARTNER_DATABASE_URL) and never as the owner. It gets exactly the
-- grants below and nothing else: no users, admins, organisations, memberships, subscribers, billing, TOTP or login
-- tables, and no DDL. Later partner stories extend THIS file (one grant per table the partner needs).
--
-- Run it as the database OWNER (a role that may create roles and grant on these tables), once per database, after
-- `alembic upgrade head`, and again after any story that adds a grant here:
--
--   psql "$OWNER_DATABASE_URL" -v ON_ERROR_STOP=1 \
--        -v partner_role=freejoy_partner -v partner_password="$PARTNER_ROLE_PASSWORD" \
--        -f commands/sql/partner_role.sql
--
-- partner_role is optional (the default is freejoy_partner); partner_password is required the first time (and
-- resets the password when given again). Then set PARTNER_DATABASE_URL to the role's URL, ending ?sslmode=verify-full.
--
-- It is safe to run again: the role is created only if it does not exist, and the table privileges are rebuilt
-- from scratch, so the role holds what this file says and nothing left over from an older version. It never drops
-- anything. Roles are cluster-wide, so the CONNECT grant and every table grant here apply to the database the
-- script runs in, and to no other. Needs PostgreSQL 15 or newer (the public schema no longer lets everyone create
-- tables; the e2e test checks the role cannot).
--
-- Tests run this same file through `tests/partner_role_support.py`, which replaces the parameter block below with
-- the two `set_config` calls it stands for. Keep the markers.

-- parameters (begin)
\if :{?partner_role}
\else
  \set partner_role freejoy_partner
\endif
\if :{?partner_password}
\else
  \set partner_password ''
\endif
SELECT set_config('freejoy.partner_role', :'partner_role', false);
SELECT set_config('freejoy.partner_password', :'partner_password', false);
-- parameters (end)

DO $partner$
DECLARE
  role_name text := current_setting('freejoy.partner_role');
  role_password text := current_setting('freejoy.partner_password');
  database_name text := current_database();
BEGIN
  IF role_name !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'partner_role must be 1 to 63 characters of a-z, 0-9 and _, starting with a letter';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
    IF role_password = '' THEN
      RAISE EXCEPTION 'partner_password is required to create the role';
    END IF;
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', role_name, role_password);
  ELSIF role_password <> '' THEN
    EXECUTE format('ALTER ROLE %I PASSWORD %L', role_name, role_password);
  END IF;

  -- Whatever the role was created with, it holds no special attribute.
  EXECUTE format(
    'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',
    role_name);
  -- A runaway query from the internet-facing surface is cut off.
  EXECUTE format('ALTER ROLE %I SET statement_timeout = ''10s''', role_name);

  -- Start from nothing in this database, then grant exactly what is listed below.
  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', role_name);
  EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', role_name);
  EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', role_name);

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', database_name, role_name);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', role_name);

  -- Keys: look one up by id, and move last_used_at. Nothing else on the table (a key is never created,
  -- changed or revoked by the partner surface).
  EXECUTE format('GRANT SELECT ON partner_api_keys TO %I', role_name);
  EXECUTE format('GRANT UPDATE (last_used_at) ON partner_api_keys TO %I', role_name);
  -- The merchant a key belongs to (name, active): read only.
  EXECUTE format('GRANT SELECT ON merchants TO %I', role_name);
  -- The audit trail: append only (INSERT and nothing else, so no RETURNING and no reading it back).
  EXECUTE format('GRANT INSERT ON audit_logs TO %I', role_name);
END
$partner$;
