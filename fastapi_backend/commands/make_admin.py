"""Give a user the platform operator (admin) role. Run by hand by the owner.

    uv run python -m commands.make_admin someone@example.com                      # grant admin
    uv run python -m commands.make_admin someone@example.com --dry-run            # report only
    uv run python -m commands.make_admin someone@example.com --role super_admin   # full access

The user must already exist: users are created by their first Firebase sign-in, so ask the
person to sign in once before running this. The email is trimmed and compared ignoring case.

What it writes is the same single ``admin_users`` row the admin users API writes
(``PUT /v1/admin/users/{id}/admin``), so the app treats the result like any other admin:

* ``admin`` (the default) gets every permission in ``ADMIN_PERMISSIONS``, listed by name. There is no
  smaller preset in the app: the API takes an explicit list, and an empty one grants nothing.
* ``super_admin`` gets exactly ``["*"]``, which the app expands to every known permission and only
  honours for that role.

Idempotent: a user who already holds the requested role is left alone (exit 0). A user with another
role is moved to the requested one and the report says so. The last super admin is never demoted,
the same guard the API applies. Each change also writes an ``audit_logs`` row
(``admin.users.grant_admin``, resource ``admin_users``) with no ``user_id`` (nobody is signed in)
and ``metadata.actor`` set to ``commands.make_admin``.

Exit codes: 0 done, already done or dry run; 1 no such user, an ambiguous email, a refused demotion
or a database failure; 2 bad arguments. It prints where it ran (host, port, database; never the
password) and the email exactly as given, never any other user.
"""

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import (
    ADMIN_PERMISSIONS,
    ROLE_SUPER_ADMIN,
    WILDCARD,
    effective_permissions,
)
from app.repositories.admin_users import AdminUsersRepository
from app.repositories.audit import AuditRepository
from app.services.audit import AuditService

ROLE_ADMIN = "admin"
ROLES = (ROLE_ADMIN, ROLE_SUPER_ADMIN)
SOURCE = "commands.make_admin"
AUDIT_ACTION = "admin.users.grant_admin"
AUDIT_RESOURCE = "admin_users"
MAX_EMAIL_LENGTH = 254
EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class AdminStore(Protocol):
    async def find_user_ids_by_email(self, email: str, limit: int = 2) -> list[str]: ...
    async def get_admin_grant(self, user_id: str) -> tuple[str, Any] | None: ...
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
    async def rollback(self) -> None: ...


class Outcome(StrEnum):
    GRANTED = "granted"
    UPDATED = "updated"
    ALREADY_ADMIN = "already_admin"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    LAST_SUPER_ADMIN = "last_super_admin"


WRITING_OUTCOMES = frozenset({Outcome.GRANTED, Outcome.UPDATED})
SUCCESS_OUTCOMES = WRITING_OUTCOMES | {Outcome.ALREADY_ADMIN}


@dataclass(frozen=True)
class MakeAdminResult:
    outcome: Outcome
    role: str
    previous_role: str | None
    dry_run: bool

    @property
    def exit_code(self) -> int:
        return 0 if self.outcome in SUCCESS_OUTCOMES else 1


def normalise_email(raw: str) -> str:
    return raw.strip().lower()


def is_valid_email(raw: str) -> bool:
    value = raw.strip()
    return len(value) <= MAX_EMAIL_LENGTH and EMAIL_SHAPE.fullmatch(value) is not None


def permissions_for(role: str) -> list[str]:
    """The permission list stored for ``role``, in the shape the admin users API stores."""
    if role == ROLE_SUPER_ADMIN:
        return [WILDCARD]
    return sorted(ADMIN_PERMISSIONS)


def _state(role: str, stored: Any) -> dict[str, Any]:
    return {"role": role, "permissions": sorted(effective_permissions(role, stored))}


def _is_usable(role: str, held: list[str]) -> bool:
    """Whether a row holds its role in substance, not only in name (a hollow row is repaired)."""
    if role == ROLE_SUPER_ADMIN:
        return set(held) == ADMIN_PERMISSIONS  # the wildcard, expanded
    return bool(held)


