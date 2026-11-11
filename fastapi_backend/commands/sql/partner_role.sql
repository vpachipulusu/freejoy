-- The restricted database role of the partner API (ADR-013, FREEJ-57).
--
-- The partner surface connects as this role (PARTNER_DATABASE_URL) and never as the owner. It gets exactly the
-- grants below and nothing else in the public schema: no users, admins, organisations, memberships, subscribers,
-- billing, TOTP or login tables, and no DDL in public. Later partner stories extend THIS file (one grant per
-- column the partner reads, written next to the Core table definition in app/partner/keys.py).
--
-- Run it as the database OWNER (any role that may create roles and grant on these tables; it does not need to
-- be a superuser), on the DIRECT (unpooled) owner URL, once per database after `alembic upgrade head`, and again
-- after any story that adds a grant here and after any migration that drops and recreates a granted table
-- (grants go with the table). Needs psql 15 or newer (\getenv) and PostgreSQL 15 or newer (the public schema no
-- longer lets everyone create tables). The password comes from the environment, never from the command line:
--
--   export PARTNER_ROLE_PASSWORD='<a long random password>'
--   psql "$OWNER_DIRECT_DATABASE_URL" -v ON_ERROR_STOP=1 -f commands/sql/partner_role.sql
--
-- Optional psql variables: -v partner_role=<name> (default freejoy_partner) and -v partner_connection_limit=<n>
-- (default 20). The password is required the first time and resets the password when given again.
--
-- What it does, in ONE transaction (it all happens or none of it does):
--   * creates the role if it is absent, with LOGIN and every special attribute off and a connection limit, stated
--     on CREATE ROLE (a managed Postgres does not let a non-superuser ALTER a role with those words in it);
--   * for a role that already exists it ALTERS NOTHING about attributes: it raises if the role holds any special
--     attribute, is a member of any role, owns a relation, or is the role running the script;
--   * sets statement_timeout, lock_timeout and idle_in_transaction_session_timeout on the role. These stop
--     accidents (a stuck query, a stuck transaction); they do not stop injected SQL, because the role can reset
--     them for its own session;
--   * revokes every privilege the role holds directly on tables, sequences, functions, the public schema and this
--     database, then grants exactly the lines below, then VERIFIES the result from the catalog and raises (rolling
--     everything back) if the role can do anything else in public. The verification counts privileges that reach
--     the role through PUBLIC.
--
-- What it does NOT remove, and the role keeps through PUBLIC: reading the catalogs (table and column names,
-- approximate row counts), temporary tables (so "no DDL" means no DDL in public), advisory locks, LISTEN/NOTIFY,
-- large objects, and CONNECT to other databases in the cluster. None of it reads protected data.
--
-- Tests run this same file through tests/partner_role_support.py, which replaces the marked psql-only blocks
-- with the transaction and the set_config calls they stand for. Keep the markers.

-- parameters (begin)
\if :{?partner_role}
\else
  \set partner_role freejoy_partner
\endif
\if :{?partner_connection_limit}
\else
  \set partner_connection_limit 20
\endif
\getenv partner_password PARTNER_ROLE_PASSWORD
\if :{?partner_password}
\else
  \set partner_password ''
\endif
BEGIN;
-- Transaction-local (true), and the result is consumed by \gset, so nothing is printed or left on a pooled session.
SELECT set_config('freejoy.partner_role', :'partner_role', true) AS discard_a,
       set_config('freejoy.partner_password', :'partner_password', true) AS discard_b,
       set_config('freejoy.partner_connection_limit', :'partner_connection_limit', true) AS discard_c
\gset freejoy_
\unset freejoy_discard_b
\unset partner_password
-- parameters (end)

