# Backend admin users API

Platform-admin user management in FastAPI. Authorisation is the `require_admin(...)`
dependency and the audit trail is `AdminAuditor` (see [backend-auth.md](backend-auth.md) and
[platform-models.md](platform-models.md)). Stats and admin-sent notifications are . The audit log API and covered actions: [backend-audit-logs.md](backend-audit-logs.md).

## Routes

All under `/v1/admin/users`, behind the internal service key and the `X-Session-Token` session.
Success bodies are `{"success": true, "data": ...}`; errors use the standard envelope.

| Route                     | Permission      | Notes                                                                                         |
| ------------------------- | --------------- | --------------------------------------------------------------------------------------------- |
| `GET ""`                  | `users.read`    | `limit` 1 to 100 (default 20), `offset`, `search` (email or name, literal match)              |
| `GET /{user_id}`          | `users.read`    | Profile, admin role and effective permissions. No 2FA secret                                  |
| `PATCH /{user_id}`        | `users.update`  | Profile fields only (name, bio, location, website, phone, avatar). Email is owned by Firebase |
| `DELETE /{user_id}`       | `users.delete`  | Deletes the Firebase account and the row. Refuses admins and organisation owners              |
| `PUT /{user_id}/admin`    | `admins.create` | Body `{role, permissions}`; creates or replaces the admin grant                               |
| `DELETE /{user_id}/admin` | `admins.delete` | Removes the admin grant                                                                       |
| `POST /{user_id}/ban`     | `users.update`  | Disables the Firebase user and revokes refresh tokens. Optional `{reason}`                    |
| `POST /{user_id}/unban`   | `users.update`  | Re-enables the Firebase user                                                                  |

Ban state lives in Firebase (a disabled user fails session verification), so no column was added
and the detail response has no `banned` field.

## Privilege rules

Enforced in `app/services/admin_users.py`, on top of `assert_can_grant`:

- Nobody changes their own admin grant, up or down (403). Revoking, banning or deleting yourself is 409.
- Only a `super_admin` grants `super_admin` or `*`; nobody grants a permission they do not hold. `*` with
  the `admin` role is a 422.
- A non-super admin cannot grant to, revoke, ban or edit a `super_admin`, or an admin holding permissions
  they lack (403).
- A `super_admin` grant is exactly `["*"]` (422 otherwise), and only an actor holding every known permission may
  hand it out or manage super admins: a `super_admin` row with a narrower list gains no extra standing.
- The actor's grant is re-read inside the write transaction, so a revoked or reduced admin loses standing at once
  (a request already past `require_admin` is still refused). The re-read does not lock the actor's row, which
  would invite deadlocks between admins acting on each other; the remaining window is one statement wide.
- Banning an admin also needs `admins.delete`. If the ban succeeds but token revocation fails, the audit row is
  still committed and the request returns 503; retrying is safe.
- The last `super_admin` (rows holding `*`) cannot be revoked or demoted (409). The check locks the super-admin rows, so two
  super admins revoking each other at once leave one.

Denied requests write no audit row; they are logged without payload by `require_admin`.

## Audit

Each change stages one row in the same transaction and commits once after `record`:
`admin.users.update`, `admin.users.delete`, `admin.users.ban`, `admin.users.unban`,
`admin.users.grant_admin` and `admin.users.revoke_admin` (resource `admin_users`, with `before` and `after`
role and permissions). Profile updates record changed field names only, never values.

Ban and delete call Firebase after the audit row is staged and before the commit. A Firebase failure rolls
the change back; a commit failure after a successful Firebase call leaves the same residue as account
deletion (see [backend-profile.md](backend-profile.md)).

## Bootstrapping the first admin

