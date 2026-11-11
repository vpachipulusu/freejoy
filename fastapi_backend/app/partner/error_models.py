"""The error bodies of the partner contract, described for a retailer (ADR-013).

These are the models the partner OpenAPI document shows for every error answer. They document the same envelope the
shared handler sends (``{"success": false, "error": "<message>"}``) but carry retailer-facing descriptions: the
shared ``app.core.errors.ErrorResponse`` is also the internal API's model, and its description is written for us.
"""

from typing import Literal

from pydantic import BaseModel, Field


class PartnerErrorResponse(BaseModel):
    """The body of an error answer: a fixed message that never repeats anything you sent."""

    success: Literal[False] = False
    error: str = Field(
        description="A short fixed message. Do not match on it: use the status."
    )


class PartnerRangeErrorResponse(PartnerErrorResponse):
    """The body of a refused date range: the fixed range message and a stable code."""

    code: Literal["invalid_range"] | None = Field(
        default=None,
        description="A code that does not change. Match on it, not on the message.",
    )
