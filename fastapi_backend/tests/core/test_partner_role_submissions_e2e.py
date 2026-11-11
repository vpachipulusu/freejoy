"""End to end: what the restricted partner role can and cannot do with offers and the audit trail (FREEJ-58).

Same setup as ``test_partner_role_e2e.py`` (the committed script, applied to a throwaway ``*_test`` database, connected
to as ``freejoy_partner_<database>``). The point of this file: the role can create and refresh DRAFT offers and end
them, and there is no sequence of statements it can run that publishes, verifies, features or re-bases one, or that
forges an audit row.
"""

import json
import os
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

import tests.core.test_partner_role_e2e as base
from app.core.ids import new_id
from tests.partner_role_support import apply_partner_role, role_password_for

# The fixtures and the refusal helper of the role suite, re-bound here so pytest finds them in this module.
database_url, owner, partner, partner_url, role = (
    base.database_url,
    base.owner,
    base.partner,
    base.partner_url,
    base.role,
)
denied = base.denied

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)

KEY_ID = "k" * 24
OFFER_COLUMNS = (
    "id, title, slug, summary, description, category_id, merchant_id, offer_type, image_tint, image_label, "
    "image_alt, postage_kind, requires_card, eligibility, availability, steps"
)


def offer_values(
    offer_id: str, merchant_id: str, category_id: str
) -> dict[str, object]:
    return {
        "id": offer_id,
        "title": "Free sample",
        "slug": f"free-sample-{offer_id}",
        "summary": "A sample",
        "description": json.dumps(["One paragraph"]),
        "category": category_id,
        "merchant": merchant_id,
        "kind": "FREE_SAMPLE",
        "tint": "cream",
        "label": "Shop",
        "alt": "Illustration of an offer",
        "postage": "FREE",
        "card": False,
        "eligibility": "UK",
        "availability": "While stocks last",
        "steps": json.dumps([]),
    }


INSERT_OFFER = (
    f"INSERT INTO offers ({OFFER_COLUMNS}) VALUES (:id, :title, :slug, :summary, CAST(:description AS jsonb), "
    ":category, :merchant, :kind, :tint, :label, :alt, :postage, :card, :eligibility, :availability, "
    "CAST(:steps AS jsonb))"
)


async def seed(owner: AsyncEngine) -> tuple[str, str]:
    """A merchant and an enabled category; returns (merchant id, category id)."""
    merchant_id, category_id = new_id(), new_id()
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO merchants (id, name, slug, website, active) "
                "VALUES (:id, 'Shop', :slug, 'https://shop.example.com', true)"
            ),
            {"id": merchant_id, "slug": f"shop-{merchant_id}"},
        )
        await connection.execute(
            text(
                "INSERT INTO categories (id, name, slug, short_name, description, intro, tint, enabled) "
                "VALUES (:id, 'Home', :slug, 'Home', 'd', 'i', 'sage', true)"
            ),
            {"id": category_id, "slug": f"home-{category_id}"},
        )
    return merchant_id, category_id


async def run(engine: AsyncEngine, sql: str, **params: object) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(sql), params)


async def fetch_one(engine: AsyncEngine, sql: str, **params: object):  # type: ignore[no-untyped-def]
    async with engine.connect() as connection:
        return (await connection.execute(text(sql), params)).one()


async def refusal(engine: AsyncEngine, sql: str, **params: object) -> str:
    """The lower-cased database error message of ``sql`` run as the role (``""`` when it was allowed)."""
    try:
        await run(engine, sql, **params)
    except DBAPIError as error:
        return str(error.orig).lower()
    return ""


async def partner_offer(
    owner: AsyncEngine, partner: AsyncEngine, *, submitted: bool = True
) -> tuple[str, str, str]:
    """Insert an offer, and by default its submission, as the role, in the order the guards need.

    The offer comes first and the submission second (a submission can only be attached to a new draft of its own
    merchant). A test that inserts the submission itself passes ``submitted=False``.
    Returns (offer id, merchant id, category id).
    """
    merchant_id, category_id = await seed(owner)
    offer_id = new_id()
    await run(partner, INSERT_OFFER, **offer_values(offer_id, merchant_id, category_id))
    if submitted:
        await run(
            partner,
            "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
            "VALUES (:i, :m, :r, :o, :h, :k)",
            i=new_id(),
            m=merchant_id,
            r=f"ref-{offer_id}",
            o=offer_id,
            h="a" * 64,
            k=KEY_ID,
        )
    return offer_id, merchant_id, category_id


# ------------------------------------------------------------------------------------- a new offer is a DRAFT


async def test_an_offer_the_role_inserts_is_an_unverified_unfeatured_draft_with_a_placeholder_basis(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)

    row = await fetch_one(
        owner,
        "SELECT status, featured, last_verified_at, commercial_basis, freshness, publisher_notes "
        "FROM offers WHERE id = :id",
        id=offer_id,
    )

    assert tuple(row) == ("DRAFT", False, None, "SPONSORED", None, None)