The API needs an admin to call it, so the first one is granted from the command line. Run it from `fastapi_backend/`
against the database in `DATABASE_URL` (the owner runs it by hand; nothing schedules it). Setup, environment and the
`--dry-run` first habit: [getting-started.md](getting-started.md#first-admin).

```bash
uv run python -m commands.make_admin someone@example.com --dry-run   # report only, change nothing
uv run python -m commands.make_admin someone@example.com             # role admin
uv run python -m commands.make_admin someone@example.com --role super_admin
```

Flags: `--role admin|super_admin` (default `admin`), `--dry-run`, `--allow-demote`, `--allow-unverified`,
`--refresh-permissions` (`--role admin` only), `--verbose` (traceback on stderr).

- **Who.** An existing user, found by email (trimmed, case-insensitive in the database, then confirmed with Python's
  lower-casing). The target row is locked before it is read, as the API does. Users are created by Firebase sign-in, so
  the person signs in once first; with no match it prints "no user found", changes nothing and exits 1. Two users whose
  emails differ only in case are refused (exit 1). A user whose email is not verified is refused (exit 1) unless
  `--allow-unverified`. An invalid email or option exits 2.
- **What it prints.** Line 1: the environment (`ENVIRONMENT`, and `VERCEL_ENV` when set) and the database as
  `host:port/name`, never the user or password (a URL whose password contains an unencoded `@` prints "an unreadable
  DATABASE_URL"). Then, for every outcome once exactly one user matched, that user's id, email as stored,
  `email_verified`, `created_at` and `last_login_at`; then the result, which names the email in its trimmed form. No
  other user is ever listed.
- **What it writes.** The same single `admin_users` row as `PUT /{user_id}/admin`. `admin` gets every permission in
  `ADMIN_PERMISSIONS` (no smaller preset exists in the app); `super_admin` gets exactly `["*"]`. Trim the list afterwards
  through the API if a narrower admin is wanted.
- **Idempotent, and it does not silently narrow or widen.**
  - Same role again: "already ... No change." (exit 0, nothing written). A `super_admin` is "already" only when the
    stored list is exactly `["*"]`. An `admin` holding fewer than all permissions is reported as "already admin with N of
    M permissions", kept as is, and widened only with `--refresh-permissions`.
  - An existing `super_admin` covers the default `--role admin`: "already super_admin (covers admin). No change." Moving
    one to `admin` needs `--allow-demote`, and the last `super_admin` is never demoted (exit 1), as in the API. A
    `super_admin` row without the wildcard is not counted as one, so changing it is never blocked by that rule.
  - Another role is updated and reported as `changed from X to Y`.
  - A hollow row (a `super_admin` whose list is not exactly `["*"]`, or an `admin` with no usable permission) is
    repaired and reported as a repair, not as a role change.
- **Audit.** Each change stages an `admin.users.grant_admin` row (resource `admin_users`, with `before` and `after`
  role and permissions) in the same transaction. A command has no signed-in admin, so `user_id` is null (the admin audit
  list shows an empty actor) and `metadata.actor` is `commands.make_admin`. Dry runs, refusals and no-ops write no row.
- **Failures.** Database and connection errors (SQLAlchemy, OS) print the error type only, on stderr, and exit 1; a
  real write run says the change "may not have been written; re-run with --dry-run to check", a dry run says nothing was
  written. Other (programming) errors are not caught. `--verbose` adds the traceback on stderr.

Code: `commands/make_admin.py` (rules, on a small `AdminStore` protocol), `AdminUsersRepository.find_user_ids_by_email`
and `AuditService.record_system_action`.

## Tests

- `tests/services/test_admin_users.py`: rules with in-memory fakes.
- `tests/commands/test_make_admin.py`: the bootstrap command on fakes; `tests/commands/test_make_admin_e2e.py`: the same
  against real Postgres (same `TEST_DATABASE_URL` contract).
- `tests/api/test_admin_users.py`: routes through the real `require_admin` chain.
- `tests/api/test_admin_users_e2e.py`: real Postgres through the real app (same `TEST_DATABASE_URL` contract
  as the Stripe webhook e2e); asserts 401, 403, self-escalation refusal, audit rows, rollback and the
  concurrent super-admin case.
