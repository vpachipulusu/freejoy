"""OutboundClickService: which offers and destinations may be followed, and the one-row rule."""

from datetime import timedelta

import pytest

from app.models.platform.click_event import UserAgentClass
from app.models.platform.offer import CommercialBasis, OfferStatus
from app.repositories.catalogue import OfferRecord
from app.repositories.outbound_clicks import LinkRecord
from app.services.catalogue import CatalogueService, ExpiredOffer
from app.services.outbound_clicks import (
    ClickRedirect,
    DisclosureNeeded,
    OutboundClickService,
    clean_referrer_host,
)
from tests.fakes_catalogue import NOW, FakeCatalogueRepository, make_offer
from tests.fakes_outbound import FakeClickStore, FakeCommitter, make_link

AWIN = "https://www.awin1.com/cread.php?awinmid=1&awinaffid=2"


def build(
    offer: OfferRecord | None,
    links: list[LinkRecord],
    allow_sample: bool = False,
) -> tuple[OutboundClickService, FakeClickStore, FakeCommitter]:
    store = FakeClickStore(links)
    committer = FakeCommitter()
    catalogue = CatalogueService(
        FakeCatalogueRepository(by_id=offer), clock=lambda: NOW
    )
    service = OutboundClickService(
        catalogue, store, committer, allow_sample_provider=allow_sample
    )
    return service, store, committer


async def test_a_live_offer_returns_the_destination_and_writes_one_click() -> None:
    service, store, committer = build(make_offer(), [make_link()])

    outcome = await service.record_click(
        "offer_1", "news.example", UserAgentClass.MOBILE
    )

    assert outcome == ClickRedirect(AWIN)
    assert store.clicks == [
        {
            "offer_id": "offer_1",
            "affiliate_link_id": "link_1",
            "referrer_host": "news.example",
            "user_agent_class": UserAgentClass.MOBILE,
        }
    ]
    assert committer.commits == 1


async def test_the_query_string_of_the_stored_destination_is_kept_byte_for_byte() -> (
    None
):
    url = "https://www.awin1.com/cread.php?a=1&b=%20x&c=https%3A%2F%2Fevil.example"
    service, _, _ = build(make_offer(), [make_link(destination_url=url)])

    outcome = await service.record_click("offer_1", None, None)

    assert outcome == ClickRedirect(url)


@pytest.mark.parametrize("status", [OfferStatus.DRAFT, OfferStatus.ARCHIVED])
async def test_unpublished_offers_answer_none_and_write_nothing(
    status: OfferStatus,
) -> None:
    service, store, committer = build(
        make_offer(status=status, last_verified_at=None), [make_link()]
    )

    assert await service.record_click("offer_1", None, None) is None
    assert store.clicks == [] and committer.commits == 0
    assert store.asked == []


async def test_an_unknown_offer_answers_none_and_writes_nothing() -> None:
    service, store, committer = build(None, [make_link()])

    assert await service.record_click("nope", None, None) is None
    assert store.clicks == [] and committer.commits == 0


@pytest.mark.parametrize(
    "offer",
    [
        make_offer(end_at=NOW),
        make_offer(end_at=NOW - timedelta(days=1)),
        make_offer(status=OfferStatus.EXPIRED),
    ],
)
async def test_a_previously_public_ended_offer_answers_expired_and_writes_nothing(
    offer: OfferRecord,
) -> None:
    service, store, committer = build(offer, [make_link()])

    outcome = await service.record_click("offer_1", None, None)

    assert isinstance(outcome, ExpiredOffer)
    assert outcome.slug == "free-shampoo"
    assert store.clicks == [] and committer.commits == 0


async def test_an_expired_row_that_never_went_live_looks_like_an_unknown_offer() -> (
    None
):
    service, store, _ = build(
        make_offer(status=OfferStatus.EXPIRED, last_verified_at=None), [make_link()]
    )

    assert await service.record_click("offer_1", None, None) is None
    assert store.clicks == []


async def test_a_live_offer_without_an_active_link_answers_none() -> None:
    service, store, committer = build(make_offer(), [])

    assert await service.record_click("offer_1", None, None) is None
    assert store.clicks == [] and committer.commits == 0


