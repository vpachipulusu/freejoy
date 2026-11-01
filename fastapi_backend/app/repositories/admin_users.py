"""Queries behind the admin users API. No business rules live here."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import String, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ROLE_SUPER_ADMIN, WILDCARD
from app.models.platform.admin_user import AdminUser
from app.models.platform.membership import Membership, MembershipRole
from app.models.platform.user import User
from app.repositories.base import BaseRepository


@dataclass(frozen=True)
class AdminUserRecord:
    """A user and their admin grant. Never carries 2FA secrets."""

    id: str
    firebase_uid: str
    email: str
    email_verified: bool
    display_name: str | None
    bio: str | None
    avatar_url: str | None
    location: str | None
    website: str | None
    phone_number: str | None
    two_factor_enabled: bool
    created_at: datetime
    last_login_at: datetime | None
    admin_role: str | None
    admin_permissions: Any  # raw JSONB; interpret with core.permissions


@dataclass(frozen=True)
class AdminUserPage:
    users: list[AdminUserRecord]
    total_count: int


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _select_records() -> Any:
    return select(
        User.id,
        User.firebase_uid,
        User.email,
        User.email_verified,
        User.display_name,
        User.bio,
        User.avatar_url,
        User.location,
        User.website,
        User.phone_number,
        User.two_factor_enabled,
        User.created_at,
        User.last_login_at,
        AdminUser.role,
        AdminUser.permissions,
    ).outerjoin(AdminUser, AdminUser.user_id == User.id)


class AdminUsersRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_users(
        self, search: str | None, limit: int, offset: int
    ) -> AdminUserPage:
        statement = _select_records()
        count = select(func.count()).select_from(User)
        if search:
            pattern = f"%{_escape_like(search)}%"
            match = User.email.ilike(pattern, escape="\\") | User.display_name.ilike(
                pattern, escape="\\"
            )
            statement = statement.where(match)
            count = count.where(match)
        statement = (
            statement.order_by(User.created_at.desc(), User.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(statement)).all()
        total = (await self._session.execute(count)).scalar_one()
        return AdminUserPage([AdminUserRecord(*row) for row in rows], total)

    async def find_user_ids_by_email(self, email: str, limit: int = 2) -> list[str]:
        """Ids of users whose stored email equals ``email`` ignoring case.

        ``email`` must already be trimmed. Both sides are folded in the ``C`` collation, so
        only ASCII letters change case whatever the server locale (an accented or
        look-alike letter never matches its ASCII twin); the caller re-checks in Python. The column is unique but case-sensitive, so
        more than one id is possible; the caller decides what that means.
        """
        statement = (
            select(User.id)
            .where(
                func.lower(User.email.collate("C"))
                == func.lower(literal(email, String).collate("C"))
            )
            .order_by(User.id)
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def get_user(
        self, user_id: str, *, lock: bool = False
    ) -> AdminUserRecord | None:
        if lock:
            # Lock first, then read in a fresh statement: the outer-joined admin
            # columns must be read after any lock wait, not before it.
            await self._session.execute(
                select(User.id).where(User.id == user_id).with_for_update()
            )
        statement = _select_records().where(User.id == user_id)
        row = (await self._session.execute(statement)).one_or_none()
        return AdminUserRecord(*row) if row is not None else None

    async def update_user(self, user_id: str, changes: dict[str, str | None]) -> None:
        await self._session.execute(
            update(User).where(User.id == user_id).values(**changes)
        )

    async def has_owner_membership(self, user_id: str) -> bool:
        statement = (
            select(Membership.role)
            .where(Membership.user_id == user_id)
            .with_for_update()
        )
        roles = (await self._session.execute(statement)).scalars().all()
        return MembershipRole.OWNER in roles

    async def delete_user(self, user_id: str) -> None:
        await self._session.execute(delete(User).where(User.id == user_id))

    async def get_admin_grant(self, user_id: str) -> tuple[str, Any] | None:
        statement = select(AdminUser.role, AdminUser.permissions).where(
            AdminUser.user_id == user_id
        )
        row = (await self._session.execute(statement)).one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def lock_super_admin_count(self) -> int:
        """Count super admins while holding their rows, serialising concurrent removals."""
        statement = (
            select(AdminUser.id)
            .where(
                AdminUser.role == ROLE_SUPER_ADMIN,
                AdminUser.permissions.contains([WILDCARD]),  # hollow rows: not counted
            )
            .with_for_update()
        )
        return len((await self._session.execute(statement)).scalars().all())

    async def upsert_admin(
        self, user_id: str, role: str, permissions: list[str]
    ) -> None:
        statement = insert(AdminUser).values(
            user_id=user_id, role=role, permissions=permissions
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[AdminUser.user_id],
                set_={
                    "role": role,
                    "permissions": permissions,
                    "updated_at": func.now(),
                },
            )
        )

    async def delete_admin(self, user_id: str) -> None:
        await self._session.execute(
            delete(AdminUser).where(AdminUser.user_id == user_id)
        )
