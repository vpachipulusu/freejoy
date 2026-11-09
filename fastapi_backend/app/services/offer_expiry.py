"""Move published offers whose ``end_at`` has passed to EXPIRED (the offer health command).

The status change is the ``expire`` action of the offer status machine (``offer_lifecycle``), so a
second table of rules does not exist: ``next_status`` decides, and refuses anything that is not
``PUBLISHED``. What differs from the admin ``expire`` endpoint is the actor. Nobody is signed in,
so each change writes the same audit entry (``admin.offers.expire``, resource ``offers``) through
``AuditService.record_system_action``: ``user_id`` is null and ``metadata.actor`` names the command.

Offers are handled in committed batches. A batch locks its rows (``SKIP LOCKED``: an offer an
editor is saving right now waits for the next run), changes their status, stages one audit row per
offer and commits once, so an offer and its audit entry stand or fall together. Rows already
expired are no longer selected, which is what makes a rerun do nothing.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from app.models.platform.offer import OfferStatus
from app.repositories.offer_health import DueOffer
from app.services.offer_lifecycle import OfferAction, next_status

SOURCE = "commands.check_offer_health"
AUDIT_ACTION = f"admin.offers.{OfferAction.EXPIRE.value}"
AUDIT_RESOURCE = "offers"
EXPIRY_REASON = "end_at_passed"
DEFAULT_BATCH_SIZE = 200


class ExpiryStore(Protocol):
    async def lock_due_offers(self, now: datetime, limit: int) -> list[DueOffer]: ...
    async def mark_expired(self, offer_ids: Sequence[str]) -> int: ...


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


class ExpiryFailed(Exception):
    """A batch failed; ``expired`` offers were already committed by earlier batches."""

    def __init__(self, expired: int) -> None:
        super().__init__(expired)
        self.expired = expired


async def _expire_batch(
    store: ExpiryStore, auditor: SystemAuditor, due: Sequence[DueOffer]
) -> None:
    for offer in due:
        next_status(offer.status, OfferAction.EXPIRE)  # raises unless PUBLISHED
    changed = await store.mark_expired([offer.id for offer in due])
    if changed != len(due):
        raise RuntimeError("expired row count mismatch")
    for offer in due:
        await auditor.record_system_action(
            SOURCE,
            action=AUDIT_ACTION,
            resource=AUDIT_RESOURCE,
            resource_id=offer.id,
            before={"status": offer.status.value},
            after={"status": OfferStatus.EXPIRED.value},
            details={"reason": EXPIRY_REASON, "end_at": offer.end_at},
        )


async def expire_ended_offers(
    store: ExpiryStore,
    session: Committer,
    auditor: SystemAuditor,
    now: datetime,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Expire every published offer with ``end_at <= now``; return how many.

    Raises ``ExpiryFailed`` carrying the running total if a batch fails (that batch is not
    committed; earlier ones stay).
    """
    total = 0
    while True:
        try:
            due = await store.lock_due_offers(now, batch_size)
            if not due:
                return total
            await _expire_batch(store, auditor, due)
            await session.commit()
        except Exception as error:
            raise ExpiryFailed(total) from error
        total += len(due)
        if len(due) < batch_size:
            return total