HOSTILE = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "http://www.awin1.com/cread.php",
    "//evil.example/x",
    "https:evil.example",
    "https://www.awin1.com@evil.example/",
    "https://www.awin1.com:pw@evil.example/",
    "https://evil.example\\@www.awin1.com/",
    "https://www.awin1.com\\.evil.example/",
    "https://www.awin1.com/x\r\nSet-Cookie: a=b",
    "https://www.awin1.com/x%0d%0aSet-Cookie:a=b",
    "https://www.awin1.com/x\nLocation: https://evil.example",
    "https://www.awin1.com/x y",
    "https://awin1.com.evil.example/",
    "https://evil.example/?next=https://www.awin1.com/",
    "https://evil.example/",
    "https://127.0.0.1/",
    "https://www.awin1.com:8443/",
    "https://www.awin1.com./cread.php",
    "https://www.awin1.com.123/x",
    "https://xn--win1-nza.com/cread.php",
    "https://аwin1.com/cread.php",
    "https://www.awin1.com。evil.example/x",
    "",
]


@pytest.mark.parametrize("url", HOSTILE)
async def test_a_hostile_stored_destination_never_becomes_a_redirect(url: str) -> None:
    service, store, committer = build(make_offer(), [make_link(destination_url=url)])

    assert await service.record_click("offer_1", None, None) is None
    assert store.clicks == [] and committer.commits == 0


async def test_an_unusable_link_is_skipped_for_the_next_usable_one() -> None:
    bad = make_link(id="link_bad", destination_url="javascript:alert(1)")
    good = make_link(id="link_good")
    service, store, _ = build(make_offer(), [bad, good])

    outcome = await service.record_click("offer_1", None, None)

    assert outcome == ClickRedirect(AWIN)
    assert [click["affiliate_link_id"] for click in store.clicks] == ["link_good"]


async def test_a_host_the_provider_may_not_use_is_refused() -> None:
    link = make_link(provider="awin", destination_url="https://www.tradedoubler.com/x")
    service, store, _ = build(make_offer(), [link])

    assert await service.record_click("offer_1", None, None) is None
    assert store.clicks == []


async def test_a_direct_link_follows_only_the_merchant_website() -> None:
    on_site = make_link(
        provider="direct", destination_url="https://www.acme.example/free"
    )
    off_site = make_link(provider="direct", destination_url="https://evil.example/free")

    ok, _, _ = build(make_offer(), [on_site])
    refused, store, _ = build(make_offer(), [off_site])

    assert await ok.record_click("offer_1", None, None) == ClickRedirect(
        "https://www.acme.example/free"
    )
    assert await refused.record_click("offer_1", None, None) is None
    assert store.clicks == []


async def test_the_sample_provider_needs_the_sample_flag() -> None:
    link = make_link(provider="sample", destination_url="https://example.com/go/x")

    allowed, _, _ = build(make_offer(), [link], allow_sample=True)
    refused, store, _ = build(make_offer(), [link], allow_sample=False)

    assert await allowed.record_click("offer_1", None, None) == ClickRedirect(
        "https://example.com/go/x"
    )
    assert await refused.record_click("offer_1", None, None) is None
    assert store.clicks == []


async def test_a_bad_referrer_is_dropped_but_the_click_is_still_recorded() -> None:
    service, store, _ = build(make_offer(), [make_link()])

    outcome = await service.record_click("offer_1", "https://x.example/a?b=1", None)

    assert isinstance(outcome, ClickRedirect)
    assert store.clicks[0]["referrer_host"] is None


async def test_refusals_are_logged_without_the_destination(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "https://evil.example/?token=SECRET123"
    service, _, _ = build(make_offer(), [make_link(destination_url=secret)])

    with caplog.at_level("DEBUG", logger="yourapp.outbound"):
        await service.record_click("offer_1", None, None)

    assert "link_1" in caplog.text
    assert "SECRET123" not in caplog.text and "evil.example" not in caplog.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("News.Example", "news.example"),
        ("  www.google.co.uk ", "www.google.co.uk"),
        ("localhost", "localhost"),
        (None, None),
        ("", None),
        ("a b.example", None),
        ("news.example/path", None),
        ("news.example:8080", None),
        ("user@news.example", None),
        ("https://news.example", None),
        ("192.168.1.5", None),
        ("[::1]", None),
        ("-bad.example", None),
        ("a" * 64 + ".example", None),
        ("a." * 130 + "example", None),
        ("news.example\r\nX: y", None),
    ],
)
def test_clean_referrer_host(raw: str | None, expected: str | None) -> None:
    assert clean_referrer_host(raw) == expected


