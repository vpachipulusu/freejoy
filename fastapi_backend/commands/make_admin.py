"""Give a user the platform operator (admin) role. Run by hand by the owner.

    uv run python -m commands.make_admin someone@example.com --dry-run            # look first
    uv run python -m commands.make_admin someone@example.com                      # grant admin
    uv run python -m commands.make_admin someone@example.com --role super_admin   # full access

The user must already exist: users are created by their first Firebase sign-in, so ask the
person to sign in once before running this. The email must be plain printable ASCII (exit 2
otherwise), is trimmed and compared ignoring ASCII case; an account whose stored email is not
ASCII is never acted on (use the admin Users page, which works by user id).

Run it with ``--dry-run`` first: every run (dry or not) prints the matched user's id, stored
email, ``email_verified``, ``created_at`` and ``last_login_at`` (that one user only), so you can
confirm it is the person you meant before anything is written.

What it writes is the same single ``admin_users`` row the admin users API writes
(``PUT /v1/admin/users/{id}/admin``), so the app treats the result like any other admin:

* ``admin`` (the default) gets every permission in ``ADMIN_PERMISSIONS``, listed by name. There is no
  smaller preset in the app: the API takes an explicit list, and an empty one grants nothing.
* ``super_admin`` gets exactly ``["*"]``, which the app expands to every known permission and only
  honours for that role.

Idempotent, and it never silently narrows or widens an existing grant:

* Same role again: "already ..., no change" (exit 0). An ``admin`` holding fewer than all permissions
  is left as it is and reported ("N of M"); ``--refresh-permissions`` grants the full set (admin only).
* An existing ``super_admin`` covers the default ``--role admin`` (no change, exit 0) only when
  its list contains ``*``. A hollow ``super_admin`` (no ``*``) on the default role is refused:
  repair it with ``--role super_admin``, or change it to ``admin`` with ``--allow-demote``.
  Changing any ``super_admin`` to ``admin`` needs ``--allow-demote`` and is still refused for the
  last super admin.
* Another role is changed to the requested one and the report says so.
* A hollow row (``super_admin`` whose list is not exactly ``["*"]``, or an ``admin`` with no usable
  permission) is repaired and reported as a repair.
* A user whose email is not verified is refused when a change would be made, unless
  ``--allow-unverified`` is given; a no-op on such an account exits 0 with a warning line.

The target row is locked before it is read, as the API does, so the decision and the audit ``before``
cannot go stale; the transaction sets ``lock_timeout`` to 5s, so a row held by another session
fails fast ("retry shortly", nothing committed). Each change also writes an ``audit_logs`` row
(``admin.users.grant_admin``, resource ``admin_users``) with no ``user_id`` (nobody is signed in)
and ``metadata.actor`` set to ``commands.make_admin``.

Exit codes: 0 done, already done or dry run; 1 no such user, an ambiguous email, a stored email
that is not ASCII, an unverified user, a refused demotion or a database failure; 2 bad arguments.
The first line names the environment and the database (host, port, name; never the password).
Failures print the error type only (``--verbose`` adds the traceback, on stderr).
"""

import argparse
import asyncio
import re
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import (
    ADMIN_PERMISSIONS,
    ROLE_SUPER_ADMIN,
    WILDCARD,
    effective_permissions,
)
from app.repositories.admin_users import AdminUserRecord, AdminUsersRepository
from app.repositories.audit import AuditRepository
from app.services.audit import AuditService

ROLE_ADMIN = "admin"
ROLES = (ROLE_ADMIN, ROLE_SUPER_ADMIN)
SOURCE = "commands.make_admin"
AUDIT_ACTION = "admin.users.grant_admin"
AUDIT_RESOURCE = "admin_users"
MAX_EMAIL_LENGTH = 254
LOCK_NOT_AVAILABLE = "55P03"  # SQLSTATE lock_not_available, from lock_timeout
LOCK_TIMEOUT = "5s"  # fail fast instead of waiting on a row another session holds
EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class AdminStore(Protocol):
    async def find_user_ids_by_email(self, email: str, limit: int = 2) -> list[str]: ...
    async def get_user(
        self, user_id: str, *, lock: bool = False
    ) -> AdminUserRecord | None: ...
    async def lock_super_admin_count(self) -> int: ...
    async def upsert_admin(
        self, user_id: str, role: str, permissions: list[str]
    ) -> None: ...


