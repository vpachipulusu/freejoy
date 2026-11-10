"""``ReportImporter``: insert, update, unchanged, and how a reported click reference is matched."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.platform.network_report import CommissionStatus
from app.services.awin_transactions import SkipReason
from app.services.network_report_import import ReportImporter, ReportTally
from tests.awin_report_samples import ADVERTISER_ID, row
from tests.fakes_network_reports import FakeLink, FakeReportDatabase

T1 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
T2 = T1 + timedelta(hours=1)


async def import_rows(
    database: FakeReportDatabase, rows: list, now: datetime = T1
) -> ReportTally:
    async with database.unit() as unit:
        importer = ReportImporter(unit.store, unit.session, lambda: now)
        return await importer.import_batch(rows)


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
    original_id = database.row("5001")["id"]
    changed = row(
        5001,
        commissionStatus="declined",
        commissionAmount={"amount": 0, "currency": "GBP"},
    )

    tally = await import_rows(database, [changed], T2)

    stored = database.row("5001")
    assert (tally.inserted, tally.updated, tally.unchanged) == (0, 1, 0)
    assert stored["id"] == original_id and len(database.committed.rows) == 1
    assert stored["commission_status"] == "declined"
    assert stored["commission_amount_minor"] == 0
    assert stored["first_seen_at"] == T1 and stored["last_seen_at"] == T2
    assert stored["report_hash"] != ""


async def test_a_batch_counts_each_outcome() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(1), row(2)], T1)

    tally = await import_rows(
        database, [row(1), row(2, commissionStatus="paid"), row(3)], T2
    )

    assert (tally.inserted, tally.updated, tally.unchanged) == (1, 1, 1)


async def test_the_run_lock_is_taken_before_anything_else_in_a_batch() -> None:
    database = FakeReportDatabase()

    await import_rows(database, [row(1), row(2), row(3)])

    assert database.committed.run_locks == 1


async def test_a_failing_insert_commits_nothing_from_the_batch() -> None:
    database = FakeReportDatabase(fail_insert_at=2)

    with pytest.raises(RuntimeError):
        await import_rows(database, [row(1), row(2)])

    assert database.committed.rows == {} and database.commits == 0


# --- matching ------------------------------------------------------------------------------


async def test_a_click_reference_equal_to_an_active_tracking_id_matches_the_link_and_offer() -> (
    None
):
    database = FakeReportDatabase()
    database.add_link(
        FakeLink("link-1", "offer-1", "https://www.awin1.com/cread.php", "ref-5001")
    )

    tally = await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert stored["affiliate_link_id"] == "link-1" and stored["offer_id"] == "offer-1"
    assert tally.unmatched == 0


@pytest.mark.parametrize("name", ["clickref", "clickref2", "clickref3", "clickref6"])
async def test_a_click_reference_inside_the_stored_link_destination_matches(
    name: str,
) -> None:
    database = FakeReportDatabase()
    url = f"https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&{name}=ref-5001"
    database.add_link(FakeLink("link-9", "offer-9", url))

    await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert stored["affiliate_link_id"] == "link-9" and stored["offer_id"] == "offer-9"


async def test_a_value_that_only_contains_the_reference_does_not_match() -> None:
    database = FakeReportDatabase()
    url = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=1&clickref=ref-50011"
    database.add_link(FakeLink("link-1", "offer-1", url))

    tally = await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None and tally.unmatched == 1


async def test_a_reference_in_a_non_click_parameter_does_not_match() -> None:
    database = FakeReportDatabase()
    url = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=1&ued=ref-5001"
    database.add_link(FakeLink("link-1", "offer-1", url))

    await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None


async def test_the_tracking_id_wins_over_a_destination_match() -> None:
    database = FakeReportDatabase()
    url = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=1&clickref=ref-5001"
    database.add_link(FakeLink("by-url", "offer-u", url))
    database.add_link(FakeLink("by-id", "offer-i", "https://x.example", "ref-5001"))

    await import_rows(database, [row(5001)])

    assert database.row("5001")["affiliate_link_id"] == "by-id"


async def test_an_inactive_or_foreign_provider_link_never_matches() -> None:
    database = FakeReportDatabase()
    database.add_link(
        FakeLink("off", "o1", "https://x.example", "ref-5001", active=False)
    )
    database.add_link(
        FakeLink("other", "o2", "https://x.example", "ref-5001", provider="direct")
    )

    await import_rows(database, [row(5001)])

    assert database.row("5001")["offer_id"] is None


async def test_two_links_claiming_one_reference_are_ambiguous_and_leave_both_null() -> (
    None
):
    database = FakeReportDatabase()
    database.add_link(FakeLink("a", "o1", "https://x.example", "ref-5001"))
    database.add_link(FakeLink("b", "o2", "https://y.example", "ref-5001"))

    tally = await import_rows(database, [row(5001)])

    stored = database.row("5001")
    assert stored["offer_id"] is None and stored["affiliate_link_id"] is None
    assert tally.unmatched == 1


async def test_a_row_with_no_click_reference_is_kept_unmatched() -> None:
    database = FakeReportDatabase()
    database.add_link(FakeLink("a", "o1", "https://x.example", "ref-5001"))

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


async def test_a_changed_row_is_matched_again_and_an_unchanged_one_is_not() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(5001, commissionStatus="pending")], T1)
    assert database.row("5001")["offer_id"] is None
    database.add_link(FakeLink("late", "offer-late", "https://x.example", "ref-5001"))

    await import_rows(database, [row(5001, commissionStatus="pending")], T2)
    assert (
        database.row("5001")["offer_id"] is None
    )  # unchanged: only last_seen_at moves

    await import_rows(database, [row(5001, commissionStatus="approved")], T2)
    assert database.row("5001")["offer_id"] == "offer-late"


async def test_an_unchanged_row_that_is_unmatched_still_counts_as_unmatched() -> None:
    database = FakeReportDatabase()
    await import_rows(database, [row(5001)], T1)

    tally = await import_rows(database, [row(5001)], T2)

    assert tally.unchanged == 1 and tally.unmatched == 1


# --- the tally -----------------------------------------------------------------------------


def test_a_tally_is_immutable_and_merges_skip_reasons() -> None:
    first = ReportTally(inserted=1).skip(SkipReason.UNKNOWN_STATUS)
    second = ReportTally(updated=2, unmatched=1).skip(SkipReason.UNKNOWN_STATUS)

    merged = first.merged(second)

    assert (merged.inserted, merged.updated, merged.unmatched) == (1, 2, 1)
    assert dict(merged.skipped) == {"unknown_status": 2} and merged.skipped_total == 2
    assert dict(first.skipped) == {"unknown_status": 1}
    assert CommissionStatus.PAID.value == "paid"
