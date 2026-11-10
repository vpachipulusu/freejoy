"""Platform database models (ADR-004).

Importing this package registers every table on `Base.metadata`, which Alembic
autogenerate and the metadata test rely on.
"""

from app.models.platform.admin_user import AdminUser
from app.models.platform.affiliate_link import AffiliateLink
from app.models.platform.app_setting import AppSetting
from app.models.platform.audit_log import AuditLog
from app.models.platform.catalogue_import import MerchantImport, OfferImport
from app.models.platform.category import Category
from app.models.platform.click_event import ClickEvent, UserAgentClass
from app.models.platform.email_subscriber import (
    AlertFrequency,
    EmailSubscriber,
    SubscriberStatus,
)
from app.models.platform.email_template import EmailTemplate
from app.models.platform.feature_flag import FeatureFlag
from app.models.platform.invitation import Invitation, InvitationStatus
from app.models.platform.membership import Membership, MembershipRole
from app.models.platform.merchant import Merchant
from app.models.platform.network_report import CommissionStatus, NetworkReportRow
from app.models.platform.offer import (
    Offer,
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
)
from app.models.platform.organization import Organization
from app.models.platform.seo_meta import SeoMeta
from app.models.platform.stripe_event import StripeEvent
from app.models.platform.url_redirect import UrlRedirect
from app.models.platform.user import LoginHistory, Notification, User, UserSettings

__all__ = [
    "AdminUser",
    "AffiliateLink",
    "AlertFrequency",
    "AppSetting",
    "AuditLog",
    "Category",
    "ClickEvent",
    "CommissionStatus",
    "EmailSubscriber",
    "EmailTemplate",
    "FeatureFlag",
    "Invitation",
    "InvitationStatus",
    "LoginHistory",
    "Membership",
    "MembershipRole",
    "Merchant",
    "MerchantImport",
    "NetworkReportRow",
    "Notification",
    "Offer",
    "OfferFreshness",
    "OfferImport",
    "OfferStatus",
    "OfferType",
    "Organization",
    "PostageKind",
    "SeoMeta",
    "StripeEvent",
    "SubscriberStatus",
    "UrlRedirect",
    "User",
    "UserAgentClass",
    "UserSettings",
]