class SystemAuditor(Protocol):
    async def record_system_action(
        self,
        source: str,
        *,
        action: str,
        resource: str,
        resource_id: str | None = None,
        before: Any = None,
        after: Any = None,
        details: Any = None,
    ) -> None: ...


class Committer(Protocol):
    async def commit(self) -> None: ...


class Outcome(StrEnum):
    GRANTED = "granted"
    UPDATED = "updated"
    REPAIRED = "repaired"
    REFRESHED = "refreshed"
    ALREADY_ADMIN = "already_admin"
    ALREADY_COVERED = "already_covered"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    UNVERIFIED = "unverified"
    NOT_ASCII = "not_ascii"
    DEMOTE_REFUSED = "demote_refused"
    LAST_SUPER_ADMIN = "last_super_admin"


WRITING_OUTCOMES = frozenset(
    {Outcome.GRANTED, Outcome.UPDATED, Outcome.REPAIRED, Outcome.REFRESHED}
)
SUCCESS_OUTCOMES = WRITING_OUTCOMES | {Outcome.ALREADY_ADMIN, Outcome.ALREADY_COVERED}


@dataclass(frozen=True)
class Options:
    role: str = ROLE_ADMIN
    dry_run: bool = False
    allow_demote: bool = False
    allow_unverified: bool = False
    refresh_permissions: bool = False


@dataclass(frozen=True)
class MatchedUser:
    """The one matched user, as stored. Never more than one, never anyone else."""

    id: str
    email: str
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True)
class MakeAdminResult:
    outcome: Outcome
    role: str
    previous_role: str | None
    dry_run: bool
    user: MatchedUser | None = None
    held: int | None = None  # permissions the user's admin row actually grants

    @property
    def exit_code(self) -> int:
        return 0 if self.outcome in SUCCESS_OUTCOMES else 1


@dataclass(frozen=True)
class _Plan:
    outcome: Outcome
    held: int | None
    demotes_counted_super_admin: bool = False


def normalise_email(raw: str) -> str:
    return raw.strip().lower()


def is_plain_ascii(value: str) -> bool:
    """ASCII and printable: no accents, look-alike letters or control/ANSI characters."""
    return value.isascii() and value.isprintable()


def is_valid_email(raw: str) -> bool:
    value = raw.strip()
    return (
        len(value) <= MAX_EMAIL_LENGTH
        and is_plain_ascii(value)
        and EMAIL_SHAPE.fullmatch(value) is not None
    )


def permissions_for(role: str) -> list[str]:
    """The permission list stored for ``role``, in the shape the admin users API stores."""
    if role == ROLE_SUPER_ADMIN:
        return [WILDCARD]
    return sorted(ADMIN_PERMISSIONS)


def _state(role: str, stored: Any) -> dict[str, Any]:
    return {"role": role, "permissions": sorted(effective_permissions(role, stored))}


def _counts_as_super_admin(stored: Any) -> bool:
    """Whether the lock/count query counts this row: a list containing the wildcard."""
    return isinstance(stored, list) and WILDCARD in stored


