from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Table, UniqueConstraint

import app.models.platform  # noqa: F401  (registers the tables)
from app.core.base import Base
from app.models.platform import (
    AlertFrequency,
    MembershipRole,
    OfferFreshness,
    OfferStatus,
    OfferType,
    PostageKind,
    SubscriberStatus,
    UserAgentClass,
)

EXPECTED_TABLES = {
    "users",
    "user_settings",
    "login_history",
    "notifications",
    "admin_users",
    "audit_logs",
    "seo_meta",
    "url_redirects",
    "app_settings",
    "feature_flags",
    "email_templates",
    "organizations",
    "memberships",
    "invitations",
    # billing webhook (Stripe Billing module)
    "stripe_events",
    # Freejoy catalogue
    "categories",
    "merchants",
    "offers",
    "affiliate_links",
    "click_events",
    "email_subscribers",
    # Awin importer provenance
    "offer_imports",
    "merchant_imports",
    # network report ingest
    "network_report_rows",
}

BILLING_COLUMNS = {
    "stripe_customer_id",
    "stripe_subscription_id",
    "stripe_price_id",
    "stripe_current_period_end",
    "subscription_tier",
    "subscription_status",
}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def column_sets(items: Any) -> set[tuple[str, ...]]:
    return {tuple(column.name for column in item.columns) for item in items}


def test_metadata_contains_exactly_the_platform_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_billing_lives_on_organizations_not_users():
    assert BILLING_COLUMNS <= set(table("organizations").columns.keys())
    assert not BILLING_COLUMNS & set(table("users").columns.keys())


def test_membership_is_unique_per_organization_and_user():
    uniques = [
        c for c in table("memberships").constraints if isinstance(c, UniqueConstraint)
    ]

    assert ("organization_id", "user_id") in column_sets(uniques)


def test_roles_are_the_decided_values():
    assert [role.value for role in MembershipRole] == ["OWNER", "ADMIN", "STAFF"]


def test_audit_logs_index_organization_by_time():
    audit = table("audit_logs")

    assert audit.columns["organization_id"].nullable
    assert ("organization_id", "created_at") in column_sets(audit.indexes)


def test_primary_keys_are_generated_string_ids():
    for name, tbl in Base.metadata.tables.items():
        key = tbl.primary_key.columns["id"]
        assert key.type.python_type is str, name
        assert key.default is not None, name


def test_every_datetime_column_is_timezone_aware():
    for name, tbl in Base.metadata.tables.items():
        for column in tbl.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone, f"{name}.{column.name}"


def test_constraints_follow_the_naming_convention():
    assert table("memberships").primary_key.name == "pk_memberships"


def check_names(name: str) -> set[str]:
    return {
        str(c.name)
        for c in table(name).constraints
        if isinstance(c, CheckConstraint) and c.name
    }


def foreign_key(tbl: str, column: str) -> ForeignKey:
    (key,) = table(tbl).columns[column].foreign_keys
    return key


# --- Freejoy catalogue ---


def test_catalogue_slugs_and_subscriber_email_are_unique():
    for name, column in [
        ("categories", "slug"),
        ("merchants", "slug"),
        ("offers", "slug"),
        ("email_subscribers", "email"),
        ("email_subscribers", "confirmation_token_hash"),
    ]:
        uniques = [
            c for c in table(name).constraints if isinstance(c, UniqueConstraint)
        ]

        assert (column,) in column_sets(uniques), f"{name}.{column}"


def test_enums_are_the_decided_values():
    assert [m.value for m in OfferType] == [
        "FREE_SAMPLE",
        "FREE_TRIAL",
        "CASHBACK",
        "VOUCHER",
        "COMPETITION",
    ]
    assert [m.value for m in OfferStatus] == [
        "DRAFT",
        "PUBLISHED",
        "EXPIRED",
        "ARCHIVED",
    ]
    assert [m.value for m in OfferFreshness] == [
        "NEW_TODAY",
        "JUST_ADDED",
        "POPULAR",
        "ENDS_SOON",
        "LIMITED",
    ]
    assert [m.value for m in PostageKind] == ["FREE", "COST", "CHECK_TERMS"]
    assert [m.value for m in UserAgentClass] == [
        "DESKTOP",
        "MOBILE",
        "TABLET",
        "BOT",
        "OTHER",
    ]
    assert [m.value for m in SubscriberStatus] == [
        "PENDING",
        "CONFIRMED",
        "UNSUBSCRIBED",
    ]
    assert [m.value for m in AlertFrequency] == ["DAILY", "WEEKLY"]