async def test_record_false_returns_the_destination_without_writing_a_click() -> None:
    service, store, committer = build(make_offer(), [make_link()])

    outcome = await service.record_click("offer_1", "news.example", None, record=False)

    assert outcome == ClickRedirect(AWIN)
    assert store.clicks == [] and committer.commits == 0


async def test_record_false_still_refuses_what_is_not_followable() -> None:
    hostile = make_link(destination_url="javascript:alert(1)")
    service, store, committer = build(make_offer(), [hostile])
    ended, _, _ = build(make_offer(end_at=NOW - timedelta(days=1)), [make_link()])

    assert await service.record_click("offer_1", None, None, record=False) is None
    assert isinstance(
        await ended.record_click("offer_1", None, None, record=False), ExpiredOffer
    )
    assert store.clicks == [] and committer.commits == 0


# --- the disclosure guard (FREEJ-60) -----------------------------------------------


async def test_an_undisclosed_click_on_a_commission_link_writes_nothing_and_returns_no_destination() -> (
    None
):
    service, store, committer = build(make_offer(), [make_link()])

    outcome = await service.record_click(
        "offer_1", "news.example", UserAgentClass.MOBILE, commission_disclosed=False
    )

    assert outcome == DisclosureNeeded("free-shampoo")
    assert store.clicks == []
    assert committer.commits == 0


async def test_the_guard_applies_even_when_the_offer_cap_is_spent() -> None:
    service, store, _ = build(make_offer(), [make_link()])

    outcome = await service.record_click(
        "offer_1", None, None, record=False, commission_disclosed=False
    )

    assert outcome == DisclosureNeeded("free-shampoo")
    assert store.clicks == []


async def test_an_undisclosed_click_on_a_non_commission_link_redirects_and_counts_once() -> (
    None
):
    link = make_link(
        provider="direct", destination_url="https://www.acme.example/free-sample"
    )
    service, store, committer = build(make_offer(), [link])

    outcome = await service.record_click(
        "offer_1", None, None, commission_disclosed=False
    )

    assert outcome == ClickRedirect("https://www.acme.example/free-sample")
    assert len(store.clicks) == 1
    assert committer.commits == 1


async def test_an_undisclosed_click_on_a_direct_link_of_an_affiliate_offer_is_refused() -> (
    None
):
    link = make_link(
        provider="direct", destination_url="https://www.acme.example/free-sample"
    )
    offer = make_offer(commercial_basis=CommercialBasis.AFFILIATE)
    service, store, _ = build(offer, [link])

    outcome = await service.record_click(
        "offer_1", None, None, commission_disclosed=False
    )

    assert outcome == DisclosureNeeded("free-shampoo")
    assert store.clicks == []


@pytest.mark.parametrize("passed", ["absent", True])
async def test_a_disclosed_or_absent_flag_leaves_the_click_unchanged(
    passed: object,
) -> None:
    service, store, committer = build(make_offer(), [make_link()])
    extra: dict[str, bool] = (
        {} if passed == "absent" else {"commission_disclosed": True}
    )

    outcome = await service.record_click("offer_1", None, None, **extra)

    assert outcome == ClickRedirect(AWIN)
    assert len(store.clicks) == 1
    assert committer.commits == 1


async def test_the_guard_runs_after_the_ordinary_refusals() -> None:
    expired = make_offer(end_at=NOW - timedelta(days=1))
    service, store, _ = build(expired, [make_link()])
    missing, _, _ = build(None, [make_link()])
    no_link, _, _ = build(make_offer(), [])

    assert isinstance(
        await service.record_click("offer_1", None, None, commission_disclosed=False),
        ExpiredOffer,
    )
    assert (
        await missing.record_click("offer_1", None, None, commission_disclosed=False)
        is None
    )
    assert (
        await no_link.record_click("offer_1", None, None, commission_disclosed=False)
        is None
    )
    assert store.clicks == []
