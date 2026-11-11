"""``GET /partner/v1/clicks``: click counts per offer per UTC day for the caller's merchant (FREEJ-59).

The merchant is the one the key was issued to (``PartnerContext``); there is no merchant or offer parameter, so a
key cannot name another merchant's data and there is nothing to probe. Authentication and the per-key read limit
(``PARTNER_READ_POLICY``) are applied by the surface before routing, so nothing is added per route here, and the
surface sets ``Cache-Control: no-store, private`` on every response, this route's own errors included.

The response is narrower than the admin report on purpose: no internal merchant or offer id, no bot count, no
count of offers. Offers are named by title, public slug and public URL, and only listed offers (published or
ended) are ever read. A refused range is one fixed text with one stable code and echoes nothing.
"""

import datetime as dt
import logging
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from starlette.responses import Response

from app.core.click_retention import CLICK_RETENTION_DAYS
from app.core.config import settings
from app.core.errors import (
    CodedHTTPException,
    ErrorResponse,
    error_response,
    validation_exception_handler,
)
from app.core.iso_day import IsoDay
from app.partner.auth import PartnerContextDep
from app.partner.clicks_report import (
    CLICK_DEFINITION,
    MAX_DATE,
    MAX_OFFERS,
    MAX_RANGE_DAYS,
    MIN_DATE,
    InvalidClickRange,
    OfferDays,
    PartnerClickReport,
    PartnerClickReportService,
)
from app.partner.deps import ClickStoreDep

logger = logging.getLogger("yourapp.partner")
router = APIRouter()

INVALID_RANGE_CODE = "invalid_range"
# One text for every refusal of ``from`` or ``to`` (missing, not a real YYYY-MM-DD date, reversed, too long, out of
# bounds). It states the rule and never repeats what the caller sent.
RANGE_MESSAGE = (
    "Send from and to as dates written YYYY-MM-DD, with from on or before to, "
    f"at most {MAX_RANGE_DAYS} days apart (both included) and between {MIN_DATE.year} and {MAX_DATE.year}"
)
RANGE_PARAMETERS = (("query", "from"), ("query", "to"))


class PartnerErrorResponse(ErrorResponse):
    """An error body that may carry a stable machine-readable ``code`` (only ``invalid_range`` today)."""

    code: Literal["invalid_range"] | None = None


class ClickDay(BaseModel):
    date: dt.date = Field(description="A UTC calendar day.")
    clicks: int = Field(description="Clicks counted on that day.")


class ClickOffer(BaseModel):
    title: str
    slug: str = Field(description="The offer's public slug.")
    url: str = Field(description="The offer's public page on the Freejoy website.")
    total_clicks: int = Field(description="The sum of days.")
    days: list[ClickDay] = Field(
        description="Only days with at least one click, oldest first. Empty when the offer had none."
    )


class ClickRetention(BaseModel):
    days: int = Field(description="How long click records are kept, in days.")
    earliest_complete_date: dt.date = Field(
        description="The first UTC day all of whose clicks are still kept. Earlier days can be incomplete."
    )
    starts_before_retention: bool = Field(
        description="True when from is earlier than earliest_complete_date."
    )


class ClicksData(BaseModel):
    from_date: dt.date = Field(description="The first day asked for, as sent.")
    to_date: dt.date = Field(description="The last day asked for, as sent.")
    offers: list[ClickOffer] = Field(
        description=f"Your published and ended offers, busiest first, at most {MAX_OFFERS}."
    )
    offers_truncated: bool = Field(
        description="True when you have more offers than were returned."
    )
    retention: ClickRetention
    definition: str = Field(
        description="What a click is and is not, in plain words.",
        examples=[CLICK_DEFINITION],
    )


class ClicksResponse(BaseModel):
    success: Literal[True] = True
    data: ClicksData


def offer_url(slug: str) -> str:
    return f"{settings.PUBLIC_SITE_URL.rstrip('/')}/freebies/{quote(slug, safe='')}"


def _offer(offer: OfferDays) -> ClickOffer:
    return ClickOffer(
        title=offer.title,
        slug=offer.slug,
        url=offer_url(offer.slug),
        total_clicks=offer.total_clicks,
        days=[ClickDay(date=d.day, clicks=d.clicks) for d in offer.days],
    )


def clicks_response(report: PartnerClickReport) -> ClicksResponse:
    return ClicksResponse(
        data=ClicksData(
            from_date=report.from_date,
            to_date=report.to_date,
            offers=[_offer(o) for o in report.offers],
            offers_truncated=report.offers_truncated,
            retention=ClickRetention(
                days=CLICK_RETENTION_DAYS,
                earliest_complete_date=report.earliest_complete_date,
                starts_before_retention=report.starts_before_retention,
            ),
            definition=CLICK_DEFINITION,
        )
    )


def refused_range() -> Response:
    return error_response(422, RANGE_MESSAGE, code=INVALID_RANGE_CODE)


async def partner_validation_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    """The shared 422 (``Invalid request``), except that every problem with ``from`` or ``to`` gets the one range text.

    Nothing the caller sent is echoed: the answer is built from fixed text and ignores the error details.
    """
    if exc.errors() and all(
        tuple(error["loc"]) in RANGE_PARAMETERS for error in exc.errors()
    ):
        return refused_range()
    return await validation_exception_handler(request, exc)


@router.get(
    "/clicks",
    response_model=ClicksResponse,
    operation_id="partner-clicks",
    summary="Clicks on your offers, per offer per day",
    description=(
        "Outbound clicks on your merchant's published and ended offers over a range of UTC days, per offer and "
        f"per day. The range is `from` to `to`, both included, written YYYY-MM-DD, at most {MAX_RANGE_DAYS} days. "
        f"At most {MAX_OFFERS} offers are returned, busiest first. "
        f"{CLICK_DEFINITION}"
    ),
    responses={
        422: {
            "model": PartnerErrorResponse,
            "description": f"Invalid request. A refused range has the code `{INVALID_RANGE_CODE}`.",
        }
    },
)
async def read_clicks(
    context: PartnerContextDep,
    store: ClickStoreDep,
    date_from: Annotated[
        IsoDay,
        Query(
            alias="from",
            description="First UTC day, YYYY-MM-DD.",
            examples=["2026-03-01"],
        ),
    ],
    date_to: Annotated[
        IsoDay,
        Query(
            alias="to",
            description="Last UTC day, YYYY-MM-DD, included.",
            examples=["2026-03-31"],
        ),
    ],
) -> ClicksResponse:
    try:
        report = await PartnerClickReportService(store).report(
            context.merchant_id, date_from, date_to
        )
    except InvalidClickRange:
        raise CodedHTTPException(422, RANGE_MESSAGE, INVALID_RANGE_CODE) from None
    logger.info(
        "partner clicks read: key_id=%s from=%s to=%s offers=%d",
        context.key_id,
        report.from_date,
        report.to_date,
        len(report.offers),
    )
    return clicks_response(report)