async def test_the_owner_still_has_to_state_the_commercial_basis(
    owner: AsyncEngine,
) -> None:
    merchant_id, category_id = await seed(owner)

    message = await refusal(
        owner, INSERT_OFFER, **offer_values(new_id(), merchant_id, category_id)
    )

    assert "commercial_basis" in message
    assert "null value" in message


@pytest.mark.parametrize(
    "column_and_value",
    [
        "status, 'PUBLISHED'",
        "status, 'DRAFT'",
        "featured, true",
        "last_verified_at, now()",
        "commercial_basis, 'SPONSORED'",
        "freshness, 'POPULAR'",
        "publisher_notes, 'note'",
        "source_url, 'https://shop.example.com/'",
    ],
)
async def test_an_insert_that_names_status_featured_verification_or_basis_is_refused(
    owner: AsyncEngine, partner: AsyncEngine, column_and_value: str
) -> None:
    merchant_id, category_id = await seed(owner)
    column, value = column_and_value.split(", ", 1)
    statement = INSERT_OFFER.replace(
        "INSERT INTO offers (", f"INSERT INTO offers ({column}, "
    ).replace("VALUES (", f"VALUES ({value}, ")

    message = await refusal(
        partner, statement, **offer_values(new_id(), merchant_id, category_id)
    )

    assert "permission denied" in message
    count = await fetch_one(owner, "SELECT count(*) FROM offers")
    assert count[0] == 0


@pytest.mark.parametrize(
    "assignment",
    [
        "status = 'PUBLISHED'",
        "status = 'DRAFT'",
        "featured = true",
        "last_verified_at = now()",
        "commercial_basis = 'SPONSORED'",
        "freshness = 'POPULAR'",
        "publisher_notes = 'x'",
        "slug = 'taken-over'",
        "merchant_id = 'x'",
        "source_url = 'https://shop.example.com/'",
        "image_tint = 'sage'",
    ],
)
async def test_an_update_of_a_column_an_editor_owns_is_refused(
    owner: AsyncEngine, partner: AsyncEngine, assignment: str
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)

    message = await refusal(
        partner, f"UPDATE offers SET {assignment} WHERE id = :id", id=offer_id
    )

    assert "permission denied" in message


async def test_no_statement_of_the_role_publishes_verifies_or_deletes_an_offer(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)

    for statement in (
        "UPDATE offers SET status = 'PUBLISHED', last_verified_at = now()",
        "DELETE FROM offers",
        "TRUNCATE offers",
        "UPDATE offers SET end_at = now(), status = 'EXPIRED'",
    ):
        assert await denied(partner, statement), statement
    row = await fetch_one(
        owner, "SELECT status, last_verified_at FROM offers WHERE id = :id", id=offer_id
    )
    assert tuple(row) == ("DRAFT", None)


async def test_the_role_may_refresh_the_content_and_end_an_offer(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)

    await run(
        partner,
        "UPDATE offers SET title = 'New', end_at = now(), updated_at = now() WHERE id = :id",
        id=offer_id,
    )
    row = await fetch_one(
        owner, "SELECT title, end_at, status FROM offers WHERE id = :id", id=offer_id
    )

    assert (row.title, row.status) == ("New", "DRAFT")
    assert row.end_at is not None


# ------------------------------------------------------------------------------------------------- links


async def test_a_link_the_role_inserts_is_a_direct_active_link_and_the_provider_cannot_be_named(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)
    link_id = new_id()

    await run(
        partner,
        "INSERT INTO affiliate_links (id, offer_id, destination_url) VALUES (:i, :o, 'https://shop.example.com/x')",
        i=link_id,
        o=offer_id,
    )
    row = await fetch_one(
        owner,
        "SELECT provider, active, tracking_id FROM affiliate_links WHERE id = :i",
        i=link_id,
    )
    named = await refusal(
        partner,
        "INSERT INTO affiliate_links (id, offer_id, provider, destination_url) "
        "VALUES (:i, :o, 'awin', 'https://www.awin1.com/x')",
        i=new_id(),
        o=offer_id,
    )

    assert tuple(row) == ("direct", True, None)
    assert "permission denied" in named


@pytest.mark.parametrize(
    "assignment",
    ["provider = 'awin'", "active = false", "tracking_id = 'x'", "offer_id = 'x'"],
)
async def test_a_link_can_only_get_a_new_destination(
    owner: AsyncEngine, partner: AsyncEngine, assignment: str
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)
    link_id = new_id()
    await run(
        partner,
        "INSERT INTO affiliate_links (id, offer_id, destination_url) VALUES (:i, :o, 'https://shop.example.com/x')",
        i=link_id,
        o=offer_id,
    )

    refused = await refusal(
        partner, f"UPDATE affiliate_links SET {assignment} WHERE id = :i", i=link_id
    )
    await run(
        partner,
        "UPDATE affiliate_links SET destination_url = 'https://shop.example.com/y', updated_at = now() WHERE id = :i",
        i=link_id,
    )

    assert "permission denied" in refused
    assert await denied(partner, "DELETE FROM affiliate_links")


