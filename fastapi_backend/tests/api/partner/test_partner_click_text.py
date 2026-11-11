"""The public click text states no cap figure; the internal docs do, tied to the code (FREEJ-59).

Recording is rate limited per offer (``OUTBOUND_OFFER_POLICY``). The retailer-facing text (the response ``definition``,
the partner schema and the public page) says only that some clicks are not recorded in a very busy minute. The figure
lives in the internal developer docs, and this test keeps it equal to the policy the click endpoint enforces.
"""

from pathlib import Path

from app.api.v1 import catalogue_click
from app.core.rate_limit import OUTBOUND_OFFER_BOT_POLICY, OUTBOUND_OFFER_POLICY

REPO = Path(__file__).resolve().parents[4]
DOCS = (
    REPO / "docs" / "developer" / "backend-partner-api.md",
    REPO / "docs" / "developer" / "freejoy-affiliate.md",
)
PUBLIC_SCHEMAS = (
    REPO / "docs" / "partner" / "openapi.json",
    REPO / "nextjs-frontend" / "public" / "partner-openapi.json",
)
ADR = REPO / "docs" / "adr" / "0007-rate-limit-keys.md"
ADMIN_ANALYTICS = REPO / "docs" / "developer" / "backend-admin-analytics.md"
BOT = OUTBOUND_OFFER_BOT_POLICY
MAIN = OUTBOUND_OFFER_POLICY


def words(path: Path) -> str:
    """The file's text with every run of whitespace collapsed, so a wrapped line still matches."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_click_endpoint_enforces_the_policy_the_docs_quote() -> None:
    assert catalogue_click.OUTBOUND_OFFER_POLICY is OUTBOUND_OFFER_POLICY
    assert OUTBOUND_OFFER_POLICY.window_seconds == 60


def test_each_internal_doc_states_the_cap_the_code_enforces() -> None:
    phrase = f"{OUTBOUND_OFFER_POLICY.limit} clicks for one offer in a minute"
    for doc in DOCS:
        assert phrase in doc.read_text(encoding="utf-8").replace("\r\n", "\n"), doc.name


def test_each_internal_doc_states_the_bot_cap_the_code_enforces() -> None:
    assert BOT.scope != MAIN.scope
    for doc in DOCS:
        text = words(doc).replace("per minute", "a minute")
        assert f"`OUTBOUND_OFFER_BOT_POLICY`, {BOT.limit} a minute" in text, doc.name


def test_the_adr_states_both_per_offer_keys_with_the_figures_the_code_enforces() -> (
    None
):
    text = words(ADR)
    for policy in (MAIN, BOT):
        key = f"`rl:{policy.scope}:offer:{{offer id}}` ({policy.limit} per minute)"
        assert key in text, key


def test_the_admin_analytics_doc_states_both_figures_with_their_policy_names() -> None:
    text = words(ADMIN_ANALYTICS)
    bot = (
        f"{BOT.limit} `BOT` clicks a minute for one offer (`OUTBOUND_OFFER_BOT_POLICY`"
    )
    main = f"the {MAIN.limit} a minute for every other class (`OUTBOUND_OFFER_POLICY`)"
    assert bot in text and main in text


def test_the_public_schemas_do_not_state_the_cap_or_how_the_limiter_behaves() -> None:
    for path in PUBLIC_SCHEMAS:
        schema = path.read_text(encoding="utf-8")

        # The figure must not be stated as a click rate. (The bare number can appear: the offer fields have length
        # limits of their own, for example maxLength 600 on a paragraph, which has nothing to do with clicks.)
        for phrasing in (
            f"{MAIN.limit} clicks",
            f"{MAIN.limit} a minute",
            f"{MAIN.limit} per minute",
            f"{MAIN.limit} times",
        ):
            assert phrasing not in schema, (path, phrasing)
        assert "still sent on" not in schema, path
        for internal in ("bucket", "lower bound", "outbound_offer"):
            assert internal not in schema, (path, internal)
        assert "rate limited for each offer" in schema, path
