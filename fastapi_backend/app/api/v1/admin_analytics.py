"""Platform admin analytics API: overview, revenue, system stats, Stripe sync.

Every route is ``require_admin(<permission>)``: authentication first (401), then the
admin record and permission (403, one generic body). The overview needs ``users.read``,
revenue ``subscriptions.read``, system stats ``settings.read`` and the Stripe sync
``subscriptions.sync``, the merchant click report ``offers.read`` (it is about a merchant's offers, like the
merchants API the picker reads). Responses carry counts and money totals only, never personal
data, and are never cached. The sync reports drift and writes one audit row; see
``SubscriptionSyncService``.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app.api.admin import AuditorDep, require_admin
from app.api.auth import require_internal_key
from app.api.deps import SessionDep
from app.api.rate_limit import RATE_LIMIT_FAIL_CLOSED_RESPONSES, limit_by_subject
from app.api.v1.admin_analytics_clicks import (
    IsoDay,
    MerchantClicksResponse,
    clicks_response,
)
from app.api.v1.admin_catalogue_common import (
    CatalogueErrorResponse,
    catalogue_http_error,
)
from app.api.v1.auth import current_subject
from app.core.config import settings
from app.core.errors import CodedHTTPException, ErrorResponse
from app.core.permissions import AdminContext
from app.core.plans import PlanKey
from app.core.rate_limit import ADMIN_SYNC_POLICY, API_POLICY
from app.core.stripe_prices import StripePriceDetailsReader, get_stripe_prices
from app.core.stripe_sync import (
    StripeSubscriptionLister,
    get_stripe_subscription_lister,
)
from app.repositories.admin_analytics import AdminAnalyticsRepository
from app.repositories.app_settings import AppSettingsRepository
from app.repositories.merchant_clicks import MerchantClickRepository
from app.services.admin_analytics import AdminAnalyticsService, PriceEntry
from app.services.admin_catalogue_errors import CatalogueError
from app.services.admin_subscription_sync import (
    SubscriptionSyncService,
    SyncNotConfigured,
    SyncUnavailable,
)
from app.services.merchant_click_report import (
    InvalidClickRange,
    MerchantClickReportService,
)
from app.services.price_cache import SHARED_PRICE_CACHE, CachedPriceReader
from app.services.public_settings import PublicSettingsService

router = APIRouter(
    prefix="/v1/admin/analytics",
    tags=["admin-analytics"],
    dependencies=[
        Depends(require_internal_key),
        Depends(limit_by_subject(API_POLICY, current_subject)),
    ],
    responses={
        **RATE_LIMIT_FAIL_CLOSED_RESPONSES,
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)

UsersAdmin = Annotated[AdminContext, Depends(require_admin("users.read"))]
SubscriptionsAdmin = Annotated[
    AdminContext, Depends(require_admin("subscriptions.read"))
]
SettingsAdmin = Annotated[AdminContext, Depends(require_admin("settings.read"))]
SyncAdmin = Annotated[AdminContext, Depends(require_admin("subscriptions.sync"))]
OffersAdmin = Annotated[AdminContext, Depends(require_admin("offers.read"))]


class UsersOverview(BaseModel):
    total: int
    verified: int
    new_this_month: int
    active_last_30_days: int
    admins: int


class OrganizationsOverview(BaseModel):
    total: int
    new_this_month: int


class OverviewData(BaseModel):
    users: UsersOverview
    organizations: OrganizationsOverview


class OverviewResponse(BaseModel):
    success: Literal[True] = True
    data: OverviewData


class PlanRevenueData(BaseModel):
    plan: PlanKey
    organizations: int
    mrr_minor: int


class RevenueData(BaseModel):
    currency: str
    # Minor units (cents). Billed = active or past due; trials are not revenue yet.
    mrr_minor: int
    arr_minor: int
    billed_organizations: int
    unpriced_organizations: int
    # False when some billed organisation could not be priced: the totals are a floor.
    complete: bool
    by_plan: list[PlanRevenueData]
    organizations_by_status: dict[str, int]
    organizations_by_tier: dict[str, int]
    cancelling_at_period_end: int
    active_grants: int


class RevenueResponse(BaseModel):
    success: Literal[True] = True
    data: RevenueData


class SystemData(BaseModel):
    app_settings: int
    feature_flags: int
    email_templates: int
    seo_entries: int
    redirects: int
    audit_logs: int
    stripe_events: int
    admin_users: int


class SystemResponse(BaseModel):
    success: Literal[True] = True
    data: SystemData


DriftKindName = Literal[
    "unmatched",
    "subscription_id",
    "status",
    "plan",
    "cancel_at_period_end",
    "missing_in_stripe",
]


class DriftData(BaseModel):
    kind: DriftKindName
    organization_id: str | None
    subscription_id: str | None
    stripe_status: str | None
    local_status: str | None


class SyncData(BaseModel):
    stripe_subscriptions: int
    in_sync: int
    drift_count: int
    historical_ignored: int
    # True when Stripe had more subscriptions than one sync reads.
    truncated: bool
    # Stripe rows that could not be read; the not-found check is skipped when > 0.
    skipped_rows: int
    drifts: list[DriftData]


class SyncResponse(BaseModel):
    success: Literal[True] = True
    data: SyncData


def price_entries() -> dict[str, PriceEntry]:
    """Stripe price id to plan and interval, from settings; unset prices are left out."""
    configured = {
        settings.STRIPE_PRICE_ID_PRO_MONTHLY: PriceEntry(PlanKey.PRO, "month"),
        settings.STRIPE_PRICE_ID_PRO_YEARLY: PriceEntry(PlanKey.PRO, "year"),
        settings.STRIPE_PRICE_ID_ENTERPRISE_MONTHLY: PriceEntry(
            PlanKey.ENTERPRISE, "month"
        ),
        settings.STRIPE_PRICE_ID_ENTERPRISE_YEARLY: PriceEntry(
            PlanKey.ENTERPRISE, "year"
        ),
    }
    return {price: entry for price, entry in configured.items() if price}


def stripe_prices() -> StripePriceDetailsReader | None:
    """Price reads, reused for a few minutes across requests (see ``price_cache``)."""
    reader = get_stripe_prices()
    return CachedPriceReader(reader, SHARED_PRICE_CACHE) if reader else None


def get_analytics_service(
    session: SessionDep,
    prices: Annotated[StripePriceDetailsReader | None, Depends(stripe_prices)],
) -> AdminAnalyticsService:
    return AdminAnalyticsService(
        AdminAnalyticsRepository(session),
        prices,
        price_entries(),
        PublicSettingsService(AppSettingsRepository(session)),
        session.rollback,
    )


async def stripe_lister() -> AsyncIterator[StripeSubscriptionLister | None]:
    """One HTTP client for all the pages of a sync, closed when the request ends."""
    if get_stripe_subscription_lister() is None:
        yield None
        return
    async with httpx.AsyncClient() as client:
        yield get_stripe_subscription_lister(client)


def get_sync_service(
    session: SessionDep,
    auditor: AuditorDep,
    lister: Annotated[StripeSubscriptionLister | None, Depends(stripe_lister)],
) -> SubscriptionSyncService:
    plans = {price: entry.plan for price, entry in price_entries().items()}
    return SubscriptionSyncService(
        AdminAnalyticsRepository(session), session, lister, auditor, plans
    )


def get_click_report_service(session: SessionDep) -> MerchantClickReportService:
    return MerchantClickReportService(MerchantClickRepository(session))


AnalyticsDep = Annotated[AdminAnalyticsService, Depends(get_analytics_service)]
ClickReportDep = Annotated[
    MerchantClickReportService, Depends(get_click_report_service)
]
SyncDep = Annotated[SubscriptionSyncService, Depends(get_sync_service)]


NO_STORE_HEADERS = {"Cache-Control": "no-store"}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/overview", response_model=OverviewResponse)
async def admin_overview(
    response: Response, _admin: UsersAdmin, service: AnalyticsDep
) -> OverviewResponse:
    _no_store(response)
    counts = await service.overview()
    return OverviewResponse(
        data=OverviewData(
            users=UsersOverview(
                total=counts.users_total,
                verified=counts.users_verified,
                new_this_month=counts.users_new,
                active_last_30_days=counts.users_active,
                admins=counts.admins,
            ),
            organizations=OrganizationsOverview(
                total=counts.organizations_total,
                new_this_month=counts.organizations_new,
            ),
        )
    )


@router.get("/revenue", response_model=RevenueResponse)
async def admin_revenue(
    response: Response, _admin: SubscriptionsAdmin, service: AnalyticsDep
) -> RevenueResponse:
    _no_store(response)
    report = await service.revenue()
    return RevenueResponse(
        data=RevenueData(
            currency=report.currency,
            mrr_minor=report.mrr_minor,
            arr_minor=report.arr_minor,
            billed_organizations=report.billed_organizations,
            unpriced_organizations=report.unpriced_organizations,
            complete=report.complete,
            by_plan=[
                PlanRevenueData(
                    plan=item.plan,
                    organizations=item.organizations,
                    mrr_minor=item.mrr_minor,
                )
                for item in report.by_plan
            ],
            organizations_by_status=report.breakdown.by_status,
            organizations_by_tier=report.breakdown.by_tier,
            cancelling_at_period_end=report.breakdown.canceling,
            active_grants=report.breakdown.active_grants,
        )
    )


@router.get("/system", response_model=SystemResponse)
async def admin_system_stats(
    response: Response, _admin: SettingsAdmin, service: AnalyticsDep
) -> SystemResponse:
    _no_store(response)
    counts = await service.system()
    return SystemResponse(data=SystemData(**vars(counts)))


@router.get(
    "/clicks",
    response_model=MerchantClicksResponse,
    summary="Clicks per offer per day for one merchant",
    description=(
        "Outbound clicks on one merchant's offers over a range of UTC days (inclusive, from <= to, at "
        "most 366 days), per offer and per day, for sending to a retailer. A click is a visitor sent "
        "to the destination: not a sale, not a unique person. Bot-classed clicks are left out and "
        "counted apart. At most 100 offers are returned (offers_truncated says when more exist). "
        "Click records are kept for 395 days; the answer says when a range reaches past them."
    ),
    responses={
        404: {"model": CatalogueErrorResponse},
        422: {"model": CatalogueErrorResponse, "description": "Invalid request"},
    },
)
async def admin_merchant_clicks(
    response: Response,
    _admin: OffersAdmin,
    service: ClickReportDep,
    merchant_id: Annotated[
        str, Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ],
    date_from: Annotated[IsoDay, Query(alias="from")],
    date_to: Annotated[IsoDay, Query(alias="to")],
) -> MerchantClicksResponse:
    _no_store(response)
    try:
        report = await service.report(merchant_id, date_from, date_to)
    except InvalidClickRange as exc:
        raise CodedHTTPException(
            422, str(exc), "invalid_range", headers=NO_STORE_HEADERS
        ) from None
    except CatalogueError as exc:
        refusal = catalogue_http_error(exc)
        refusal.headers = NO_STORE_HEADERS
        raise refusal from None
    return clicks_response(report)


@router.post(
    "/subscription-sync",
    response_model=SyncResponse,
    summary="Compare Stripe subscriptions with stored billing state",
    description=(
        "Reads every Stripe subscription and reports where it differs from the organisations' "
        "stored state. Changes no subscription; writes one audit row. Limited to 3 runs a minute."
    ),
    dependencies=[Depends(limit_by_subject(ADMIN_SYNC_POLICY, current_subject))],
    responses={502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def admin_sync_subscriptions(
    response: Response, admin: SyncAdmin, service: SyncDep
) -> SyncResponse:
    _no_store(response)
    try:
        report = await service.reconcile(admin)
    except SyncNotConfigured:
        raise HTTPException(503, "Billing is not configured") from None
    except SyncUnavailable:
        raise HTTPException(502, "Billing provider unavailable") from None
    return SyncResponse(
        data=SyncData(
            stripe_subscriptions=report.stripe_subscriptions,
            in_sync=report.in_sync,
            drift_count=report.drift_count,
            historical_ignored=report.historical_ignored,
            truncated=report.truncated,
            skipped_rows=report.skipped,
            drifts=[
                DriftData(
                    kind=item.kind,
                    organization_id=item.organization_id,
                    subscription_id=item.subscription_id,
                    stripe_status=item.stripe_status,
                    local_status=item.local_status,
                )
                for item in report.drifts
            ],
        )
    )
