from fastapi import APIRouter

from app.api.v1 import (
    admin_analytics,
    admin_audit_logs,
    admin_categories,
    admin_merchants,
    admin_offers,
    admin_partner_keys,
    admin_subscriptions,
    admin_users,
    auth,
    catalogue,
    catalogue_click,
    health,
    me,
    me_export,
    notifications,
    org_billing,
    organizations,
    settings,
    subscribers,
    totp,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(me_export.router)
api_router.include_router(notifications.router)
api_router.include_router(totp.router)
api_router.include_router(admin_users.router)
api_router.include_router(admin_audit_logs.router)
api_router.include_router(admin_analytics.router)
api_router.include_router(admin_categories.router)
api_router.include_router(admin_merchants.router)
api_router.include_router(admin_offers.router)
api_router.include_router(admin_partner_keys.router)
api_router.include_router(organizations.router)
api_router.include_router(settings.router)
api_router.include_router(catalogue.router)
api_router.include_router(catalogue_click.router)
api_router.include_router(subscribers.router)

# Stripe Billing module (docs/developer/modules.md): remove these three lines with the module.
api_router.include_router(webhooks.router)
api_router.include_router(admin_subscriptions.router)
api_router.include_router(org_billing.router)
