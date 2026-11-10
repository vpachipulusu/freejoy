"""Made-up Awin transaction rows for the report ingest tests. Every value here is invented."""

from datetime import UTC, datetime
from typing import Any

from app.services.awin_transactions import ReportRow, parse_transaction

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
ADVERTISER_ID = 77


def raw_row(transaction_id: int = 5001, **changes: Any) -> dict[str, Any]:
    """A valid raw row; ``changes`` overrides keys."""
    row: dict[str, Any] = {
        "id": transaction_id,
        "advertiserId": ADVERTISER_ID,
        "transactionDate": "2026-11-03T10:15:00",
        "commissionStatus": "approved",
        "commissionAmount": {"amount": 1.5, "currency": "GBP"},
        "saleAmount": {"amount": 30.0, "currency": "GBP"},
        "clickRefs": {"clickRef": f"ref-{transaction_id}"},
    }
    return {**row, **changes}


def row(transaction_id: int = 5001, **changes: Any) -> ReportRow:
    result = parse_transaction(raw_row(transaction_id, **changes))
    assert isinstance(result, ReportRow), result
    return result


def raw_rows(first_id: int, count: int, **changes: Any) -> list[dict[str, Any]]:
    return [raw_row(first_id + n, **changes) for n in range(count)]
