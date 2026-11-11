"""The submission rules, run against an in-memory store (no database): the decision tree of ``SubmissionService``.

``tests/api/partner/test_partner_submissions_e2e.py`` proves the same behaviour against Postgres and the real role.
"""

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from app.core.offer_fingerprint import fingerprint
from app.partner.auth import PartnerContext
from app.partner.caps import MAX_NEW_SUBMISSIONS_PER_DAY, MAX_OPEN_SUBMISSIONS
from app.partner.errors import SubmissionBusy, SubmissionNotFound, SubmissionRefused
from app.partner.schemas import OfferSubmission
from app.partner.submission_service import (
    ACTION_END,
    ACTION_SUBMIT,
    ACTION_UPDATE,
    SubmissionService,
    draft_slug,
)
from tests.fakes_partner_submissions import (
    NOW,
    FakeCategories,
    FakeSession,
    FakeSubmissionStore,
)

MERCHANT = "merchant-a"
OTHER = "merchant-b"
KEY = "key-a"
SITE = "https://shop.example.com"


def body(**changes: Any) -> OfferSubmission:
    values: dict[str, Any] = {
        "title": "Free sample box",
        "summary": "A box of samples",
        "description": ["Try our range."],
        "offer_type": "FREE_SAMPLE",
        "category": "home",
        "url": f"{SITE}/sample",
        "postage_kind": "FREE",
        "requires_card": False,
        "subscription_terms": None,
        "eligibility": "UK residents",
        "availability": "While stocks last",
        "steps": [],
        "start_at": None,
        "end_at": None,
    }
    return OfferSubmission.model_validate({**values, **changes})


class World:
    def __init__(self) -> None:
        self.store = FakeSubmissionStore()
        self.store.add_merchant(MERCHANT)
        self.store.add_merchant(OTHER, "Other", "https://other.example.com")
        self.session = FakeSession(self.store)

    def service(self, merchant: str = MERCHANT, key: str = KEY) -> SubmissionService:
        return SubmissionService(
            self.session,  # type: ignore[arg-type]
            PartnerContext(key, merchant),
            clock=lambda: NOW,
            store=self.store,  # type: ignore[arg-type]
            categories=FakeCategories(),  # type: ignore[arg-type]
        )


@pytest.fixture
def world() -> World:
    return World()


async def test_a_new_ref_creates_one_draft_one_direct_link_one_provenance_row_and_an_audit_row(
    world: World,
) -> None:
    result = await world.service().submit("sku-1", body())

    (offer,) = world.store.offers.values()
    (link,) = world.store.links_by_offer[offer.id]
    (imported,) = world.store.imports.values()
    assert result.outcome == "created" and result.status == "pending_review"
    assert (offer.status, offer.featured, offer.last_verified_at) == (
        "DRAFT",
        False,
        None,
    )
    assert (offer.merchant_id, offer.category_id) == (MERCHANT, "cat-home")
    assert (link.provider, link.destination_url, link.active) == (
        "direct",
        f"{SITE}/sample",
        True,
    )
    assert imported.applied_content_hash == fingerprint(offer, [link])
    assert imported.source_changed_at is None
    assert [a["action"] for a in world.store.audits] == [ACTION_SUBMIT]
    assert (
        world.store.audits[0]["key_id"] == KEY
        and world.store.audits[0]["ref"] == "sku-1"
    )
    assert world.session.commits == 1


async def test_the_audit_row_holds_no_copy_of_what_was_sent(world: World) -> None:
    await world.service().submit("sku-1", body(title="Unmistakable title text"))

    assert "Unmistakable" not in str(world.store.audits)


async def test_the_image_placeholder_is_the_shared_default_with_the_merchants_name(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())

    (offer,) = world.store.offers.values()
    assert (offer.image_tint, offer.image_label, offer.image_alt) == (
        "cream",
        "Shop",
        "Illustration of an offer",
    )


async def test_the_slug_is_the_title_and_a_token_and_never_the_bare_title(
    world: World,
) -> None:
    await world.service().submit("sku-1", body(title="Free sample box"))

    (offer,) = world.store.offers.values()
    assert offer.slug.startswith("free-sample-box-")
    assert len(offer.slug) == len("free-sample-box-") + 8


@pytest.mark.parametrize("title", ["!!!", "***", "☃☃"])
def test_a_title_with_nothing_slug_worthy_gets_an_offer_slug(title: str) -> None:
    assert draft_slug(title).startswith("offer-")


