"""Made-up Awin items for the importer tests. Every value here is invented; no real brand or URL."""

from datetime import UTC, datetime
from typing import Any

from app.services.awin_promotions import Promotion, parse_promotion

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
CODE = "SAVE10-SECRET"
TRACKING = "https://www.awin1.com/cread.php?awinmid=77&awinaffid=424242&ued=x"


def raw_item(promotion_id: int = 1001, **changes: Any) -> dict[str, Any]:
    """A valid raw item; ``changes`` overrides keys. Titles and urls differ per id."""
    item: dict[str, Any] = {
        "promotionId": promotion_id,
        "type": "voucher",
        "advertiser": {"id": 77, "name": "Acme Stores", "joined": True},
        "title": f"10% off everything ({promotion_id})",
        "description": "<p>Save <b>10%</b> on all orders.</p><p>Ends soon.</p>",
        "terms": "New customers only.",
        "startDate": "2026-11-01T00:00:00.000",
        "endDate": "2026-12-01T00:00:00.000",
        "dateAdded": "2026-10-30T00:00:00.000",
        "url": f"https://www.acme.example/sale-{promotion_id}",
        "urlTracking": f"{TRACKING}&p={promotion_id}",
        "regions": {
            "all": False,
            "list": [{"name": "United Kingdom", "countryCode": "GB"}],
        },
        "voucher": {"code": CODE, "exclusive": False, "attributable": True},
    }
    return {**item, **changes}


def promotion(promotion_id: int = 1001, **changes: Any) -> Promotion:
    result = parse_promotion(raw_item(promotion_id, **changes), NOW)
    assert isinstance(result, Promotion), result
    return result


def raw_page(first_id: int, count: int, **changes: Any) -> list[dict[str, Any]]:
    return [raw_item(first_id + n, **changes) for n in range(count)]
