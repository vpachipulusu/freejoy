"""A valid partner key opens nothing outside the partner surface (ADR-013).

For every route of the main application a request carrying a valid partner key and nothing else never gets a
2xx and never carries the partner surface header: the key is not a credential of the internal API (which asks
for ``X-Internal-Key``), and the main app does not know the partner store. The routes that are open by design
(the docs while enabled, the Stripe webhook) are held to the weaker rule that the key changes nothing: the
answer is the one a request with no key gets.
"""

import pytest

from app.main import app
from tests.api.partner.support import call_raw
from tests.confinement_walker import (
    OPEN_ROUTES,
    PATH_PARAMETER,
    STRIPE_WEBHOOK_PATH,
    context_path,
    docs_paths,
    main_app_contexts,
)
from tests.partner_world import PartnerWorld

SURFACE_HEADER = "x-freejoy-surface"
# /health answers from the database; it is open by design and has no use for a credential.
NOT_CALLED = frozenset({"/health"})


def _requests() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for context in main_app_contexts(app):
        path = context_path(context) or ""
        for method in sorted((context.methods or set()) - {"OPTIONS"}):
            pairs.append((method, PATH_PARAMETER.sub("x", path)))
    return pairs


def test_there_are_enough_routes_to_make_this_meaningful() -> None:
    assert len(_requests()) > 60


@pytest.mark.parametrize(("method", "path"), _requests())
async def test_a_valid_partner_key_alone_gets_nothing_from_a_main_app_route(
    world: PartnerWorld, method: str, path: str
) -> None:
    open_paths = OPEN_ROUTES | docs_paths(app) | {STRIPE_WEBHOOK_PATH}
    template = next(
        p
        for c in main_app_contexts(app)
        if PATH_PARAMETER.sub("x", p := context_path(c) or "") == path
    )
    if template in NOT_CALLED:
        pytest.skip("open by design and answered from the database")

    keyed = await call_raw(app, method, path, headers=world.headers())

    assert SURFACE_HEADER not in keyed.header_names()
    if template in open_paths:
        bare = await call_raw(app, method, path)
        assert keyed.status == bare.status
    else:
        assert keyed.status == 401