async def make_admin(
    store: AdminStore,
    auditor: SystemAuditor,
    session: Committer,
    email: str,
    role: str,
    *,
    dry_run: bool,
) -> MakeAdminResult:
    """Grant ``role`` to the user with ``email``; change nothing when ``dry_run``."""
    if role not in ROLES:
        raise ValueError("Unknown role")
    user_ids = await store.find_user_ids_by_email(normalise_email(email))
    if not user_ids:
        return MakeAdminResult(Outcome.NOT_FOUND, role, None, dry_run)
    if len(user_ids) > 1:
        return MakeAdminResult(Outcome.AMBIGUOUS, role, None, dry_run)
    user_id = user_ids[0]
    grant = await store.get_admin_grant(user_id)
    previous_role = grant[0] if grant else None
    before = _state(*grant) if grant else None
    if before and previous_role == role and _is_usable(role, before["permissions"]):
        return MakeAdminResult(Outcome.ALREADY_ADMIN, role, previous_role, dry_run)
    demoting = previous_role == ROLE_SUPER_ADMIN and role != ROLE_SUPER_ADMIN
    if demoting and await store.lock_super_admin_count() <= 1:
        return MakeAdminResult(Outcome.LAST_SUPER_ADMIN, role, previous_role, dry_run)
    outcome = Outcome.UPDATED if grant else Outcome.GRANTED
    if not dry_run:
        stored = permissions_for(role)
        await store.upsert_admin(user_id, role, stored)
        await auditor.record_system_action(
            SOURCE,
            action=AUDIT_ACTION,
            resource=AUDIT_RESOURCE,
            resource_id=user_id,
            before=before,
            after={"role": role, "permissions": stored},
        )
        await session.commit()
    return MakeAdminResult(outcome, role, previous_role, dry_run)


def describe_database(url: str) -> str:
    """``host:port/database`` for the operator; never the user or the password."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        return "an unreadable DATABASE_URL"
    host = parsed.host or "(local socket)"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port}/{parsed.database or '(default)'}"


def report(email: str, result: MakeAdminResult) -> str:
    role, previous = result.role, result.previous_role
    would = "would be " if result.dry_run else ""
    messages = {
        Outcome.GRANTED: f"{email} {would}made {role}.",
        Outcome.UPDATED: f"{email} {would}changed from {previous} to {role}.",
        Outcome.ALREADY_ADMIN: f"{email} is already {role}. No change.",
        Outcome.NOT_FOUND: (
            f"no user found for {email}. Nothing changed. Ask them to sign in once "
            "first: users are created by their first sign-in."
        ),
        Outcome.AMBIGUOUS: (
            f"more than one user matches {email} ignoring case. Nothing changed."
        ),
        Outcome.LAST_SUPER_ADMIN: (
            f"{email} is the last super_admin and cannot be changed to {role}. "
            "Nothing changed."
        ),
    }
    suffix = " (dry run, nothing written)"
    shown = suffix if result.dry_run and result.outcome in WRITING_OUTCOMES else ""
    return f"make-admin: {messages[result.outcome]}{shown}"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m commands.make_admin",
        description="Give an existing user the platform admin role.",
    )
    parser.add_argument("email", help="the user's email (trimmed, case-insensitive)")
    parser.add_argument("--role", choices=ROLES, default=ROLE_ADMIN)
    parser.add_argument("--dry-run", action="store_true", help="report only")
    args = parser.parse_args(argv)
    args.email = args.email.strip()
    if not is_valid_email(args.email):
        parser.error("email must look like name@domain.tld")
    return args


async def _run(email: str, role: str, *, dry_run: bool) -> MakeAdminResult:
    async with async_session_maker() as session:
        result = await make_admin(
            AdminUsersRepository(session),
            AuditService(AuditRepository(session)),
            session,
            email,
            role,
            dry_run=dry_run,
        )
        if result.dry_run or result.outcome not in WRITING_OUTCOMES:
            await session.rollback()  # release a super admin row lock, if one was taken
        return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_request:  # argparse: 2 for bad arguments, 0 for --help
        return exit_request.code if isinstance(exit_request.code, int) else 2
    print(f"make-admin: database {describe_database(settings.DATABASE_URL)}")
    try:
        result = asyncio.run(_run(args.email, args.role, dry_run=args.dry_run))
    except Exception as error:  # noqa: BLE001  (report the type only, never the message)
        # A driver message can carry the host, user or a statement; only the type is safe.
        print(
            f"make-admin: failed ({type(error).__name__}). Check DATABASE_URL and that "
            "'uv run alembic upgrade head' has run. Nothing was written."
        )
        return 1
    print(report(args.email, result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