# --------------------------------------------------------------------------------------------- the audit trail


AUDIT_INSERT = (
    "INSERT INTO audit_logs (id, action, resource, resource_id, metadata) "
    "VALUES (:id, :action, 'offers', 'o1', CAST('{}' AS jsonb))"
)


@pytest.mark.parametrize(
    "action", ["partner.offers.submit", "partner.offers.update", "partner.offers.end"]
)
async def test_the_role_can_append_a_partner_audit_row_with_no_user(
    owner: AsyncEngine, partner: AsyncEngine, action: str
) -> None:
    audit_id = new_id()

    await run(partner, AUDIT_INSERT, id=audit_id, action=action)
    row = await fetch_one(
        owner,
        "SELECT user_id, organization_id, action, ip_address, user_agent, created_at IS NOT NULL "
        "FROM audit_logs WHERE id = :id",
        id=audit_id,
    )

    assert tuple(row) == (None, None, action, None, None, True)


@pytest.mark.parametrize(
    "action",
    [
        "admin.offers.publish",
        "admin.partner_keys.create",
        "offers.publish",
        "partner",
        "partner.offers",
        "partner.Offers.submit",
        "partner.offers.submit.extra",
        "xpartner.offers.submit",
        "partner.offers.submit\n",
        "",
    ],
)
async def test_the_role_cannot_forge_an_admin_row_or_any_row_that_is_not_a_partner_action(
    owner: AsyncEngine, partner: AsyncEngine, action: str
) -> None:
    message = await refusal(partner, AUDIT_INSERT, id=new_id(), action=action)

    assert "this writer may only append partner audit rows" in message
    count = await fetch_one(owner, "SELECT count(*) FROM audit_logs")
    assert count[0] == 0


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO audit_logs (id, user_id, action, resource) VALUES ('x', 'u1', 'partner.offers.submit', 'offers')",
        "INSERT INTO audit_logs (id, organization_id, action, resource) VALUES ('x', 'o1', 'partner.offers.submit', 'offers')",
        "INSERT INTO audit_logs (id, action, resource, ip_address) VALUES ('x', 'partner.offers.submit', 'offers', '1.2.3.4')",
        "INSERT INTO audit_logs (id, action, resource, created_at) VALUES ('x', 'partner.offers.submit', 'offers', now())",
        "SELECT 1 FROM audit_logs",
        "SELECT id FROM audit_logs",
        "INSERT INTO audit_logs (id, action, resource) VALUES ('x', 'partner.offers.submit', 'offers') RETURNING id",
        "UPDATE audit_logs SET action = 'partner.offers.submit'",
        "UPDATE audit_logs SET metadata = '{}'",
        "DELETE FROM audit_logs",
        "TRUNCATE audit_logs",
    ],
)
async def test_the_role_cannot_name_a_user_read_change_or_delete_the_trail(
    partner: AsyncEngine, statement: str
) -> None:
    assert await denied(partner, statement)


async def test_the_guard_does_not_touch_the_owner_so_admin_rows_are_written_as_before(
    owner: AsyncEngine,
) -> None:
    await run(
        owner,
        "INSERT INTO audit_logs (id, user_id, organization_id, action, resource) "
        "VALUES ('a1', 'u1', 'org1', 'admin.offers.publish', 'offers')",
    )

    row = await fetch_one(owner, "SELECT action FROM audit_logs WHERE id = 'a1'")

    assert row[0] == "admin.offers.publish"


# ------------------------------------------------------------------------- submissions and provenance


async def insert_submission(
    partner: AsyncEngine, offer_id: str, merchant_id: str, ref: str = "ref-1"
) -> str:
    """Attach a submission to an offer the role just inserted (key id of the right shape)."""
    submission_id = new_id()
    await run(
        partner,
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "VALUES (:i, :m, :r, :o, :h, :k)",
        k=KEY_ID,
        i=submission_id,
        m=merchant_id,
        r=ref,
        o=offer_id,
        h="a" * 64,
    )
    return submission_id


