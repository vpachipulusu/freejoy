"""A run that is not a dry run may only ask for joined programmes (FREEJ-52)."""

from typing import Any

import pytest

from app.core.awin_client import Membership
from app.services.awin_import_run import ImportFailed, RunOptions, run_import
from tests.awin_samples import NOW, PUBLISHER_ID, raw_page
from tests.fakes_awin import FakeAwinClient
from tests.fakes_awin_import import FakeDatabase


async def go(client: FakeAwinClient, db: FakeDatabase, **options: Any) -> Any:
    return await run_import(
        client, db.unit, RunOptions(publisher_id=PUBLISHER_ID, **options), NOW
    )


@pytest.mark.parametrize("membership", ["all", "notJoined"])
async def test_a_real_run_with_another_membership_is_refused_before_any_fetch(
    membership: Membership,
) -> None:
    client = FakeAwinClient([raw_page(1, 3)])
    db = FakeDatabase()

    with pytest.raises(ImportFailed) as caught:
        await go(client, db, membership=membership)

    assert caught.value.cause == "membership_not_joined"
    assert caught.value.stage == "before the first fetch"
    assert caught.value.report.pages == 0 and caught.value.report.received == 0
    assert client.requests == []
    assert db.units_opened == 0 and db.committed.offers == {}


@pytest.mark.parametrize("membership", ["all", "notJoined"])
async def test_a_dry_run_may_ask_for_another_membership(
    membership: Membership,
) -> None:
    client = FakeAwinClient([raw_page(1, 3)])
    db = FakeDatabase()

    report = await go(client, db, membership=membership, dry_run=True)

    assert client.requests[0][0].membership == membership
    assert report.received == 3 and db.units_opened == 0


async def test_the_default_is_joined_and_a_real_run_imports() -> None:
    client = FakeAwinClient([raw_page(1, 3)])
    db = FakeDatabase()

    report = await go(client, db)

    assert RunOptions(publisher_id=1).membership == "joined"
    assert client.requests[0][0].membership == "joined"
    assert report.tally.created == 3
