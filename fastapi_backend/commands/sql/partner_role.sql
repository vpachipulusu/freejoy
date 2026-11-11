-- The restricted database role of the partner API (ADR-013, FREEJ-57).
--
-- The partner surface connects as this role (PARTNER_DATABASE_URL) and never as the owner. It gets exactly the
-- grants below and nothing else in the public schema: no users, admins, organisations, memberships, subscribers,
-- billing, TOTP or login tables, and no DDL in public. Later partner stories extend THIS file (one grant per
-- column the partner uses, written next to the Core table definitions in app/partner/keys.py and tables.py).
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
    'partner_api_keys.label.SELECT', 'partner_api_keys.revoked_at.SELECT',
    'partner_api_keys.last_used_at.SELECT', 'partner_api_keys.last_used_at.UPDATE', 'merchants.id.SELECT',
    'merchants.name.SELECT', 'merchants.website.SELECT', 'merchants.active.SELECT', 'offers.id.SELECT',
    'offers.title.SELECT', 'offers.slug.SELECT', 'offers.summary.SELECT', 'offers.description.SELECT',
    'offers.category_id.SELECT', 'offers.merchant_id.SELECT', 'offers.offer_type.SELECT',
    'offers.status.SELECT', 'offers.featured.SELECT', 'offers.image_tint.SELECT', 'offers.image_label.SELECT',
    'offers.image_alt.SELECT', 'offers.source_url.SELECT', 'offers.postage_kind.SELECT',
    'offers.postage_amount_minor.SELECT', 'offers.postage_currency.SELECT', 'offers.requires_card.SELECT',
    'offers.subscription_terms.SELECT', 'offers.eligibility.SELECT', 'offers.availability.SELECT',
    'offers.steps.SELECT', 'offers.start_at.SELECT', 'offers.end_at.SELECT', 'offers.last_verified_at.SELECT',
    'offers.freshness.SELECT', 'offers.publisher_notes.SELECT', 'offers.id.INSERT', 'offers.title.INSERT',
    'offers.slug.INSERT', 'offers.summary.INSERT', 'offers.description.INSERT', 'offers.category_id.INSERT',
    'offers.merchant_id.INSERT', 'offers.offer_type.INSERT', 'offers.image_tint.INSERT',
    'offers.image_label.INSERT', 'offers.image_alt.INSERT', 'offers.postage_kind.INSERT',
    'offers.postage_amount_minor.INSERT', 'offers.postage_currency.INSERT', 'offers.requires_card.INSERT',
    'offers.subscription_terms.INSERT', 'offers.eligibility.INSERT', 'offers.availability.INSERT',
    'offers.steps.INSERT', 'offers.start_at.INSERT', 'offers.end_at.INSERT', 'offers.title.UPDATE',
    'offers.summary.UPDATE', 'offers.description.UPDATE', 'offers.category_id.UPDATE',
    'offers.offer_type.UPDATE', 'offers.postage_kind.UPDATE', 'offers.postage_amount_minor.UPDATE',
    'offers.postage_currency.UPDATE', 'offers.requires_card.UPDATE', 'offers.subscription_terms.UPDATE',
    'offers.eligibility.UPDATE', 'offers.availability.UPDATE', 'offers.steps.UPDATE', 'offers.start_at.UPDATE',
    'offers.end_at.UPDATE', 'offers.updated_at.UPDATE', 'affiliate_links.id.SELECT',
    'affiliate_links.offer_id.SELECT', 'affiliate_links.provider.SELECT',
    'affiliate_links.destination_url.SELECT', 'affiliate_links.tracking_id.SELECT',
    'affiliate_links.active.SELECT', 'affiliate_links.id.INSERT', 'affiliate_links.offer_id.INSERT',
    'affiliate_links.destination_url.INSERT', 'affiliate_links.destination_url.UPDATE',
    'affiliate_links.updated_at.UPDATE', 'offer_imports.id.SELECT', 'offer_imports.provider.SELECT',
    'offer_imports.external_id.SELECT', 'offer_imports.offer_id.SELECT', 'offer_imports.source_hash.SELECT',
    'offer_imports.applied_source_hash.SELECT', 'offer_imports.applied_content_hash.SELECT',
    'offer_imports.applied_at.SELECT', 'offer_imports.source_changed_at.SELECT', 'offer_imports.id.INSERT',
    'offer_imports.provider.INSERT', 'offer_imports.external_id.INSERT', 'offer_imports.offer_id.INSERT',
    'offer_imports.source_hash.INSERT', 'offer_imports.applied_source_hash.INSERT',
    'offer_imports.applied_content_hash.INSERT', 'offer_imports.applied_at.INSERT',
    'offer_imports.source_hash.UPDATE',
    'offer_imports.applied_source_hash.UPDATE', 'offer_imports.applied_content_hash.UPDATE',
    'offer_imports.applied_at.UPDATE', 'offer_imports.source_changed_at.UPDATE',
    'offer_imports.updated_at.UPDATE', 'categories.id.SELECT', 'categories.slug.SELECT',
    'categories.enabled.SELECT', 'partner_submissions.id.SELECT', 'partner_submissions.merchant_id.SELECT',
    'partner_submissions.ref.SELECT', 'partner_submissions.offer_id.SELECT',
    'partner_submissions.content_hash.SELECT', 'partner_submissions.pending_payload.SELECT',
    'partner_submissions.pending_hash.SELECT', 'partner_submissions.pending_at.SELECT',
    'partner_submissions.submitted_at.SELECT', 'partner_submissions.key_id.SELECT',
    'partner_submissions.first_published_at.SELECT', 'partner_submissions.created_at.SELECT',
    'partner_submissions.id.INSERT', 'partner_submissions.merchant_id.INSERT',
    'partner_submissions.ref.INSERT', 'partner_submissions.offer_id.INSERT',
    'partner_submissions.content_hash.INSERT', 'partner_submissions.key_id.INSERT',
    'partner_submissions.content_hash.UPDATE', 'partner_submissions.pending_payload.UPDATE',
    'partner_submissions.pending_hash.UPDATE', 'partner_submissions.pending_at.UPDATE',
    'partner_submissions.submitted_at.UPDATE', 'partner_submissions.key_id.UPDATE',
    'partner_submissions.updated_at.UPDATE', 'audit_logs.id.INSERT', 'audit_logs.action.INSERT',
    'audit_logs.resource.INSERT', 'audit_logs.resource_id.INSERT', 'audit_logs.metadata.INSERT'];
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

  -- The grants below lean on the triggers of migration 0008: the defaults the role cannot set and the guards that
  -- give its column grants a row condition. Refuse to grant anything unless every one is the expected trigger: on
  -- the expected table, running the expected function, for the expected event (tgtype), and enabled. (A trigger
  -- whose function a later migration replaced is the migration's business; a disabled one, or a same-named
  -- trigger elsewhere, is not accepted.) Run alembic upgrade head first. The same list is in
  -- app/core/database.py (RESTRICTED_ROLE_QUERY), which makes the live connection check it too.
  IF (SELECT count(*) FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
       WHERE NOT t.tgisinternal AND t.tgenabled IN ('O', 'A') AND n.nspname = 'public'
         AND (c.relname, t.tgname, p.proname, t.tgtype::int) IN (
         ('offers', 'trg_offers_insert_defaults', 'freejoy_offer_insert_defaults', 7),
         ('offers', 'trg_offers_update_guard', 'freejoy_offer_update_guard', 19),
         ('offers', 'trg_offers_note_first_publish', 'freejoy_note_first_publish', 17),
         ('affiliate_links', 'trg_affiliate_links_insert_defaults', 'freejoy_link_insert_defaults', 7),
         ('affiliate_links', 'trg_affiliate_links_write_guard', 'freejoy_link_write_guard', 23),
         ('offer_imports', 'trg_offer_imports_write_guard', 'freejoy_import_write_guard', 23),
         ('partner_submissions', 'trg_partner_submissions_insert_guard', 'freejoy_submission_insert_guard', 7),
         ('audit_logs', 'trg_audit_logs_insert_guard', 'freejoy_audit_insert_guard', 7))) <> 8 THEN
    RAISE EXCEPTION 'the partner guard triggers of migration 0008 are missing, changed or disabled; run alembic upgrade head first';
  END IF;

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', database_name, role_name);
  EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', role_name);

  -- Keys: look one up by id (the columns the code reads, no created_by) and move last_used_at. Nothing else
  -- on the table: a key is never created, changed or revoked by the partner surface. UPDATE (last_used_at)
  -- lets the role set that one column to any value on any key; it cannot touch the hash or the revoke time.
  EXECUTE format(
    'GRANT SELECT (id, merchant_id, key_hash, label, revoked_at, last_used_at) ON partner_api_keys TO %I', role_name);
  EXECUTE format('GRANT UPDATE (last_used_at) ON partner_api_keys TO %I', role_name);
  -- The merchant a key belongs to: the columns the code reads (website judges the host of a link), read only. The
  -- role can never write merchants, so the API cannot change what a direct link may point at.
  EXECUTE format('GRANT SELECT (id, name, website, active) ON merchants TO %I', role_name);

  -- Partner submissions (FREEJ-58). A submission becomes a DRAFT offer; nothing here can publish it.
  --
  -- offers: INSERT on the content columns only. status is not granted, so the column default (DRAFT) supplies it, and
  -- featured (default false), last_verified_at (null), freshness and publisher_notes (null) stay at their defaults.
  -- commercial_basis (NOT NULL, no default) is not granted either: the trigger of migration 0008 gives a writer that
  -- may not set it the placeholder EDITORIAL, and the editor sets the real basis at review. UPDATE: the same content
  -- columns minus the identity (slug, merchant_id) and the editor-owned columns; end_at is how a retailer ends an offer.
  -- Never status, last_verified_at, featured, verified_* or commercial_basis. SELECT is the content plus what the
  -- fingerprint of an untouched draft reads.
  EXECUTE format(
    'GRANT SELECT (id, title, slug, summary, description, category_id, merchant_id, offer_type, status, featured, image_tint, image_label, image_alt, source_url, postage_kind, postage_amount_minor, postage_currency, requires_card, subscription_terms, eligibility, availability, steps, start_at, end_at, last_verified_at, freshness, publisher_notes) ON offers TO %I', role_name);
  EXECUTE format(
    'GRANT INSERT (id, title, slug, summary, description, category_id, merchant_id, offer_type, image_tint, image_label, image_alt, postage_kind, postage_amount_minor, postage_currency, requires_card, subscription_terms, eligibility, availability, steps, start_at, end_at) ON offers TO %I', role_name);
  EXECUTE format(
    'GRANT UPDATE (title, summary, description, category_id, offer_type, postage_kind, postage_amount_minor, postage_currency, requires_card, subscription_terms, eligibility, availability, steps, start_at, end_at, updated_at) ON offers TO %I', role_name);

  -- affiliate_links: provider is not granted (the trigger sets direct), nor are active (default true) or tracking_id;
  -- the only update is a new destination.
  EXECUTE format(
    'GRANT SELECT (id, offer_id, provider, destination_url, tracking_id, active) ON affiliate_links TO %I', role_name);
  EXECUTE format(
    'GRANT INSERT (id, offer_id, destination_url) ON affiliate_links TO %I', role_name);
  EXECUTE format(
    'GRANT UPDATE (destination_url, updated_at) ON affiliate_links TO %I', role_name);

  -- offer_imports: the provenance row (provider partner) that puts a submission in the editors' review queue and holds
  -- the hashes that tell the retailer changed it from an editor changed it. No DELETE.
  EXECUTE format(
    'GRANT SELECT (id, provider, external_id, offer_id, source_hash, applied_source_hash, applied_content_hash, applied_at, source_changed_at) ON offer_imports TO %I', role_name);
  EXECUTE format(
    'GRANT INSERT (id, provider, external_id, offer_id, source_hash, applied_source_hash, applied_content_hash, applied_at) ON offer_imports TO %I', role_name);
  EXECUTE format(
    'GRANT UPDATE (source_hash, applied_source_hash, applied_content_hash, applied_at, source_changed_at, updated_at) ON offer_imports TO %I', role_name);

  -- categories: read, to check that the category a retailer names is one an editor enabled.
  EXECUTE format(
    'GRANT SELECT (id, slug, enabled) ON categories TO %I', role_name);

  -- partner_submissions: the table of this API. merchant_id, ref and offer_id are written once and never updated;
  -- first_published_at is set by a trigger and only read.
  EXECUTE format(
    'GRANT SELECT (id, merchant_id, ref, offer_id, content_hash, pending_payload, pending_hash, pending_at, submitted_at, key_id, first_published_at, created_at) ON partner_submissions TO %I', role_name);
  EXECUTE format(
    'GRANT INSERT (id, merchant_id, ref, offer_id, content_hash, key_id) ON partner_submissions TO %I', role_name);
  EXECUTE format(
    'GRANT UPDATE (content_hash, pending_payload, pending_hash, pending_at, submitted_at, key_id, updated_at) ON partner_submissions TO %I', role_name);

  -- audit_logs: append only. The role may insert the five columns an audit row needs and nothing else: no SELECT (it
  -- cannot read the trail), no UPDATE or DELETE, and not user_id or organization_id. The trigger of migration 0008
  -- (freejoy_audit_insert_guard) then lets a writer that may not name a user append only rows whose action is
  -- partner.<area>.<verb>, so the role cannot forge an admin. row.
  EXECUTE format(
    'GRANT INSERT (id, action, resource, resource_id, metadata) ON audit_logs TO %I', role_name);

  -- Verify from the catalog, counting what reaches the role through PUBLIC. Raise on anything not listed.
  IF has_parameter_privilege(role_name, 'session_replication_role', 'SET') THEN
    RAISE EXCEPTION 'verification failed: the role can switch triggers off for its session';
  END IF;
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