async def test_a_ref_is_unique_per_merchant_and_an_offer_has_one_submission(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, merchant_id, category_id = await partner_offer(
        owner, partner, submitted=False
    )
    await run(
        partner,
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "VALUES (:i, :m, 'ref-1', :o, :h, :k)",
        i=new_id(),
        m=merchant_id,
        o=offer_id,
        h="a" * 64,
        k=KEY_ID,
    )
    other_offer = new_id()
    await run(
        partner, INSERT_OFFER, **offer_values(other_offer, merchant_id, category_id)
    )

    same_ref = await refusal(
        partner,
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "VALUES (:i, :m, 'ref-1', :o, :h, 'kkkkkkkkkkkkkkkkkkkkkkkk')",
        i=new_id(),
        m=merchant_id,
        o=other_offer,
        h="a" * 64,
    )
    same_offer = await refusal(
        partner,
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "VALUES (:i, :m, 'ref-2', :o, :h, 'kkkkkkkkkkkkkkkkkkkkkkkk')",
        i=new_id(),
        m=merchant_id,
        o=offer_id,
        h="a" * 64,
    )

    assert "uq_partner_submissions_merchant_id_ref" in same_ref
    assert "uq_partner_submissions_offer_id" in same_offer


@pytest.mark.parametrize(
    "assignment",
    [
        "merchant_id = 'x'",
        "ref = 'other'",
        "offer_id = 'x'",
        "first_published_at = now()",
        "id = 'x'",
    ],
)
async def test_a_submission_keeps_its_identity_and_its_first_publish_time_is_not_the_roles(
    owner: AsyncEngine, partner: AsyncEngine, assignment: str
) -> None:
    _offer_id, _merchant_id, _ = await partner_offer(owner, partner)

    message = await refusal(partner, f"UPDATE partner_submissions SET {assignment}")

    assert "permission denied" in message
    assert await denied(partner, "DELETE FROM partner_submissions")


async def test_a_pending_update_is_all_or_nothing_and_capped(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    _offer_id, _merchant_id, _ = await partner_offer(owner, partner)
    big = json.dumps({"x": "y" * 50_000})

    half = await refusal(
        partner, "UPDATE partner_submissions SET pending_hash = :h", h="b" * 64
    )
    oversize = await refusal(
        partner,
        "UPDATE partner_submissions SET pending_payload = CAST(:p AS jsonb), pending_hash = :h, pending_at = now()",
        p=big,
        h="b" * 64,
    )
    await run(
        partner,
        "UPDATE partner_submissions SET pending_payload = CAST(:p AS jsonb), pending_hash = :h, pending_at = now()",
        p=json.dumps({"title": "New"}),
        h="b" * 64,
    )

    assert "ck_partner_submissions_pending_complete" in half
    assert "ck_partner_submissions_pending_complete" in oversize


async def test_a_ref_that_is_not_a_plain_identifier_is_refused_by_the_database(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, merchant_id, _ = await partner_offer(owner, partner)

    message = await refusal(
        partner,
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "VALUES (:i, :m, 'a b', :o, :h, 'kkkkkkkkkkkkkkkkkkkkkkkk')",
        i=new_id(),
        m=merchant_id,
        o=offer_id,
        h="a" * 64,
    )

    assert "ck_partner_submissions_ref_valid" in message


async def test_the_first_time_an_offer_is_published_its_submission_notes_it_once(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _merchant_id, _ = await partner_offer(owner, partner)
    sql = "SELECT first_published_at FROM partner_submissions"

    before = await fetch_one(owner, sql)
    await run(
        owner, "UPDATE offers SET last_verified_at = now() WHERE id = :i", i=offer_id
    )
    await run(owner, "UPDATE offers SET status = 'PUBLISHED' WHERE id = :i", i=offer_id)
    first = await fetch_one(owner, sql)
    await run(owner, "UPDATE offers SET status = 'DRAFT' WHERE id = :i", i=offer_id)
    await run(owner, "UPDATE offers SET status = 'PUBLISHED' WHERE id = :i", i=offer_id)
    again = await fetch_one(owner, sql)

    assert before[0] is None
    assert first[0] is not None
    assert again[0] == first[0]


async def test_an_archived_draft_never_published_has_no_first_publish_time(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _merchant_id, _ = await partner_offer(owner, partner)

    await run(owner, "UPDATE offers SET status = 'ARCHIVED' WHERE id = :i", i=offer_id)
    row = await fetch_one(owner, "SELECT first_published_at FROM partner_submissions")

    assert row[0] is None


async def test_the_role_writes_provenance_for_the_review_queue_and_never_deletes_it(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)
    await run(
        partner,
        "INSERT INTO offer_imports (id, provider, external_id, offer_id, source_hash, applied_source_hash, "
        "applied_content_hash, applied_at) VALUES (:i, 'partner', :e, :o, :h, :h, :h, now())",
        i=new_id(),
        e=new_id(),
        o=offer_id,
        h="c" * 64,
    )

    await run(
        partner,
        "UPDATE offer_imports SET source_changed_at = now(), updated_at = now()",
    )

    assert await denied(partner, "DELETE FROM offer_imports")
    assert await denied(partner, "UPDATE offer_imports SET provider = 'awin'")
    assert await denied(partner, "UPDATE offer_imports SET offer_id = 'x'")


async def test_the_role_reads_categories_but_cannot_change_them_and_cannot_write_merchants(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    _, category_id = await seed(owner)

    row = await fetch_one(
        partner, "SELECT id, slug, enabled FROM categories WHERE id = :i", i=category_id
    )

    assert row.enabled is True
    for statement in (
        "UPDATE categories SET enabled = false",
        "DELETE FROM categories",
        "SELECT name FROM categories",
        "UPDATE merchants SET website = 'https://evil.example.net'",
        "UPDATE merchants SET active = false",
        "INSERT INTO merchants (id, name, slug) VALUES ('m', 'm', 'm')",
    ):
        assert await denied(partner, statement), statement


# ------------------------------------------------------------------------------- the script's own checks


async def test_the_script_refuses_to_run_when_a_guard_trigger_is_disabled_and_runs_again_when_it_is_back(
    database_url: str, role: str, owner: AsyncEngine
) -> None:
    await run(
        owner, "ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_insert_guard"
    )
    try:
        with pytest.raises(Exception, match="guard triggers of migration 0008"):
            await apply_partner_role(database_url, role, role_password_for(role))
    finally:
        await run(
            owner, "ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_insert_guard"
        )

    await apply_partner_role(database_url, role, role_password_for(role))


async def test_the_end_of_an_offer_only_needs_the_end_column(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)

    await run(
        partner,
        "UPDATE offers SET end_at = now() + :d * interval '1 second', updated_at = now() WHERE id = :id",
        id=offer_id,
        d=int(timedelta(days=1).total_seconds()),
    )
    row = await fetch_one(
        owner, "SELECT status FROM offers WHERE id = :id", id=offer_id
    )

    assert row[0] == "DRAFT"


# ------------------------------------------------------- row conditions: what the role may touch (FREEJ-58 H1)

OWNER_OFFER = (
    "INSERT INTO offers (id, title, slug, summary, description, category_id, merchant_id, offer_type, "
    "commercial_basis, status, image_tint, image_label, image_alt, postage_kind, eligibility, "
    "availability, steps) VALUES (:id, 'Owner offer', :slug, 's', '[]'::jsonb, :cat, :mer, :kind, 'EDITORIAL', "
    "'DRAFT', 'cream', 'l', 'a', 'FREE', 'e', 'a', '[]'::jsonb)"
)


async def fetch_all(engine: AsyncEngine, sql: str, **params: object):  # type: ignore[no-untyped-def]
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), params)).all())


async def owner_offer(
    owner: AsyncEngine,
    merchant_id: str,
    category_id: str,
    *,
    status: str = "PUBLISHED",
    kind: str = "FREE_SAMPLE",
    provider: str | None = None,
) -> str:
    """An offer the owner made (hand-made, never submitted); with ``provider`` it has a link and an import row."""
    offer_id = new_id()
    async with owner.begin() as connection:
        await connection.execute(
            text(OWNER_OFFER),
            {
                "id": offer_id,
                "slug": f"owner-{offer_id}",
                "cat": category_id,
                "mer": merchant_id,
                "kind": kind,
            },
        )
        if status != "DRAFT":
            await connection.execute(
                text("UPDATE offers SET last_verified_at = now() WHERE id = :i"),
                {"i": offer_id},
            )
            await connection.execute(
                text("UPDATE offers SET status = :s WHERE id = :i"),
                {"i": offer_id, "s": status},
            )
        if provider is not None:
            await connection.execute(
                text(
                    "INSERT INTO affiliate_links (id, offer_id, provider, destination_url) "
                    "VALUES (:l, :o, :p, 'https://www.awin1.com/cread.php?x=1')"
                ),
                {"l": new_id(), "o": offer_id, "p": provider},
            )
            await connection.execute(
                text(
                    "INSERT INTO offer_imports (id, provider, external_id, offer_id, source_hash, "
                    "applied_source_hash, applied_content_hash, applied_at) "
                    "VALUES (:i, :p, :e, :o, :h, :h, :h, now())"
                ),
                {
                    "i": new_id(),
                    "p": provider,
                    "e": new_id(),
                    "o": offer_id,
                    "h": "d" * 64,
                },
            )
    return offer_id


async def publish_as_owner(owner: AsyncEngine, offer_id: str) -> None:
    await run(
        owner, "UPDATE offers SET last_verified_at = now() WHERE id = :i", i=offer_id
    )
    await run(owner, "UPDATE offers SET status = 'PUBLISHED' WHERE id = :i", i=offer_id)


@pytest.mark.parametrize(
    "assignment",
    [
        "title = 'Rewritten'",
        "summary = 'x'",
        "description = '[\"x\"]'::jsonb",
        "end_at = now()",
        "start_at = NULL",
    ],
)
@pytest.mark.parametrize("status", ["PUBLISHED", "EXPIRED"])
async def test_the_role_cannot_rewrite_or_end_a_hand_made_offer_in_any_status(
    owner: AsyncEngine, partner: AsyncEngine, status: str, assignment: str
) -> None:
    merchant_id, category_id = await seed(owner)
    offer_id = await owner_offer(owner, merchant_id, category_id, status=status)

    message = await refusal(
        partner, f"UPDATE offers SET {assignment} WHERE id = :i", i=offer_id
    )

    assert "this writer may only change an offer it submitted" in message


async def test_an_unscoped_end_of_every_offer_is_refused_and_changes_nothing(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    merchant_id, category_id = await seed(owner)
    await owner_offer(owner, merchant_id, category_id)
    await partner_offer(owner, partner)

    message = await refusal(partner, "UPDATE offers SET end_at = now()")

    assert "this writer may only" in message
    ended = await fetch_one(
        owner, "SELECT count(*) FROM offers WHERE end_at IS NOT NULL"
    )
    assert ended[0] == 0


async def test_once_the_owner_publishes_a_partner_offer_the_role_can_only_end_it(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)
    await publish_as_owner(owner, offer_id)

    for assignment in (
        "title = 'Defaced'",
        "description = '[\"x\"]'::jsonb",
        "end_at = now() + interval '1 day'",
        "end_at = NULL",
        "start_at = now()",
        "offer_type = 'FREE_TRIAL'",
        "requires_card = true",
    ):
        message = await refusal(
            partner, f"UPDATE offers SET {assignment} WHERE id = :i", i=offer_id
        )
        assert "this writer may only end an offer that is not a draft" in message, (
            assignment
        )
    await run(
        partner,
        "UPDATE offers SET end_at = now(), updated_at = now() WHERE id = :i",
        i=offer_id,
    )
    row = await fetch_one(
        owner, "SELECT status, title, end_at FROM offers WHERE id = :i", i=offer_id
    )
    assert (row.status, row.title) == ("PUBLISHED", "Free sample")
    assert row.end_at is not None


async def test_ending_an_offer_may_clear_a_future_start_but_never_set_one(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)
    await run(
        owner,
        "UPDATE offers SET start_at = now() + interval '5 days' WHERE id = :i",
        i=offer_id,
    )
    await publish_as_owner(owner, offer_id)

    await run(
        partner,
        "UPDATE offers SET end_at = now(), start_at = NULL, updated_at = now() WHERE id = :i",
        i=offer_id,
    )

    row = await fetch_one(
        owner, "SELECT start_at, end_at FROM offers WHERE id = :i", i=offer_id
    )
    assert row.start_at is None and row.end_at is not None


async def test_the_role_cannot_insert_an_offer_of_a_kind_the_api_refuses(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    merchant_id, category_id = await seed(owner)
    values = {**offer_values(new_id(), merchant_id, category_id), "kind": "VOUCHER"}

    message = await refusal(partner, INSERT_OFFER, **values)

    assert "this writer may only insert a sample or a trial" in message


async def test_the_residual_nothing_separates_one_merchant_from_another_inside_the_role(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    """Documented in ADR-013: the session carries no merchant identity, so the role can edit any partner draft."""
    offer_id, _, _ = await partner_offer(owner, partner)

    await run(partner, "UPDATE offers SET title = 'Edited' WHERE id = :i", i=offer_id)

    row = await fetch_one(owner, "SELECT title FROM offers WHERE id = :i", i=offer_id)
    assert row.title == "Edited"


async def test_an_awin_link_and_a_link_of_a_published_offer_cannot_be_written(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    merchant_id, category_id = await seed(owner)
    awin_offer = await owner_offer(
        owner, merchant_id, category_id, status="DRAFT", provider="awin"
    )
    awin_link = (
        await fetch_all(
            owner, "SELECT id FROM affiliate_links WHERE offer_id = :o", o=awin_offer
        )
    )[0].id
    offer_id, _, _ = await partner_offer(owner, partner)
    mine = new_id()
    await run(
        partner,
        "INSERT INTO affiliate_links (id, offer_id, destination_url) VALUES (:i, :o, 'https://shop.example.com/x')",
        i=mine,
        o=offer_id,
    )
    await publish_as_owner(owner, offer_id)

    awin = await refusal(
        partner,
        "UPDATE affiliate_links SET destination_url = 'https://www.awin1.com/cread.php?awinaffid=1' WHERE id = :i",
        i=awin_link,
    )
    live = await refusal(
        partner,
        "UPDATE affiliate_links SET destination_url = 'https://shop.example.com/y' WHERE id = :i",
        i=mine,
    )
    insert_on_live = await refusal(
        partner,
        "INSERT INTO affiliate_links (id, offer_id, destination_url) VALUES (:i, :o, 'https://shop.example.com/z')",
        i=new_id(),
        o=offer_id,
    )
    unscoped = await refusal(
        partner,
        "UPDATE affiliate_links SET destination_url = 'https://evil.example.net/'",
    )

    assert "this writer may only write direct links" in awin
    assert "this writer may only write links of a submitted draft" in live
    assert "this writer may only write links of a submitted draft" in insert_on_live
    assert "this writer may only" in unscoped
    stored = await fetch_one(
        owner, "SELECT destination_url FROM affiliate_links WHERE id = :i", i=awin_link
    )
    assert "evil" not in stored[0] and "awinaffid" not in stored[0]


async def test_an_import_row_must_be_partner_and_of_a_submitted_offer(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    merchant_id, category_id = await seed(owner)
    awin_offer = await owner_offer(
        owner, merchant_id, category_id, status="DRAFT", provider="awin"
    )
    offer_id, _, _ = await partner_offer(owner, partner)
    insert = (
        "INSERT INTO offer_imports (id, provider, external_id, offer_id, source_hash, applied_source_hash, "
        "applied_content_hash, applied_at) VALUES (:i, :p, :e, :o, :h, :h, :h, now())"
    )

    awin_row = await refusal(
        partner, insert, i=new_id(), p="awin", e=new_id(), o=offer_id, h="c" * 64
    )
    foreign = await refusal(
        partner, insert, i=new_id(), p="partner", e=new_id(), o=awin_offer, h="c" * 64
    )
    update_awin = await refusal(
        partner,
        "UPDATE offer_imports SET source_hash = :h WHERE provider = 'awin'",
        h="e" * 64,
    )
    ok = await refusal(
        partner, insert, i=new_id(), p="partner", e=new_id(), o=offer_id, h="c" * 64
    )

    assert "this writer may only write partner provenance" in awin_row
    assert "this writer may only write provenance of a submitted offer" in foreign
    assert "this writer may only write partner provenance" in update_awin
    assert ok == ""
    untouched = await fetch_one(
        owner, "SELECT source_hash FROM offer_imports WHERE provider = 'awin'"
    )
    assert untouched[0] == "d" * 64


async def test_a_submission_can_only_be_attached_to_a_new_draft_of_the_same_merchant(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    merchant_id, category_id = await seed(owner)
    # (A draft of the same merchant with no link and no import row can be attached: the guard cannot tell it from
    # a new one, and the residual in ADR-013 says nothing separates the merchant's own drafts. A draft with a link
    # is refused: see the next test.)
    published = await owner_offer(owner, merchant_id, category_id, status="PUBLISHED")
    other_merchant, other_category = await seed(owner)
    foreign = new_id()
    await run(
        partner, INSERT_OFFER, **offer_values(foreign, other_merchant, other_category)
    )
    insert = (
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "VALUES (:i, :m, :r, :o, :h, :k)"
    )

    for kind, offer_id in (
        ("published", published),
        ("of another merchant", foreign),
    ):
        message = await refusal(
            partner,
            insert,
            i=new_id(),
            m=merchant_id,
            r=f"r-{kind[:3]}",
            o=offer_id,
            h="a" * 64,
            k=KEY_ID,
        )
        assert (
            "this writer may only submit a new draft of its own merchant" in message
        ), kind


async def test_a_submission_cannot_adopt_a_draft_that_already_has_a_link(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    merchant_id, category_id = await seed(owner)
    with_link = await owner_offer(
        owner, merchant_id, category_id, status="DRAFT", provider="awin"
    )

    message = await refusal(
        partner,
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "VALUES (:i, :m, 'adopt', :o, :h, :k)",
        i=new_id(),
        m=merchant_id,
        o=with_link,
        h="a" * 64,
        k=KEY_ID,
    )

    assert "this writer may only submit a new draft of its own merchant" in message


async def test_a_link_inserted_before_its_submission_is_refused_so_the_order_is_offer_submission_link(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    merchant_id, category_id = await seed(owner)
    offer_id = new_id()
    await run(partner, INSERT_OFFER, **offer_values(offer_id, merchant_id, category_id))

    message = await refusal(
        partner,
        "INSERT INTO affiliate_links (id, offer_id, destination_url) VALUES (:i, :o, 'https://shop.example.com/x')",
        i=new_id(),
        o=offer_id,
    )

    assert "this writer may only write links of a submitted draft" in message


# ------------------------------------------------------------------------- audit rows: shape and size (M12)


@pytest.mark.parametrize(
    ("action", "resource", "resource_id", "metadata"),
    [
        ("partner.users.delete", "users", "u1", "{}"),
        ("partner.offers.revoke", "offers", "o1", "{}"),
        ("partner.offers.submit", "users", "o1", "{}"),
        ("partner.offers.submit", "offers", "x" * 65, "{}"),
        ("partner.offers.submit", "offers", "o1", '{"x": "' + "y" * 2100 + '"}'),
    ],
)
async def test_an_audit_row_must_be_a_real_partner_action_about_an_offer_with_bounded_size(
    owner: AsyncEngine,
    partner: AsyncEngine,
    action: str,
    resource: str,
    resource_id: str,
    metadata: str,
) -> None:
    message = await refusal(
        partner,
        "INSERT INTO audit_logs (id, action, resource, resource_id, metadata) "
        "VALUES (:i, :a, :r, :ri, CAST(:m AS jsonb))",
        i=new_id(),
        a=action,
        r=resource,
        ri=resource_id,
        m=metadata,
    )

    assert "this writer may only append partner audit rows" in message
    count = await fetch_one(owner, "SELECT count(*) FROM audit_logs")
    assert count[0] == 0


# ------------------------------------------------------------ the guards cannot be switched off by the role (M4)


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_insert_guard",
        "ALTER TABLE offers DISABLE TRIGGER ALL",
        "DROP TRIGGER trg_offers_update_guard ON offers",
        "CREATE OR REPLACE FUNCTION freejoy_audit_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        "DROP FUNCTION freejoy_audit_insert_guard()",
        "SET session_replication_role = replica",
        "CREATE TRIGGER trg_mine BEFORE INSERT ON offers FOR EACH ROW EXECUTE FUNCTION freejoy_offer_insert_defaults()",
    ],
)
async def test_the_role_cannot_disable_replace_or_add_a_trigger_or_switch_triggers_off(
    partner: AsyncEngine, statement: str
) -> None:
    async with partner.connect() as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text(statement))

    message = str(caught.value.orig).lower()
    assert "must be" in message or "permission denied" in message


async def test_the_script_refuses_a_same_named_trigger_on_the_wrong_table_and_a_disabled_guard(
    database_url: str, role: str, owner: AsyncEngine
) -> None:
    await run(owner, "DROP TRIGGER trg_audit_logs_insert_guard ON audit_logs")
    await run(
        owner,
        "CREATE TRIGGER trg_audit_logs_insert_guard BEFORE INSERT ON email_templates FOR EACH ROW "
        "EXECUTE FUNCTION freejoy_audit_insert_guard()",
    )
    try:
        with pytest.raises(Exception, match="missing, changed or disabled"):
            await apply_partner_role(database_url, role, role_password_for(role))
    finally:
        await run(owner, "DROP TRIGGER trg_audit_logs_insert_guard ON email_templates")
        await run(
            owner,
            "CREATE TRIGGER trg_audit_logs_insert_guard BEFORE INSERT ON audit_logs FOR EACH ROW "
            "EXECUTE FUNCTION freejoy_audit_insert_guard()",
        )

    await run(owner, "ALTER TABLE offers DISABLE TRIGGER trg_offers_update_guard")
    try:
        with pytest.raises(Exception, match="missing, changed or disabled"):
            await apply_partner_role(database_url, role, role_password_for(role))
    finally:
        await run(owner, "ALTER TABLE offers ENABLE TRIGGER trg_offers_update_guard")
    await apply_partner_role(database_url, role, role_password_for(role))


async def test_the_live_connection_check_notices_a_disabled_guard(
    partner_url: str, owner: AsyncEngine
) -> None:
    from app.core.database import PartnerRoleNotRestricted, PartnerSessionProvider

    async with PartnerSessionProvider(partner_url).session():
        pass
    await run(
        owner,
        "ALTER TABLE affiliate_links DISABLE TRIGGER trg_affiliate_links_write_guard",
    )
    try:
        with pytest.raises(PartnerRoleNotRestricted):
            async with PartnerSessionProvider(partner_url).session():
                pass
    finally:
        await run(
            owner,
            "ALTER TABLE affiliate_links ENABLE TRIGGER trg_affiliate_links_write_guard",
        )


# --------------------------------------------------------------------------------------- shape checks (L5)


async def test_a_pending_payload_must_be_a_json_object_a_key_id_a_real_key_id_and_a_ref_start_alphanumeric(
    owner: AsyncEngine, partner: AsyncEngine
) -> None:
    offer_id, _, _ = await partner_offer(owner, partner)

    array = await refusal(
        partner,
        "UPDATE partner_submissions SET pending_payload = '[]'::jsonb, pending_hash = :h, pending_at = now()",
        h="b" * 64,
    )
    bad_key = await refusal(partner, "UPDATE partner_submissions SET key_id = 'short'")
    leading_dot = await refusal(
        partner,
        "INSERT INTO partner_submissions (id, merchant_id, ref, offer_id, content_hash, key_id) "
        "SELECT :i, merchant_id, '.hidden', id, :h, :k FROM offers WHERE id = :o",
        i=new_id(),
        o=offer_id,
        h="a" * 64,
        k=KEY_ID,
    )

    assert "ck_partner_submissions_pending_complete" in array
    assert "ck_partner_submissions_key_id_valid" in bad_key
    assert (
        "uq_partner_submissions_offer_id" in leading_dot or "ref_valid" in leading_dot
    )