def _unchanged(record: AdminUserRecord, options: Options, held: int) -> _Plan | None:
    """The no-op outcome when the user already satisfies the request, else ``None``."""
    previous, stored = record.admin_role, record.admin_permissions
    if previous == ROLE_SUPER_ADMIN and options.role == ROLE_SUPER_ADMIN:
        return _Plan(Outcome.ALREADY_ADMIN, held) if stored == [WILDCARD] else None
    if previous == ROLE_SUPER_ADMIN and not options.allow_demote:  # asked for admin
        covered = _counts_as_super_admin(stored)
        outcome = Outcome.ALREADY_COVERED if covered else Outcome.DEMOTE_REFUSED
        return _Plan(outcome, held)
    if previous == ROLE_ADMIN and options.role == ROLE_ADMIN and held:
        complete = held == len(ADMIN_PERMISSIONS)
        if complete or not options.refresh_permissions:
            return _Plan(Outcome.ALREADY_ADMIN, held)
    return None


def _change_outcome(record: AdminUserRecord, role: str) -> Outcome:
    previous = record.admin_role
    if previous is None:
        return Outcome.GRANTED
    if previous != role:
        return Outcome.UPDATED
    hollow = role == ROLE_SUPER_ADMIN or not effective_permissions(
        role, record.admin_permissions
    )
    return Outcome.REPAIRED if hollow else Outcome.REFRESHED


def _plan(record: AdminUserRecord, options: Options) -> _Plan:
    previous = record.admin_role
    held = (
        len(effective_permissions(previous, record.admin_permissions))
        if previous
        else 0
    )
    unchanged = _unchanged(record, options, held)
    if unchanged:
        return unchanged
    if not record.email_verified and not options.allow_unverified:
        return _Plan(Outcome.UNVERIFIED, held)
    demotes = (
        previous == ROLE_SUPER_ADMIN
        and options.role != ROLE_SUPER_ADMIN
        and _counts_as_super_admin(record.admin_permissions)
    )
    return _Plan(_change_outcome(record, options.role), held, demotes)


async def _match(
    store: AdminStore, email: str
) -> tuple[AdminUserRecord | None, Outcome | None]:
    """The single locked user whose stored email equals ``email``, or why there is none.

    A user is returned with ``NOT_ASCII`` so the report can show the look-alike; every
    other refusal returns no user.
    """
    wanted = normalise_email(email)
    user_ids = await store.find_user_ids_by_email(wanted)
    if len(user_ids) > 1:
        return None, Outcome.AMBIGUOUS
    record = await store.get_user(user_ids[0], lock=True) if user_ids else None
    # The database folds case ASCII-only; insist on Python's fold and plain ASCII too.
    if record is None or record.email.lower() != wanted:
        return None, Outcome.NOT_FOUND
    if not record.email.isascii():
        return record, Outcome.NOT_ASCII
    return record, None


def _matched(record: AdminUserRecord) -> MatchedUser:
    return MatchedUser(
        record.id,
        record.email,
        record.email_verified,
        record.created_at,
        record.last_login_at,
    )


async def make_admin(
    store: AdminStore,
    auditor: SystemAuditor,
    session: Committer,
    email: str,
    options: Options,
) -> MakeAdminResult:
    """Grant ``options.role`` to the user with ``email``; change nothing on a dry run."""
    if options.role not in ROLES:
        raise ValueError("Unknown role")
    found, refusal = await _match(store, email)
    if found is None or refusal is not None:
        user = _matched(found) if found else None
        return MakeAdminResult(
            refusal or Outcome.NOT_FOUND, options.role, None, options.dry_run, user
        )
    plan = _plan(found, options)
    outcome = plan.outcome
    if plan.demotes_counted_super_admin and await store.lock_super_admin_count() <= 1:
        outcome = Outcome.LAST_SUPER_ADMIN
    if outcome in WRITING_OUTCOMES and not options.dry_run:
        stored = permissions_for(options.role)
        await store.upsert_admin(found.id, options.role, stored)
        previous = found.admin_role
        before = _state(previous, found.admin_permissions) if previous else None
        await auditor.record_system_action(
            SOURCE,
            action=AUDIT_ACTION,
            resource=AUDIT_RESOURCE,
            resource_id=found.id,
            before=before,
            after={"role": options.role, "permissions": stored},
        )
        await session.commit()
    return MakeAdminResult(
        outcome,
        options.role,
        found.admin_role,
        options.dry_run,
        _matched(found),
        plan.held,
    )


