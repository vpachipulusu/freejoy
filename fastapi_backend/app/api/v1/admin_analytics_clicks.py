"""Response shapes of the per-merchant click report (``GET /v1/admin/analytics/clicks``).

Counts only: a merchant, its offers and clicks per offer per UTC day. No referrer host, no user agent, no
link or destination, nothing that could point at a visitor.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.core.iso_day import IsoDay
from app.services.merchant_click_report import ClickReport

CLICK_DEFINITION = (
    "A click is one visitor sent through a Freejoy link to the destination. It is not a sale, a "
    "sign-up or a claim, and it does not count unique people: a person who clicks twice counts twice."
)
BOT_RULE = (
    "Clicks the site classed as BOT (user_agent_class) are left out and counted in "
    "excluded_bot_clicks; no other filtering is applied. Click records are deleted after the "
    "retention period."
)

__all__ = [
    "IsoDay"
]  # re-exported: it moved to app.core.iso_day, callers import it from here


class ClickMerchant(BaseModel):
    id: str
    name: str
    slug: str


class ClickDayData(BaseModel):
    # A UTC calendar day.
    date: date
    clicks: int


class ClickOfferData(BaseModel):
    offer_id: str
    slug: str
    title: str
    total_clicks: int
    # Only days with at least one click, oldest first; empty for an offer with no clicks.
    days: list[ClickDayData]


class ClickRetention(BaseModel):
    days: int
    # The first UTC day all of whose clicks are still kept; earlier days may be incomplete.
    earliest_complete_date: date
    starts_before_retention: bool


class ClickBasis(BaseModel):
    definition: str
    bot_rule: str


class MerchantClicksData(BaseModel):
    merchant: ClickMerchant
    from_date: date
    to_date: date
    # Over every offer of the merchant, also those beyond the offer limit.
    total_clicks: int
    excluded_bot_clicks: int
    # Offers that qualify for the report, before the limit; offers_truncated is true when it cut some.
    offers_total: int
    max_offers: int
    offers_truncated: bool
    offers: list[ClickOfferData]
    retention: ClickRetention
    basis: ClickBasis


class MerchantClicksResponse(BaseModel):
    success: Literal[True] = True
    data: MerchantClicksData


def clicks_response(report: ClickReport) -> MerchantClicksResponse:
    return MerchantClicksResponse(
        data=MerchantClicksData(
            merchant=ClickMerchant(
                id=report.merchant.id,
                name=report.merchant.name,
                slug=report.merchant.slug,
            ),
            from_date=report.from_date,
            to_date=report.to_date,
            total_clicks=report.total_clicks,
            excluded_bot_clicks=report.excluded_bot_clicks,
            offers_total=report.offers_total,
            max_offers=report.max_offers,
            offers_truncated=report.offers_truncated,
            offers=[
                ClickOfferData(
                    offer_id=offer.offer_id,
                    slug=offer.slug,
                    title=offer.title,
                    total_clicks=offer.total_clicks,
                    days=[
                        ClickDayData(date=item.day, clicks=item.clicks)
                        for item in offer.days
                    ],
                )
                for offer in report.offers
            ],
            retention=ClickRetention(
                days=report.retention_days,
                earliest_complete_date=report.earliest_complete_date,
                starts_before_retention=report.starts_before_retention,
            ),
            basis=ClickBasis(definition=CLICK_DEFINITION, bot_rule=BOT_RULE),
        )
    )