async def test_the_same_content_twice_changes_nothing_and_writes_no_audit_row(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    commits = world.session.commits
    world.store.locks.clear()

    second = await world.service().submit("sku-1", body())

    assert second.outcome == "unchanged"
    assert len(world.store.offers) == 1 and len(world.store.audits) == 1
    assert world.session.commits == commits
    assert world.store.locks == []  # the fast path takes no row lock at all


async def test_an_untouched_draft_is_refreshed_with_the_hashes_following(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())

    result = await world.service().submit(
        "sku-1", body(title="New title", url=f"{SITE}/other")
    )

    (offer,) = world.store.offers.values()
    (link,) = world.store.links_by_offer[offer.id]
    (imported,) = world.store.imports.values()
    (submission,) = world.store.submissions.values()
    assert result.outcome == "refreshed"
    assert offer.title == "New title" and link.destination_url == f"{SITE}/other"
    assert imported.applied_content_hash == fingerprint(offer, [link])
    assert (
        imported.source_hash == imported.applied_source_hash == submission.content_hash
    )
    assert submission.pending_hash is None and imported.source_changed_at is None
    assert world.store.audits[-1]["action"] == ACTION_UPDATE
    assert world.store.audits[-1]["outcome"] == "refreshed"


async def test_a_refreshed_draft_can_be_refreshed_again(world: World) -> None:
    await world.service().submit("sku-1", body())
    await world.service().submit("sku-1", body(title="Two"))

    third = await world.service().submit("sku-1", body(title="Three"))

    assert third.outcome == "refreshed"


async def test_an_edited_draft_keeps_its_content_and_the_proposal_waits(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, title="Editor title")

    result = await world.service().submit("sku-1", body(title="Retailer title"))

    (stored,) = world.store.offers.values()
    (imported,) = world.store.imports.values()
    (submission,) = world.store.submissions.values()
    assert result.outcome == "pending_update" and result.update_pending is True
    assert stored.title == "Editor title"
    assert world.store.pending_payload_of(submission) is not None
    assert world.store.pending_payload_of(submission)["title"] == "Retailer title"
    assert submission.pending_hash == submission.content_hash
    assert imported.source_changed_at is not None
    assert imported.applied_source_hash != imported.source_hash


@pytest.mark.parametrize("status", ["PUBLISHED", "EXPIRED"])
async def test_a_live_or_expired_offer_is_never_overwritten(
    world: World, status: str
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, status=status)

    result = await world.service().submit("sku-1", body(title="Retailer title"))

    assert result.outcome == "pending_update"
    assert next(iter(world.store.offers.values())).title == "Free sample box"


async def test_a_draft_whose_link_an_editor_added_to_is_touched(world: World) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    await world.store.insert_link(MERCHANT, offer.id, f"{SITE}/second")

    result = await world.service().submit("sku-1", body(title="Retailer title"))

    assert result.outcome == "pending_update"


async def test_the_pending_payload_is_the_cleaned_content_never_the_raw_request(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, title="Editor title")

    await world.service().submit(
        "sku-1", body(title="<b>Bold</b><script>x()</script> title")
    )

    (submission,) = world.store.submissions.values()
    assert world.store.pending_payload_of(submission) is not None
    assert world.store.pending_payload_of(submission)["title"] == "Bold title"
    assert "<script" not in str(world.store.pending_payload_of(submission))
    assert "x()" not in str(world.store.pending_payload_of(submission))


async def test_sending_back_what_the_offer_reflects_clears_the_proposal(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, title="Editor title")
    await world.service().submit("sku-1", body(title="Retailer title"))

    result = await world.service().submit("sku-1", body())

    (submission,) = world.store.submissions.values()
    (imported,) = world.store.imports.values()
    assert result.outcome == "unchanged" and result.update_pending is False
    assert (
        world.store.pending_payload_of(submission) is None
        and imported.source_changed_at is None
    )


async def test_the_same_pending_content_twice_is_unchanged(world: World) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, title="Editor title")
    await world.service().submit("sku-1", body(title="Retailer title"))
    audits = len(world.store.audits)

    again = await world.service().submit("sku-1", body(title="Retailer title"))

    assert again.outcome == "unchanged" and len(world.store.audits) == audits


async def test_an_archived_offer_refuses_an_update_with_offer_closed(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, status="ARCHIVED")

    with pytest.raises(SubmissionRefused) as refused:
        await world.service().submit("sku-1", body(title="Again"))

    assert refused.value.code == "offer_closed"
    assert (refused.value.status_code, refused.value.message) == (409, "Conflict")


