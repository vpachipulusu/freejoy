"""``ReportImporter``: insert, update, unchanged, and how a reported click reference is matched."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.repositories.network_reports import MAX_CANDIDATES
from app.services.awin_transactions import ReportRow, SkipReason
from app.services.network_report_import import ReportImporter, ReportTally
from tests.awin_report_samples import ADVERTISER_ID, row
from tests.fakes_network_reports import FakeLink, FakeReportDatabase

T1 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
T2 = T1 + timedelta(hours=1)


def awin_url(advertiser: int = ADVERTISER_ID, **extra: str) -> str:
    """A stored Awin click link for ``advertiser``; ``extra`` are more query parameters."""
    more = "".join(f"&{key}={value}" for key, value in extra.items())
    return (
        f"https://www.awin1.com/cread.php?awinmid={advertiser}&awinaffid=424242{more}"
    )


async def import_rows(
    database: FakeReportDatabase,
    rows: list[ReportRow],
    now: datetime = T1,
    rejected_ids: tuple[str, ...] = (),
) -> ReportTally:
    async with database.unit() as unit:
        importer = ReportImporter(unit.store, unit.session, lambda: now)
        return await importer.import_batch(rows, rejected_ids)


# --- insert, update, unchanged -------------------------------------------------------------


async def test_a_new_transaction_is_inserted_with_every_reported_field() -> None:
    database = FakeReportDatabase()

    tally = await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert (tally.inserted, tally.updated, tally.unchanged) == (1, 0, 0)
    assert stored["provider"] == "awin" and stored["external_id"] == "5001"
    assert stored["merchant_external_id"] == str(ADVERTISER_ID)
    assert stored["commission_status"] == "approved"
    assert stored["commission_status_raw"] == "approved"
    assert stored["commission_amount_minor"] == 150
    assert stored["sale_amount_minor"] == 3000
    assert stored["currency"] == "GBP" and stored["click_ref"] == "ref-5001"
    assert stored["transaction_at"] == datetime(2026, 11, 3, 10, 15, tzinfo=UTC)
    assert len(stored["report_hash"]) == 64
    assert stored["first_seen_at"] == T1 and stored["last_seen_at"] == T1
    assert database.commits == 1 and database.committed.run_locks == 1


async def test_a_rerun_of_the_same_report_only_moves_last_seen_at() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(5001)], T1)
    before = dict(database.row("5001"))

    tally = await import_rows(database, [row(5001)], T2)

    after = database.row("5001")
    assert (tally.inserted, tally.updated, tally.unchanged) == (0, 0, 1)
    assert after["last_seen_at"] == T2 and after["first_seen_at"] == T1
    assert {k: v for k, v in after.items() if k != "last_seen_at"} == {
        k: v for k, v in before.items() if k != "last_seen_at"
    }


async def test_a_changed_status_and_amount_update_the_row_and_keep_first_seen_at() -> (
    None
):
    database = FakeReportDatabase()
    await import_rows(database, [row(5001, commissionStatus="pending")], T1)
    original = dict(database.row("5001"))
    changed = row(
        5001,
        commissionStatus="declined",
        commissionAmount={"amount": 0, "currency": "GBP"},
    )

    tally = await import_rows(database, [changed], T2)

    stored = database.row("5001")
    assert (tally.inserted, tally.updated, tally.unchanged) == (0, 1, 0)
    assert stored["id"] == original["id"] and len(database.committed.rows) == 1
    assert stored["commission_status"] == "declined"
    assert stored["commission_amount_minor"] == 0
    assert stored["first_seen_at"] == T1 and stored["last_seen_at"] == T2
    assert stored["report_hash"] != original["report_hash"]


async def test_a_deleted_transaction_updates_the_row_it_was_approved_in() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(5001, commissionStatus="approved")], T1)

    tally = await import_rows(database, [row(5001, commissionStatus="deleted")], T2)

    assert (tally.inserted, tally.updated) == (0, 1)
    assert database.row("5001")["commission_status"] == "deleted"


async def test_a_batch_counts_each_outcome() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(1), row(2)], T1)

    tally = await import_rows(
        database, [row(1), row(2, commissionStatus="paid"), row(3)], T2
    )

    assert (tally.inserted, tally.updated, tally.unchanged) == (1, 1, 1)


async def test_the_run_lock_is_taken_once_before_anything_else_in_a_batch() -> None:
    database = FakeReportDatabase()

    await import_rows(database, [row(1), row(2), row(3)])

    assert database.committed.run_locks == 1


async def test_a_failing_insert_commits_nothing_from_the_batch() -> None:
    database = FakeReportDatabase(fail_insert_at=2)

    with pytest.raises(RuntimeError):
        await import_rows(database, [row(1), row(2)])

    assert database.committed.rows == {} and database.commits == 0


async def test_last_seen_at_never_moves_backwards() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(5001, commissionStatus="pending")], T2)

    await import_rows(
        database, [row(5001, commissionStatus="approved")], T1
    )  # older run
    assert database.row("5001")["last_seen_at"] == T2

    await import_rows(
        database, [row(5001, commissionStatus="approved")], T1
    )  # unchanged
    assert database.row("5001")["last_seen_at"] == T2


# --- one query per kind, not one per row -----------------------------------------------------


async def test_a_batch_reads_stored_rows_merchants_and_matches_once_not_per_row() -> (
    None
):
    database = FakeReportDatabase()
    database.map_merchant(ADVERTISER_ID, "merchant-1")
    database.add_link(FakeLink("l1", "o1", awin_url(), "shared-ref"))
    rows = [row(n, clickRefs={"clickRef": "shared-ref"}) for n in range(1, 201)]

    tally = await import_rows(database, rows)

    assert tally.inserted == 200 and tally.unmatched == 0
    assert database.calls["find_rows"] == 1
    assert database.calls["find_merchant_id"] == 1
    assert database.calls["links_by_tracking_id"] == 1
    assert database.calls["links_with_text"] == 0


async def test_distinct_references_and_advertisers_are_each_looked_up_once() -> None:
    database = FakeReportDatabase()
    rows = [
        row(1, clickRefs={"clickRef": "a"}),
        row(2, clickRefs={"clickRef": "a"}),
        row(3, clickRefs={"clickRef": "b"}),
        row(4, advertiserId=9, clickRefs={"clickRef": "a"}),
    ]

    await import_rows(database, rows)

    assert database.calls["links_by_tracking_id"] == 3  # (77, a), (77, b), (9, a)
    assert database.calls["find_merchant_id"] == 2  # 77 and 9


# --- matching ------------------------------------------------------------------------------


async def test_a_click_reference_equal_to_an_active_tracking_id_matches_the_link_and_offer() -> (
    None
):
    database = FakeReportDatabase()
    database.add_link(FakeLink("link-1", "offer-1", awin_url(), "ref-5001"))

    tally = await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert stored["affiliate_link_id"] == "link-1" and stored["offer_id"] == "offer-1"
    assert tally.unmatched == 0 and dict(tally.match_problems) == {}


async def test_a_click_reference_inside_the_stored_link_destination_matches() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("link-9", "offer-9", awin_url(clickref="ref-5001")))

    await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert stored["affiliate_link_id"] == "link-9" and stored["offer_id"] == "offer-9"


async def test_the_reported_reference_is_compared_with_clickref_only_not_slots_2_to_6() -> (
    None
):
    database = FakeReportDatabase()
    for name in ("clickref2", "clickref3", "clickref6"):
        database.add_link(
            FakeLink(f"link-{name}", "offer-x", awin_url(**{name: "ref-5001"}))
        )

    tally = await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None and tally.unmatched == 1


async def test_a_value_that_only_contains_the_reference_does_not_match() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("link-1", "offer-1", awin_url(clickref="ref-50011")))

    tally = await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None and tally.unmatched == 1


async def test_a_reference_in_a_non_click_parameter_does_not_match() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("link-1", "offer-1", awin_url(ued="ref-5001")))

    await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None


async def test_the_tracking_id_wins_over_a_destination_match() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("by-url", "offer-u", awin_url(clickref="ref-5001")))
    database.add_link(FakeLink("by-id", "offer-i", awin_url(), "ref-5001"))

    await import_rows(database, [row(5001)])

    assert database.row("5001")["affiliate_link_id"] == "by-id"


async def test_an_inactive_or_foreign_provider_link_never_matches() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("off", "o1", awin_url(), "ref-5001", active=False))
    database.add_link(
        FakeLink("other", "o2", awin_url(), "ref-5001", provider="direct")
    )

    await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None


async def test_a_link_that_is_not_a_recognised_awin_link_is_not_a_candidate() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("a", "o1", "https://x.example/landing", "ref-5001"))
    database.add_link(
        FakeLink("b", "o2", "https://www.awin1.com/other.php?mid=77", "ref-5001")
    )

    tally = await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None
    assert (
        dict(tally.match_problems) == {}
    )  # not a mismatch: nothing claimed it validly


async def test_two_links_claiming_one_reference_are_ambiguous_and_leave_both_null() -> (
    None
):
    database = FakeReportDatabase()
    database.add_link(FakeLink("a", "o1", awin_url(), "ref-5001"))
    database.add_link(FakeLink("b", "o2", awin_url(), "ref-5001"))

    tally = await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert stored["offer_id"] is None and stored["affiliate_link_id"] is None
    assert tally.unmatched == 1


async def test_a_row_with_no_click_reference_is_kept_unmatched() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("a", "o1", awin_url(), "ref-5001"))

    tally = await import_rows(database, [row(5001, clickRefs=None)])

    stored = database.row("5001")
    assert stored["click_ref"] is None and stored["offer_id"] is None
    assert tally.inserted == 1 and tally.unmatched == 1


async def test_a_merchant_is_resolved_through_the_advertiser_even_when_the_click_is_not() -> (
    None
):
    database = FakeReportDatabase()
    database.map_merchant(ADVERTISER_ID, "merchant-1")

    tally = await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert stored["merchant_id"] == "merchant-1"
    assert stored["offer_id"] is None and stored["affiliate_link_id"] is None
    assert tally.unmatched == 1


async def test_an_unknown_advertiser_leaves_the_merchant_null_but_keeps_the_reported_id() -> (
    None
):
    database = FakeReportDatabase()

    await import_rows(database, [row(5001, advertiserId=9999)])

    stored = database.row("5001")
    assert stored["merchant_id"] is None and stored["merchant_external_id"] == "9999"


async def test_an_unchanged_row_that_is_unmatched_still_counts_as_unmatched() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(5001)], T1)

    tally = await import_rows(database, [row(5001)], T2)

    assert tally.unchanged == 1 and tally.unmatched == 1


# --- a link of another advertiser never matches (merchant_mismatch) ---------------------------


@pytest.mark.parametrize("path", ["tracking_id", "destination"])
async def test_a_reference_carried_only_by_another_advertisers_link_matches_nothing(
    path: str,
) -> None:
    database = FakeReportDatabase()
    other = 77
    if path == "tracking_id":
        database.add_link(FakeLink("l1", "o1", awin_url(other), "ref-1"))
    else:
        database.add_link(FakeLink("l1", "o1", awin_url(other, clickref="ref-1")))

    tally = await import_rows(
        database, [row(1, advertiserId=88, clickRefs={"clickRef": "ref-1"})]
    )

    stored = database.row("1")
    assert stored["offer_id"] is None and stored["affiliate_link_id"] is None
    assert tally.unmatched == 1 and dict(tally.match_problems) == {
        "merchant_mismatch": 1
    }


async def test_the_link_of_the_reported_advertiser_wins_over_another_advertisers_link() -> (
    None
):
    database = FakeReportDatabase()
    database.add_link(FakeLink("theirs", "o-theirs", awin_url(88), "ref-1"))
    database.add_link(FakeLink("ours", "o-ours", awin_url(77), "ref-1"))

    tally = await import_rows(database, [row(1, clickRefs={"clickRef": "ref-1"})])

    assert database.row("1")["offer_id"] == "o-ours"
    assert dict(tally.match_problems) == {}


async def test_every_row_with_a_mismatched_reference_is_counted() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("l1", "o1", awin_url(77), "ref-1"))

    tally = await import_rows(
        database,
        [row(n, advertiserId=88, clickRefs={"clickRef": "ref-1"}) for n in (1, 2, 3)],
    )

    assert dict(tally.match_problems) == {"merchant_mismatch": 3}


# --- the candidate cap fails closed ----------------------------------------------------------


@pytest.mark.parametrize("path", ["tracking_id", "destination"])
async def test_more_links_than_the_cap_match_nothing_and_are_counted(path: str) -> None:
    database = FakeReportDatabase()
    for n in range(MAX_CANDIDATES + 1):
        if path == "tracking_id":
            database.add_link(FakeLink(f"l{n:04d}", f"o{n}", awin_url(), "ref-1"))
        else:
            database.add_link(
                FakeLink(f"l{n:04d}", f"o{n}", awin_url(clickref="ref-1"))
            )

    tally = await import_rows(database, [row(1, clickRefs={"clickRef": "ref-1"})])

    assert database.row("1")["offer_id"] is None
    assert dict(tally.match_problems) == {"too_many_candidates": 1}


async def test_exactly_the_cap_of_links_is_still_decided_normally() -> None:
    database = FakeReportDatabase()
    for n in range(
        MAX_CANDIDATES - 1
    ):  # pre-filter hits (they contain "=ref-1") that are not it
        database.add_link(
            FakeLink(f"l{n:04d}", f"o{n}", awin_url(clickref=f"ref-1-{n}"))
        )
    database.add_link(FakeLink("zz-ours", "o-ours", awin_url(clickref="ref-1")))

    await import_rows(database, [row(1, clickRefs={"clickRef": "ref-1"})])

    assert database.row("1")["offer_id"] == "o-ours"


# --- a stored match survives a later change ---------------------------------------------------


async def matched_pending(database: FakeReportDatabase) -> dict[str, Any]:
    database.add_link(FakeLink("link-1", "offer-1", awin_url(), "ref-5001"))
    await import_rows(database, [row(5001, commissionStatus="pending")], T1)
    stored = dict(database.row("5001"))
    assert stored["offer_id"] == "offer-1"
    return stored


def deactivate(database: FakeReportDatabase, link_id: str) -> None:
    links = database.committed.links
    database.committed.links = [
        FakeLink(
            link.link_id,
            link.offer_id,
            link.destination_url,
            link.tracking_id,
            link.provider,
            False,
        )
        if link.link_id == link_id
        else link
        for link in links
    ]


async def test_a_status_change_keeps_the_match_when_the_link_was_deactivated_since() -> (
    None
):
    database = FakeReportDatabase()
    await matched_pending(database)
    deactivate(database, "link-1")

    tally = await import_rows(database, [row(5001, commissionStatus="approved")], T2)

    stored = database.row("5001")
    assert tally.updated == 1 and tally.unmatched == 0
    assert (stored["offer_id"], stored["affiliate_link_id"]) == ("offer-1", "link-1")
    assert stored["commission_status"] == "approved"


async def test_a_status_change_keeps_the_match_when_a_second_link_now_carries_the_reference() -> (
    None
):
    database = FakeReportDatabase()
    await matched_pending(database)
    database.add_link(FakeLink("link-2", "offer-2", awin_url(), "ref-5001"))

    tally = await import_rows(
        database,
        [
            row(
                5001,
                commissionStatus="approved",
                commissionAmount={"amount": 2, "currency": "GBP"},
            )
        ],
        T2,
    )

    stored = database.row("5001")
    assert tally.updated == 1
    assert (stored["offer_id"], stored["affiliate_link_id"]) == ("offer-1", "link-1")
    assert stored["commission_amount_minor"] == 200


async def test_a_changed_click_reference_is_matched_again() -> None:
    database = FakeReportDatabase()
    await matched_pending(database)
    database.add_link(FakeLink("link-2", "offer-2", awin_url(), "ref-new"))

    await import_rows(
        database,
        [row(5001, commissionStatus="approved", clickRefs={"clickRef": "ref-new"})],
        T2,
    )

    stored = database.row("5001")
    assert (stored["offer_id"], stored["affiliate_link_id"]) == ("offer-2", "link-2")


async def test_a_changed_click_reference_that_matches_nothing_clears_the_match() -> (
    None
):
    database = FakeReportDatabase()
    await matched_pending(database)

    tally = await import_rows(
        database, [row(5001, clickRefs={"clickRef": "nothing-carries-this"})], T2
    )

    assert database.row("5001")["offer_id"] is None and tally.unmatched == 1


async def test_a_match_that_was_empty_is_tried_again_when_the_row_changes() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(5001, commissionStatus="pending")], T1)
    assert database.row("5001")["offer_id"] is None
    database.add_link(FakeLink("late", "offer-late", awin_url(), "ref-5001"))

    await import_rows(database, [row(5001, commissionStatus="pending")], T2)
    assert (
        database.row("5001")["offer_id"] is None
    )  # unchanged: only last_seen_at moves

    await import_rows(database, [row(5001, commissionStatus="approved")], T2)
    assert database.row("5001")["offer_id"] == "offer-late"


# --- rejected rows that are already stored ---------------------------------------------------


async def test_a_rejected_transaction_that_is_already_stored_is_counted_and_left_alone() -> (
    None
):
    database = FakeReportDatabase()
    await import_rows(database, [row(5001), row(5002)], T1)
    before = {k: dict(v) for k, v in database.committed.rows.items()}

    tally = await import_rows(database, [], T2, rejected_ids=("5001", "9999"))

    assert tally.rejected_known == 1
    assert database.committed.rows == before


# --- the tally -----------------------------------------------------------------------------


def test_a_tally_is_immutable_and_merges_every_count() -> None:
    first = ReportTally(inserted=1, rejected_known=1).skip(SkipReason.UNKNOWN_STATUS)
    second = ReportTally(
        updated=2, unmatched=1, match_problems={"merchant_mismatch": 2}
    ).skip(SkipReason.UNKNOWN_STATUS)

    merged = first.merged(second)

    assert (merged.inserted, merged.updated, merged.unmatched) == (1, 2, 1)
    assert merged.rejected_known == 1
    assert dict(merged.skipped) == {"unknown_status": 2} and merged.skipped_total == 2
    assert dict(merged.match_problems) == {"merchant_mismatch": 2}
    assert dict(first.skipped) == {"unknown_status": 1}
