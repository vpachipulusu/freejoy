"""The public click text states no cap figure; the internal docs do, tied to the code (FREEJ-59).

Recording is rate limited per offer (``OUTBOUND_OFFER_POLICY``). The retailer-facing text (the response ``definition``,
the partner schema and the public page) says only that some clicks are not recorded in a very busy minute. The figure
lives in the internal developer docs, and this test keeps it equal to the policy the click endpoint enforces.
"""

from pathlib import Path

from app.api.v1 import catalogue_click
from app.core.rate_limit import OUTBOUND_OFFER_POLICY

REPO = Path(__file__).resolve().parents[4]
DOCS = (
    REPO / "docs" / "developer" / "backend-partner-api.md",
    REPO / "docs" / "developer" / "freejoy-affiliate.md",
)


def test_the_click_endpoint_enforces_the_policy_the_docs_quote() -> None:
    assert catalogue_click.OUTBOUND_OFFER_POLICY is OUTBOUND_OFFER_POLICY
    assert OUTBOUND_OFFER_POLICY.window_seconds == 60


def test_each_internal_doc_states_the_cap_the_code_enforces() -> None:
    phrase = f"{OUTBOUND_OFFER_POLICY.limit} clicks for one offer in a minute"
    for doc in DOCS:
        assert phrase in doc.read_text(encoding="utf-8").replace("\r\n", "\n"), doc.name


def test_the_public_schema_does_not_state_the_cap_or_how_the_limiter_behaves() -> None:
    schema = (REPO / "docs" / "partner" / "openapi.json").read_text(encoding="utf-8")

    assert str(OUTBOUND_OFFER_POLICY.limit) not in schema
    assert "still sent on" not in schema
    assert "rate limited for each offer" in schema
