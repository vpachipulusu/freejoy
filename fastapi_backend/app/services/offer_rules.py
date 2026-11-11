"""Content rules for offers that the schema cannot express on its own.

The pure rules (the editable and audited field sets, the postage and window checks) live in
``app/core/offer_content.py`` so the partner API shares them; this module re-exports them and turns a problem
code into ``InvalidInput``, so no existing caller changes.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from app.core.affiliate_hosts import PROVIDER_HOSTS
from app.core.offer_content import (
    AUDITED_FIELDS,
    CURRENCY_LENGTH,
    EDITABLE_FIELDS,
    URL_FIELDS,
    postage_problem,
    window_problem,
)
from app.services.admin_catalogue_errors import InvalidInput

__all__ = [
    "AUDITED_FIELDS",
    "CURRENCY_LENGTH",
    "EDITABLE_FIELDS",
    "URL_FIELDS",
    "check_postage",
    "check_window",
    "needs_basis_confirmation",
]


def needs_basis_confirmation(links: Iterable[tuple[str, bool]]) -> bool:
    """Whether publishing needs the editor's confirmed commercial basis, from ``(provider, active)`` pairs.

    True when there is at least one active link and none of the active links is on an affiliate network
    (``PROVIDER_HOSTS``, the same list the migration backfill used): nothing then says how the listing is
    paid for. No active link at all is False: nothing can be followed, so there is nothing to confirm.
    """
    providers = [provider for provider, active in links if active]
    return bool(providers) and not any(name in PROVIDER_HOSTS for name in providers)


def check_postage(values: Mapping[str, Any]) -> None:
    """The amount and a 3-letter upper-case currency exist exactly when postage is COST."""
    problem = postage_problem(values)
    if problem is not None:
        raise InvalidInput(problem)


def check_window(start_at: datetime | None, end_at: datetime | None) -> None:
    problem = window_problem(start_at, end_at)
    if problem is not None:
        raise InvalidInput(problem)