def describe_database(url: str) -> str:
    """``host:port/database`` for the operator; never the user or the password."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        return "an unreadable DATABASE_URL"
    if "@" in (
        parsed.host or ""
    ):  # an unencoded "@" in the password leaked into the host
        return "an unreadable DATABASE_URL"
    host = parsed.host or "(local socket)"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port}/{parsed.database or '(default)'}"


def describe_target(environment: str, vercel_env: str | None, url: str) -> str:
    parts = [f"environment {environment}"]
    if vercel_env:
        parts.append(f"vercel_env {vercel_env}")
    parts.append(f"database {describe_database(url)}")
    return "make-admin: " + ", ".join(parts)


def _user_line(user: MatchedUser) -> str:
    last = user.last_login_at.isoformat() if user.last_login_at else "never"
    return (
        f"make-admin: matched user id={user.id!a} email={user.email!a} "
        f"email_verified={str(user.email_verified).lower()} "
        f"created_at={user.created_at.isoformat()} last_login_at={last}"
    )


def _repair_message(email: str, result: MakeAdminResult, would: str) -> str:
    role = result.role
    if role == ROLE_SUPER_ADMIN and result.held == len(ADMIN_PERMISSIONS):
        return (
            f"{email} had super_admin with a list that already granted everything; "
            f'{would}normalised to ["*"].'
        )
    return (
        f"{email} had {role} without its permissions; {would}repaired "
        f"to the standard {role} grant."
    )


def _messages(email: str, result: MakeAdminResult) -> dict[Outcome, str]:
    role, previous = result.role, result.previous_role
    would = "would be " if result.dry_run else ""
    total = len(ADMIN_PERMISSIONS)
    if result.outcome is Outcome.ALREADY_ADMIN and role == ROLE_ADMIN:
        partial = result.held is not None and result.held < total
        already = (
            f"{email} is already admin with {result.held} of {total} permissions. "
            "No change; pass --refresh-permissions to grant the full set."
            if partial
            else f"{email} is already admin. No change."
        )
    else:
        already = f"{email} is already {role}. No change."
    return {
        Outcome.GRANTED: f"{email} {would}made {role}.",
        Outcome.UPDATED: f"{email} {would}changed from {previous} to {role}.",
        Outcome.REPAIRED: _repair_message(email, result, would),
        Outcome.REFRESHED: (
            f"{email} {would}refreshed from {result.held} to the full {total} "
            "admin permissions."
        ),
        Outcome.ALREADY_ADMIN: already,
        Outcome.ALREADY_COVERED: (
            f"{email} is already super_admin (covers admin). No change."
        ),
        Outcome.NOT_FOUND: (
            f"no user found for {email}. Nothing changed. Ask them to sign in once "
            "first: users are created by their first sign-in."
        ),
        Outcome.NOT_ASCII: (
            f"the account matching {email} has a stored email that is not plain ASCII "
            "(see the matched user above), so this command will not act on it. Nothing "
            "changed. Grant it from the admin Users page, which works by user id."
        ),
        Outcome.DEMOTE_REFUSED: (
            f"{email} is a super_admin without the standard wildcard list; changing it "
            "to admin needs --allow-demote (or use --role super_admin to repair it). "
            "Nothing changed."
        ),
        Outcome.AMBIGUOUS: (
            f"more than one user matches {email} ignoring case. Nothing changed."
        ),
        Outcome.UNVERIFIED: (
            f"{email} has not verified their email. Nothing changed; check the "
            "matched user above and pass --allow-unverified to proceed."
        ),
        Outcome.LAST_SUPER_ADMIN: (
            f"{email} is the last super_admin and cannot be changed to {role}. "
            "Nothing changed."
        ),
    }


def report(email: str, result: MakeAdminResult) -> str:
    """The matched-user line (when one user matched) and the result line."""
    line = f"make-admin: {_messages(email, result)[result.outcome]}"
    if result.dry_run and result.outcome in WRITING_OUTCOMES:
        line += " (dry run, nothing written)"
    if not result.user:
        return line
    lines = [_user_line(result.user), line]
    no_op = result.outcome in {Outcome.ALREADY_ADMIN, Outcome.ALREADY_COVERED}
    if no_op and not result.user.email_verified:
        lines.append("make-admin: warning: this account's email is not verified")
    return "\n".join(lines)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m commands.make_admin",
        description="Give an existing user the platform admin role.",
    )
    parser.add_argument("email", help="the user's email (trimmed, case-insensitive)")
    parser.add_argument("--role", choices=ROLES, default=ROLE_ADMIN)
    parser.add_argument("--dry-run", action="store_true", help="report only")
    parser.add_argument(
        "--allow-demote",
        action="store_true",
        help="let --role admin demote an existing super_admin",
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="proceed although the user's email is not verified",
    )
    parser.add_argument(
        "--refresh-permissions",
        action="store_true",
        help="give an admin holding fewer permissions the full set (--role admin)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print the traceback of a failure"
    )
    args = parser.parse_args(argv)
    args.email = args.email.strip()
    if not is_plain_ascii(args.email):
        parser.error(
            "email must be plain printable ASCII (no accents, look-alike letters "
            "or control characters); use the admin Users page for such an account"
        )
    if not is_valid_email(args.email):
        parser.error("email must look like name@domain.tld")
    if args.refresh_permissions and args.role != ROLE_ADMIN:
        parser.error("--refresh-permissions only applies to --role admin")
    return args


async def _run(email: str, options: Options) -> MakeAdminResult:
    async with async_session_maker() as session:
        # Inside the transaction, so it also covers the FOR UPDATE reads below.
        await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        result = await make_admin(
            AdminUsersRepository(session),
            AuditService(AuditRepository(session)),
            session,
            email,
            options,
        )
        if result.dry_run or result.outcome not in WRITING_OUTCOMES:
            await session.rollback()  # release the row locks taken while deciding
        return result


def _sqlstate(error: BaseException) -> str | None:
    """The SQLSTATE of a driver error, looking through SQLAlchemy's wrapper if any."""
    for candidate in (error, getattr(error, "orig", None), error.__cause__):
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str):
                return value
    return None