async def test_an_unknown_or_disabled_category_is_refused_and_nothing_is_written(
    world: World,
) -> None:
    with pytest.raises(SubmissionRefused) as refused:
        await world.service().submit("sku-1", body(category="nope"))

    assert refused.value.code == "unknown_category"
    assert world.store.offers == {} and world.session.commits == 0


async def test_a_merchant_that_is_gone_is_not_found(world: World) -> None:
    world.store.merchants.pop(MERCHANT)

    with pytest.raises(SubmissionNotFound):
        await world.service().submit("sku-1", body())


async def test_a_link_to_another_site_is_refused_before_anything_is_written(
    world: World,
) -> None:
    with pytest.raises(SubmissionRefused) as refused:
        await world.service().submit("sku-1", body(url="https://evil.example.net/x"))

    assert refused.value.code == "link_host_not_allowed"
    assert world.store.offers == {}


# ------------------------------------------------------------------------------------------- the caps


async def test_the_open_cap_is_counted_after_the_merchant_lock_and_refuses_a_new_ref(
    world: World,
) -> None:
    for n in range(MAX_OPEN_SUBMISSIONS):
        await world.service().submit(f"r{n}", body(title=f"Offer {n}"))
        world.store.created_today_override = 0  # keep the daily cap out of this test

    with pytest.raises(SubmissionRefused) as refused:
        await world.service().submit("one-too-many", body(title="Too many"))

    assert refused.value.code == "open_submission_cap"
    assert (refused.value.status_code, refused.value.message) == (
        429,
        "Too many requests",
    )
    assert world.store.locks.count(f"creation:{MERCHANT}") == MAX_OPEN_SUBMISSIONS + 1


async def test_an_update_is_never_refused_by_a_cap(world: World) -> None:
    for n in range(MAX_OPEN_SUBMISSIONS):
        await world.service().submit(f"r{n}", body(title=f"Offer {n}"))
        world.store.created_today_override = 0

    result = await world.service().submit("r0", body(title="Edited title"))

    assert result.outcome == "refreshed"


async def test_the_daily_cap_refuses_a_new_ref(world: World) -> None:
    world.store.created_today_override = MAX_NEW_SUBMISSIONS_PER_DAY

    with pytest.raises(SubmissionRefused) as refused:
        await world.service().submit("sku-1", body())

    assert refused.value.code == "daily_submission_cap"
    assert world.store.offers == {}


async def test_a_cap_of_one_merchant_does_not_count_the_other(world: World) -> None:
    for n in range(MAX_OPEN_SUBMISSIONS):
        await world.service().submit(f"r{n}", body(title=f"Offer {n}"))
        world.store.created_today_override = 0

    other = await world.service(OTHER, "key-b").submit(
        "r0", body(url="https://other.example.com/x")
    )

    assert other.outcome == "created"


# ---------------------------------------------------------------------- races and the unique constraint


async def test_the_unique_ref_is_the_arbiter_and_the_loser_carries_on_as_an_update(
    world: World,
) -> None:
    await world.service(key="key-winner").submit("sku-1", body(title="Winner title"))
    world.store.hide_find = 2  # the loser's two checks ran before the winner committed

    result = await world.service().submit("sku-1", body(title="Loser title"))

    assert (
        result.outcome == "refreshed"
    )  # the winner's draft was untouched, so the loser's content refreshes it
    assert len(world.store.offers) == 1 and len(world.store.submissions) == 1
    assert next(iter(world.store.offers.values())).title == "Loser title"
    assert next(iter(world.store.submissions.values())).key_id == KEY


async def test_a_lost_race_with_the_same_content_is_unchanged(world: World) -> None:
    await world.service(key="key-winner").submit("sku-1", body())
    world.store.hide_find = 2

    result = await world.service().submit("sku-1", body())

    assert result.outcome == "unchanged" and len(world.store.offers) == 1


async def test_a_slug_collision_is_retried_with_a_new_token(world: World) -> None:
    world.store.fail_inserts.append("slug")

    result = await world.service().submit("sku-1", body())

    assert result.outcome == "created" and len(world.store.offers) == 1


async def test_three_slug_collisions_in_a_row_are_a_busy_answer_not_a_crash(
    world: World,
) -> None:
    world.store.fail_inserts.extend(["slug", "slug", "slug"])

    with pytest.raises(SubmissionBusy):
        await world.service().submit("sku-1", body())

    assert world.store.offers == {}