def test_every_catalogue_enum_column_has_a_check_constraint():
    expected = {
        "offers": {"offer_type", "status", "postage_kind", "freshness"},
        "click_events": {"user_agent_class"},
        "email_subscribers": {"status", "frequency"},
    }

    for name, columns in expected.items():
        for column in columns:
            assert f"ck_{name}_{column}_valid" in check_names(name), f"{name}.{column}"


def test_offers_indexes_serve_listing_and_category_pages():
    offers = table("offers")

    assert ("status", "end_at") in column_sets(offers.indexes)
    assert ("category_id", "status") in column_sets(offers.indexes)
    assert {i.name for i in offers.indexes} >= {
        "ix_offers_status_end_at",
        "ix_offers_category_id_status",
    }


def test_affiliate_links_index_offer_by_active():
    assert ("offer_id", "active") in column_sets(table("affiliate_links").indexes)


def test_offers_postage_amount_and_currency_only_for_cost():
    assert "ck_offers_postage_cost_complete" in check_names("offers")
    offers = table("offers")

    assert offers.columns["postage_amount_minor"].nullable
    assert offers.columns["postage_currency"].nullable


def test_published_offers_must_carry_a_verification_date():
    assert "ck_offers_published_is_verified" in check_names("offers")


def test_subscriber_email_is_stored_lower_case():
    assert "ck_email_subscribers_email_lowercase" in check_names("email_subscribers")


def test_foreign_key_delete_behaviour():
    assert foreign_key("offers", "category_id").ondelete == "RESTRICT"
    assert foreign_key("offers", "merchant_id").ondelete == "RESTRICT"
    assert foreign_key("affiliate_links", "offer_id").ondelete == "CASCADE"
    assert foreign_key("click_events", "offer_id").ondelete == "CASCADE"
    assert foreign_key("click_events", "affiliate_link_id").ondelete == "SET NULL"
    assert table("click_events").columns["affiliate_link_id"].nullable
    # SET NULL runs on every link delete, so the column needs an index.
    assert ("affiliate_link_id",) in column_sets(table("click_events").indexes)


def test_catalogue_defaults_are_server_side():
    expectations = [
        ("categories", "sort_order"),
        ("categories", "enabled"),
        ("merchants", "active"),
        ("offers", "status"),
        ("offers", "featured"),
        ("offers", "requires_card"),
        ("offers", "description"),
        ("offers", "steps"),
        ("affiliate_links", "active"),
        ("click_events", "occurred_at"),
        ("email_subscribers", "status"),
        ("email_subscribers", "categories"),
    ]

    for name, column in expectations:
        assert table(name).columns[column].server_default is not None, (
            f"{name}.{column}"
        )


# Privacy rule: a click can be counted but never tied to a person. Adding a column means
# changing this test and the click-events section of docs/developer/platform-models.md
# together, after asking whether it could identify a visitor.
ANONYMOUS_CLICK_COLUMNS = {
    "id",
    "offer_id",
    "affiliate_link_id",
    "occurred_at",
    "referrer_host",
    "user_agent_class",
}


def test_click_events_columns_are_exactly_the_anonymous_set():
    assert set(table("click_events").columns.keys()) == ANONYMOUS_CLICK_COLUMNS


def test_subscriber_stores_a_token_hash_not_a_token():
    columns = table("email_subscribers").columns.keys()

    assert "confirmation_token_hash" in columns
    assert "confirmation_sent_at" in columns
    assert "confirmation_token" not in columns
