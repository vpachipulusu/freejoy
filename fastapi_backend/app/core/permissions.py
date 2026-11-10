"""Platform-admin permission model (ADR-003 point 7).

Permissions are ``resource.action`` strings stored on ``admin_users.permissions``.
They are read from the database only. Nothing in a request (header, query,
body) is ever consulted, so a caller cannot widen their own permissions.

Wildcard decision: the stored string ``*`` is honoured only for the
``super_admin`` role, and then it expands to ``ADMIN_PERMISSIONS`` (the known
registry), never to "anything". A ``*`` on any other role is ignored, which
closes the legacy hole where an admin could grant themselves ``*``.
Unknown strings in the stored list are dropped.
"""

from dataclasses import dataclass
from typing import Any

WILDCARD = "*"
ROLE_SUPER_ADMIN = "super_admin"

# The permissions the legacy admin actions check. New admin endpoints append here;
# a route that requires a name missing from this set fails at import time.
ADMIN_PERMISSIONS: frozenset[str] = frozenset(
    {
        "admins.create",
        "admins.delete",
        "audit.read",
        "categories.write",
        "merchants.read",
        "merchants.write",
        "notifications.create",
        "offers.publish",
        "offers.read",
        "offers.write",
        "seo.delete",
        "seo.read",
        "seo.write",
        "settings.delete",
        "settings.read",
        "settings.write",
        "subscriptions.grant",
        "subscriptions.read",
        "subscriptions.refund",
        "subscriptions.sync",
        "subscriptions.update",
        "subscribers.read",
        "users.delete",
        "users.read",
        "users.update",
    }
)


@dataclass(frozen=True)
class AdminContext:
    """The authenticated platform admin and what they may do."""

    user_id: str
    role: str
    permissions: frozenset[str]


def effective_permissions(role: str, stored: Any) -> frozenset[str]:
    """Permissions a stored admin record actually grants (fails closed)."""
    if not isinstance(stored, list):
        return frozenset()
    held = {item for item in stored if isinstance(item, str)}
    if WILDCARD in held and role == ROLE_SUPER_ADMIN:
        return ADMIN_PERMISSIONS
    return frozenset(held & ADMIN_PERMISSIONS)


def validate_required(permissions: tuple[str, ...]) -> frozenset[str]:
    """Check a route's declared permissions; a bad declaration is a bug, not a 403."""
    if not permissions:
        raise ValueError("require_admin needs at least one permission")
    unknown = set(permissions) - ADMIN_PERMISSIONS
    if unknown:
        raise ValueError(f"Unknown admin permission(s): {sorted(unknown)}")
    return frozenset(permissions)


def assert_can_grant(actor: AdminContext, role: str, permissions: Any) -> bool:
    """Whether ``actor`` may write ``role``/``permissions`` onto an admin record.

    The guard for the grant endpoints against self-escalation: the
    actor needs ``admins.create``, only a ``super_admin`` may grant the
    ``super_admin`` role or the wildcard, and nobody can grant a permission they do
    not hold. Unknown or malformed input is refused, and so is a ``super_admin`` that
    is not exactly the wildcard.
    """
    if "admins.create" not in actor.permissions:
        return False
    if not isinstance(permissions, list) or not all(
        isinstance(item, str) for item in permissions
    ):
        return False
    wanted = set(permissions)
    if role == ROLE_SUPER_ADMIN or WILDCARD in wanted:
        # A super_admin always carries the wildcard and only a holder of every
        # known permission may hand it out, so a hollow super_admin row cannot.
        return (
            role == ROLE_SUPER_ADMIN
            and wanted == {WILDCARD}
            and actor.role == ROLE_SUPER_ADMIN
            and actor.permissions >= ADMIN_PERMISSIONS
        )
    return wanted <= ADMIN_PERMISSIONS and wanted <= actor.permissions