DO $partner$
DECLARE
  role_name text := current_setting('freejoy.partner_role');
  role_password text := current_setting('freejoy.partner_password');
  connection_limit text := current_setting('freejoy.partner_connection_limit');
  database_name text := current_database();
  role_oid oid;
  problem text;
  -- Every column privilege the role may hold in public, as table.column.PRIVILEGE. Nothing else.
  expected text[] := ARRAY[
    'partner_api_keys.id.SELECT', 'partner_api_keys.merchant_id.SELECT', 'partner_api_keys.key_hash.SELECT',
    'partner_api_keys.label.SELECT', 'partner_api_keys.revoked_at.SELECT', 'partner_api_keys.last_used_at.SELECT',
    'partner_api_keys.last_used_at.UPDATE',
    'merchants.id.SELECT', 'merchants.name.SELECT', 'merchants.active.SELECT',
    'offers.id.SELECT', 'offers.merchant_id.SELECT', 'offers.title.SELECT', 'offers.slug.SELECT',
    'offers.status.SELECT',
    'click_events.offer_id.SELECT', 'click_events.occurred_at.SELECT', 'click_events.user_agent_class.SELECT'];
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('freejoy.partner_role'));

  IF role_name !~ '^[a-z][a-z0-9_]{0,62}$' THEN
    RAISE EXCEPTION 'partner_role must be 1 to 63 characters of a-z, 0-9 and _, starting with a letter';
  END IF;
  IF connection_limit !~ '^[1-9][0-9]{0,3}$' THEN
    RAISE EXCEPTION 'partner_connection_limit must be a whole number from 1 to 9999';
  END IF;
  IF role_name IN (current_user, session_user) THEN
    RAISE EXCEPTION 'partner_role must not be the role that runs this script';
  END IF;

  SELECT oid INTO role_oid FROM pg_roles WHERE rolname = role_name;
  IF role_oid IS NULL THEN
    IF role_password = '' THEN
      RAISE EXCEPTION 'PARTNER_ROLE_PASSWORD is required to create the role';
    END IF;
    BEGIN
      EXECUTE format(
        'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT '
        'CONNECTION LIMIT %s PASSWORD %L', role_name, connection_limit, role_password);
    EXCEPTION WHEN OTHERS THEN
      -- Never let the statement (it holds the password) reach an error message or the log.
      RAISE EXCEPTION 'could not create the role (SQLSTATE %)', SQLSTATE;
    END;
  ELSE
    IF EXISTS (SELECT 1 FROM pg_roles WHERE oid = role_oid
               AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
      RAISE EXCEPTION 'role % holds a special attribute; fix it by hand, this script will not', role_name;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_auth_members WHERE member = role_oid) THEN
      RAISE EXCEPTION 'role % is a member of another role; remove the membership first', role_name;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_class WHERE relowner = role_oid)
       OR EXISTS (SELECT 1 FROM pg_database WHERE datdba = role_oid) THEN
      RAISE EXCEPTION 'role % owns objects; it must be a plain login role', role_name;
    END IF;
    IF role_password <> '' THEN
      BEGIN
        EXECUTE format('ALTER ROLE %I PASSWORD %L', role_name, role_password);
      EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'could not set the role password (SQLSTATE %)', SQLSTATE;
      END;
    END IF;
    EXECUTE format('ALTER ROLE %I WITH LOGIN NOINHERIT CONNECTION LIMIT %s', role_name, connection_limit);
  END IF;

  EXECUTE format('ALTER ROLE %I SET statement_timeout = ''10s''', role_name);
  EXECUTE format('ALTER ROLE %I SET lock_timeout = ''2s''', role_name);
  EXECUTE format('ALTER ROLE %I SET idle_in_transaction_session_timeout = ''15s''', role_name);

  -- Start from nothing in this database, then grant exactly what is listed below.
  EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', role_name);
  EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', role_name);
  EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', role_name);
  EXECUTE format('REVOKE ALL ON SCHEMA public FROM %I', role_name);
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM %I', database_name, role_name);

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', database_name, role_name);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', role_name);

  -- Keys: look one up by id (the columns the code reads, no created_by) and move last_used_at. Nothing else
  -- on the table: a key is never created, changed or revoked by the partner surface. UPDATE (last_used_at)
  -- lets the role set that one column to any value on any key; it cannot touch the hash or the revoke time.
  EXECUTE format(
    'GRANT SELECT (id, merchant_id, key_hash, label, revoked_at, last_used_at) ON partner_api_keys TO %I', role_name);
  EXECUTE format('GRANT UPDATE (last_used_at) ON partner_api_keys TO %I', role_name);
  -- The merchant a key belongs to: the columns the code reads, read only.
  EXECUTE format('GRANT SELECT (id, name, active) ON merchants TO %I', role_name);
  -- Click counts (GET /partner/v1/clicks, FREEJ-59): the columns the query reads and no others. Not offers.source_url,
  -- summary, description or any commercial column; not click_events.referrer_host, affiliate_link_id or id. Read only.
  EXECUTE format('GRANT SELECT (id, merchant_id, title, slug, status) ON offers TO %I', role_name);
  EXECUTE format('GRANT SELECT (offer_id, occurred_at, user_agent_class) ON click_events TO %I', role_name);
  -- No grant on audit_logs: no partner code writes an audit row yet. The story that first does adds one,
  -- constrained (a policy or a trigger that fixes user_id and the action prefix), not the bare table.

  -- Verify from the catalog, counting what reaches the role through PUBLIC. Raise on anything not listed.
  IF has_schema_privilege(role_name, 'public', 'CREATE') THEN
    RAISE EXCEPTION 'verification failed: the role can create objects in public';
  END IF;
  IF has_database_privilege(role_name, database_name, 'CREATE') THEN
    RAISE EXCEPTION 'verification failed: the role can create schemas in this database';
  END IF;
  SELECT format('%s.%s', c.relname, p.priv) INTO problem
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']) AS p(priv)
   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
     AND has_table_privilege(role_name, c.oid, p.priv)
   LIMIT 1;
  IF problem IS NOT NULL THEN
    RAISE EXCEPTION 'verification failed: the role holds table-level % (only column grants are allowed)', problem;
  END IF;
  SELECT format('%s.%s.%s', c.relname, a.attname, p.priv) INTO problem
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) AS p(priv)
   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
     AND has_column_privilege(role_name, c.oid, a.attnum, p.priv)
     AND format('%s.%s.%s', c.relname, a.attname, p.priv) <> ALL (expected)
   LIMIT 1;
  IF problem IS NOT NULL THEN
    RAISE EXCEPTION 'verification failed: the role holds %', problem;
  END IF;
  SELECT format('%s.%s', c.relname, p.priv) INTO problem
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    CROSS JOIN unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS p(priv)
   WHERE n.nspname = 'public' AND c.relkind = 'S'
     AND has_sequence_privilege(role_name, c.oid, p.priv)
   LIMIT 1;
  IF problem IS NOT NULL THEN
    RAISE EXCEPTION 'verification failed: the role holds % on a sequence', problem;
  END IF;
END
$partner$;

-- transaction end (begin)
COMMIT;
-- transaction end (end)
