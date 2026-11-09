"""Is the platform database at the Alembic revision this build was written for?

No deploy runs ``alembic upgrade head`` (ADR-011). When a backend PR with a platform
migration is deployed before the migration is applied, every query on the changed
tables fails. ``/health/ready`` uses this to answer 503 with the applied and expected
revisions instead of the service failing request by request.

The revisions are constants, not read from ``alembic_migrations/`` at runtime, so the
check does not depend on the migration scripts being in the deployed bundle.
``tests/core/test_platform_schema.py`` fails when a migration is added without
appending it here.
"""

from dataclasses import dataclass

# Every platform revision, oldest first; the last one is the head this build expects.
PLATFORM_REVISIONS: tuple[str, ...] = ("0001", "0002", "0003", "0004")
PLATFORM_HEAD = PLATFORM_REVISIONS[-1]


@dataclass(frozen=True)
class PlatformSchemaStatus:
    pending: bool
    current: str | None
    head: str


def platform_schema_status(current: str | None) -> PlatformSchemaStatus:
    """Compare the applied revision with this build's head.

    Pending when nothing is recorded, or when the applied revision is an older one
    of this build's tree. A revision this build does not know is treated as ahead:
    a newer build already migrated the database while this one still serves
    (rolling deploy), so it is not pending here.
    """
    pending = current is None or (
        current != PLATFORM_HEAD and current in PLATFORM_REVISIONS
    )
    return PlatformSchemaStatus(pending=pending, current=current, head=PLATFORM_HEAD)
