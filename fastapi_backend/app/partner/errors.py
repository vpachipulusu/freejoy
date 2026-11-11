"""The refusals a partner submission can meet (ADR-013, FREEJ-58).

Every one has a fixed HTTP status, a fixed message from the closed set the partner surface may speak, and a stable
``code`` a retailer's software can branch on. Nothing a caller sent is ever put in a message or a code. The route
layer turns a ``SubmissionRefused`` into the response (``offer_routes.py``).
"""

from typing import Final, Literal

REFUSAL_CODES = Literal[
    "invalid_url",
    "link_host_not_allowed",
    "unknown_category",
    "invalid_postage",
    "invalid_window",
    "empty_text",
    "subscription_terms_required",
    "offer_closed",
    "open_submission_cap",
    "daily_submission_cap",
    "daily_write_budget",
]

MESSAGE_INVALID: Final = "Invalid request"
MESSAGE_CONFLICT: Final = "Conflict"
MESSAGE_TOO_MANY: Final = "Too many requests"
MESSAGE_NOT_FOUND: Final = "Not Found"
MESSAGE_BUSY: Final = "Service temporarily unavailable"

_STATUS_AND_MESSAGE: Final[dict[str, tuple[int, str]]] = {
    "invalid_url": (422, MESSAGE_INVALID),
    "link_host_not_allowed": (422, MESSAGE_INVALID),
    "unknown_category": (422, MESSAGE_INVALID),
    "invalid_postage": (422, MESSAGE_INVALID),
    "invalid_window": (422, MESSAGE_INVALID),
    "empty_text": (422, MESSAGE_INVALID),
    "subscription_terms_required": (422, MESSAGE_INVALID),
    "offer_closed": (409, MESSAGE_CONFLICT),
    "open_submission_cap": (429, MESSAGE_TOO_MANY),
    "daily_submission_cap": (429, MESSAGE_TOO_MANY),
    "daily_write_budget": (429, MESSAGE_TOO_MANY),
}


class SubmissionRefused(Exception):
    """A request the rules refuse. ``code`` is one of ``REFUSAL_CODES``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    @property
    def status_code(self) -> int:
        return _STATUS_AND_MESSAGE[self.code][0]

    @property
    def message(self) -> str:
        return _STATUS_AND_MESSAGE[self.code][1]


class SubmissionNotFound(Exception):
    """No submission with this ref for this merchant (a ref of another merchant is the same: one query, one answer)."""


class SubmissionBusy(Exception):
    """The database could not take the write in time (a lock timeout, a statement timeout or a deadlock): retry."""