def _fail(error: Exception, options: Options, *, verbose: bool) -> int:
    # A driver message can carry the host, user or a statement; only the type is safe.
    if _sqlstate(error) == LOCK_NOT_AVAILABLE:
        # Raised while taking the row lock, before any write: nothing was committed.
        print(
            "make-admin: the row is locked by another session; retry shortly. "
            "Nothing was written.",
            file=sys.stderr,
        )
        if verbose:
            traceback.print_exception(error, file=sys.stderr)
        return 1
    outcome = (
        "Nothing was written."
        if options.dry_run
        else "The change may not have been written; re-run with --dry-run to check."
    )
    print(
        f"make-admin: failed ({type(error).__name__}). Check DATABASE_URL and that "
        f"'uv run alembic upgrade head' has run. {outcome}",
        file=sys.stderr,
    )
    if verbose:
        traceback.print_exception(error, file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_request:  # argparse: 2 for bad arguments, 0 for --help
        return exit_request.code if isinstance(exit_request.code, int) else 2
    options = Options(
        role=args.role,
        dry_run=args.dry_run,
        allow_demote=args.allow_demote,
        allow_unverified=args.allow_unverified,
        refresh_permissions=args.refresh_permissions,
    )
    print(
        describe_target(
            settings.ENVIRONMENT, settings.VERCEL_ENV, settings.DATABASE_URL
        )
    )
    try:
        result = asyncio.run(_run(args.email, options))
    except (SQLAlchemyError, OSError) as error:  # database and connection failures only
        return _fail(error, options, verbose=args.verbose)
    print(report(args.email, result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
