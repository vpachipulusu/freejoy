"""Admin partner keys: list, issue and revoke a merchant's API keys (ADR-013).

Issuing returns the key once, in the return value of ``create_key``; it is never stored (only its
SHA-256 is), never audited and never logged. Create and revoke each stage one audit row on the same
session and commit once (the commit contract of ``app.services.audit``). A merchant with no website, or
an inactive one, cannot be given a key. Revoking a key that is already revoked is a no-op that stages no
audit row.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.ids import new_id
from app.core.partner_keys import KEY_LOOKING, generate_key
from app.core.permissions import AdminContext
from app.repositories.admin_merchants import MerchantRecord
from app.repositories.admin_partner_keys import PartnerKeyRecord
from app.services.admin_catalogue_common import AuditRecorder, Committer
from app.services.admin_catalogue_errors import (
    CatalogueNotFound,
    InvalidInput,
    KeyNotAllowed,
)

__all__ = ["AdminPartnerKeyService", "IssuedKey"]

RESOURCE = "partner_api_keys"
# A label that carries a key (or the start of one) would keep the secret in plain text and show it in the UI.


class KeyStore(Protocol):
    async def list_for_merchant(self, merchant_id: str) -> list[PartnerKeyRecord]: ...
    async def get(
        self, merchant_id: str, key_id: str, *, lock: bool = False
    ) -> PartnerKeyRecord | None: ...
    async def insert(self, values: dict[str, Any]) -> PartnerKeyRecord: ...
    async def revoke(self, merchant_id: str, key_id: str) -> PartnerKeyRecord: ...


class MerchantReader(Protocol):
    async def get(
        self, merchant_id: str, *, lock: bool = False
    ) -> MerchantRecord | None: ...


@dataclass(frozen=True)
class IssuedKey:
    """The new key's record and its value, which exists nowhere else."""

    record: PartnerKeyRecord
    value: str

    def __repr__(self) -> str:
        return f"IssuedKey(record={self.record!r})"


class AdminPartnerKeyService:
    def __init__(
        self,
        keys: KeyStore,
        merchants: MerchantReader,
        session: Committer,
        auditor: AuditRecorder,
        environment: str | None,
    ) -> None:
        self.keys, self.merchants = keys, merchants
        self.session, self.auditor = session, auditor
        self.environment = environment

    async def list_keys(self, merchant_id: str) -> list[PartnerKeyRecord]:
        await self._merchant(merchant_id)
        return await self.keys.list_for_merchant(merchant_id)

    async def create_key(
        self, actor: AdminContext, merchant_id: str, label: str
    ) -> IssuedKey:
        if self.environment is None:
            raise KeyNotAllowed("keys_unavailable")
        if KEY_LOOKING.search(label):
            raise InvalidInput("invalid_label")
        merchant = await self._merchant(merchant_id, lock=True)
        if not merchant.active:
            raise KeyNotAllowed("merchant_inactive")
        if not merchant.website:
            raise KeyNotAllowed("merchant_no_website")
        key = generate_key(self.environment, new_id())
        record = await self.keys.insert(
            {
                "id": key.key_id,
                "merchant_id": merchant_id,
                "key_hash": key.digest,
                "label": label,
                "created_by": actor.user_id,
            }
        )
        await self.auditor.record(
            actor,
            action="admin.partner_keys.create",
            resource=RESOURCE,
            resource_id=record.id,
            after={"merchant_id": merchant_id, "label": record.label},
        )
        await self.session.commit()
        return IssuedKey(record=record, value=key.value)

    async def revoke_key(
        self, actor: AdminContext, merchant_id: str, key_id: str
    ) -> PartnerKeyRecord:
        await self._merchant(merchant_id)
        current = await self.keys.get(merchant_id, key_id, lock=True)
        if current is None:
            raise CatalogueNotFound("partner_key")
        if current.revoked_at is not None:
            await self.session.rollback()  # release the row lock; nothing changed
            return current
        revoked = await self.keys.revoke(merchant_id, key_id)
        await self.auditor.record(
            actor,
            action="admin.partner_keys.revoke",
            resource=RESOURCE,
            resource_id=key_id,
            details={"merchant_id": merchant_id, "label": current.label},
        )
        await self.session.commit()
        return revoked

    async def _merchant(
        self, merchant_id: str, *, lock: bool = False
    ) -> MerchantRecord:
        found = await self.merchants.get(merchant_id, lock=lock)
        if found is None:
            raise CatalogueNotFound("merchant")
        return found