async def test_an_integrity_error_that_is_neither_constraint_is_raised(
    world: World,
) -> None:
    from tests.fakes_partner_submissions import unique_violation

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise unique_violation("some_other_constraint")

    world.store.insert_offer = boom  # type: ignore[method-assign]

    with pytest.raises(Exception, match="duplicate key"):
        await world.service().submit("sku-1", body())


# --------------------------------------------------------------------------------------- lock order


async def test_a_create_takes_the_merchant_creation_lock_before_anything_else(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())

    assert world.store.locks == [f"creation:{MERCHANT}"]


async def test_an_update_locks_the_offer_then_its_provenance_as_the_admin_service_does(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.locks.clear()

    await world.service().submit("sku-1", body(title="Two"))

    assert world.store.locks == [f"offer:{offer.id}", f"import:{offer.id}"]


# ------------------------------------------------------------------------------------------------ end


async def test_end_sets_the_end_date_once_and_audits_once(world: World) -> None:
    await world.service().submit("sku-1", body())

    first = await world.service().end("sku-1")
    second = await world.service().end("sku-1")

    (offer,) = world.store.offers.values()
    assert (first.changed, first.status) == (True, "ended")
    assert (second.changed, second.status) == (False, "ended")
    assert offer.status == "DRAFT" and offer.end_at == NOW
    assert [a["action"] for a in world.store.audits].count(ACTION_END) == 1


async def test_end_clears_a_start_date_that_is_still_ahead(world: World) -> None:
    await world.service().submit(
        "sku-1", body(start_at="2026-06-20T00:00:00Z", end_at="2026-07-20T00:00:00Z")
    )

    await world.service().end("sku-1")

    (offer,) = world.store.offers.values()
    assert offer.start_at is None and offer.end_at == NOW


async def test_end_on_an_archived_offer_is_a_no_op_that_shows_its_status(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, status="ARCHIVED")

    result = await world.service().end("sku-1")

    assert (result.changed, result.status) == (False, "declined")
    assert next(iter(world.store.offers.values())).end_at is None


async def test_end_of_an_unknown_ref_is_not_found(world: World) -> None:
    with pytest.raises(SubmissionNotFound):
        await world.service().end("nope")


# ----------------------------------------------------------------------------------------- the reads


async def test_the_public_url_is_given_only_while_the_offer_is_live(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    draft = await world.service().get("sku-1")
    world.store.edit_offer(offer.id, status="PUBLISHED")
    live = await world.service().get("sku-1")
    world.store.edit_offer(offer.id, past_end=True)
    ended = await world.service().get("sku-1")

    assert (draft.status, draft.public_url) == ("pending_review", None)
    assert live.status == "live"
    assert live.public_url == f"http://localhost:3000/freebies/{offer.slug}"
    assert (ended.status, ended.public_url) == ("ended", None)


async def test_list_pages_only_the_callers_submissions(world: World) -> None:
    for n in range(3):
        await world.service().submit(f"r{n}", body(title=f"Offer {n}"))
    await world.service(OTHER, "key-b").submit(
        "x", body(url="https://other.example.com/x")
    )

    page = await world.service().list_submissions(2, 0)
    rest = await world.service().list_submissions(2, 2)

    assert (len(page.offers), page.total_count) == (2, 3)
    assert len(rest.offers) == 1
    assert {o.ref for o in page.offers + rest.offers} == {"r0", "r1", "r2"}


async def test_another_merchants_ref_is_not_found_in_the_same_one_query_as_an_unknown_one(
    world: World,
) -> None:
    await world.service(OTHER, "key-b").submit(
        "theirs", body(url="https://other.example.com/x")
    )
    world.store.calls.clear()

    with pytest.raises(SubmissionNotFound):
        await world.service().get("theirs")
    foreign = list(world.store.calls)
    world.store.calls.clear()
    with pytest.raises(SubmissionNotFound):
        await world.service().get("never-existed")
    unknown = list(world.store.calls)

    assert (
        [c.split(":")[0] for c in foreign]
        == [c.split(":")[0] for c in unknown]
        == ["view"]
    )
    assert foreign == [f"view:{MERCHANT}:theirs"]


async def test_a_put_on_a_ref_another_merchant_uses_creates_a_separate_submission(
    world: World,
) -> None:
    await world.service(OTHER, "key-b").submit(
        "shared", body(url="https://other.example.com/x", title="Theirs")
    )

    mine = await world.service().submit("shared", body(title="Mine"))

    assert mine.outcome == "created"
    assert {o.title for o in world.store.offers.values()} == {"Theirs", "Mine"}
    assert replace(
        world.store.by_ref(OTHER, "shared"), ref="shared"
    )  # theirs still exists, untouched


async def test_an_unchanged_resend_of_an_archived_offer_still_takes_the_locked_path_and_is_refused(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, status="ARCHIVED")

    with pytest.raises(SubmissionRefused) as refused:
        await world.service().submit("sku-1", body())

    assert refused.value.code == "offer_closed"


async def test_a_resend_of_stored_content_is_unchanged_after_its_end_date_but_changed_content_is_refused(
    world: World,
) -> None:
    ending = body(end_at="2026-06-10T00:00:00Z")
    await world.service().submit("sku-1", ending)
    later = SubmissionService(
        world.session,  # type: ignore[arg-type]
        PartnerContext(KEY, MERCHANT),
        clock=lambda: NOW + timedelta(days=30),
        store=world.store,  # type: ignore[arg-type]
        categories=FakeCategories(),  # type: ignore[arg-type]
    )

    resent = await later.submit("sku-1", ending)
    with pytest.raises(SubmissionRefused) as refused:
        await later.submit(
            "sku-1", body(title="Changed", end_at="2026-06-10T00:00:00Z")
        )
    with pytest.raises(SubmissionRefused) as new_ref:
        await later.submit("sku-2", ending)

    assert resent.outcome == "unchanged"
    assert refused.value.code == new_ref.value.code == "invalid_window"


async def test_a_start_date_more_than_ninety_days_ahead_is_refused_on_a_new_ref(
    world: World,
) -> None:
    with pytest.raises(SubmissionRefused) as refused:
        await world.service().submit("sku-1", body(start_at="2027-01-01T00:00:00Z"))

    assert refused.value.code == "invalid_window"
    assert world.store.offers == {}


async def test_ending_discards_a_waiting_proposal_and_goes_back_to_the_applied_hash(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, status="PUBLISHED")
    first = next(iter(world.store.submissions.values())).content_hash
    queued = await world.service().submit(
        "sku-1", body(title="Retailer title", end_at="2026-12-01T00:00:00Z")
    )

    ended = await world.service().end("sku-1")

    (submission,) = world.store.submissions.values()
    (imported,) = world.store.imports.values()
    assert queued.outcome == "pending_update" and queued.update_pending is True
    assert ended.changed is True and ended.update_pending is False
    assert (
        submission.pending_hash is None
        and world.store.pending_payload_of(submission) is None
    )
    assert submission.content_hash == first == imported.applied_source_hash
    assert imported.source_changed_at is None
    assert world.store.audits[-1]["superseded_pending"] is True
    again = await world.service().submit(
        "sku-1", body(title="Retailer title", end_at="2026-12-01T00:00:00Z")
    )
    assert again.outcome == "pending_update"  # a proposal after the end queues normally


async def test_the_view_is_read_before_the_commit_so_a_write_needs_no_extra_connection(
    world: World,
) -> None:
    order: list[str] = []
    original_view = world.store.view

    async def view(merchant_id: str, ref: str):  # type: ignore[no-untyped-def]
        order.append(f"view@commits={world.session.commits}")
        return await original_view(merchant_id, ref)

    world.store.view = view  # type: ignore[method-assign]

    await world.service().submit("sku-1", body())
    await world.service().submit("sku-1", body(title="Two"))
    await world.service().end("sku-1")

    assert order == ["view@commits=0", "view@commits=1", "view@commits=2"]


async def test_the_inserts_of_a_new_draft_come_in_the_order_the_database_guards_need(
    world: World,
) -> None:
    calls: list[str] = []
    for name in ("insert_offer", "insert_submission", "insert_link", "insert_import"):
        original = getattr(world.store, name)

        async def wrapped(*args, __o=original, __n=name, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(__n)
            return await __o(*args, **kwargs)

        setattr(world.store, name, wrapped)

    await world.service().submit("sku-1", body())

    assert calls == [
        "insert_offer",
        "insert_submission",
        "insert_link",
        "insert_import",
    ]


async def test_a_view_has_no_public_url_while_the_category_is_disabled(
    world: World,
) -> None:
    await world.service().submit("sku-1", body())
    (offer,) = world.store.offers.values()
    world.store.edit_offer(offer.id, status="PUBLISHED")
    world.store.category_enabled = False

    view = await world.service().get("sku-1")

    assert (view.status, view.public_url) == ("live", None)
