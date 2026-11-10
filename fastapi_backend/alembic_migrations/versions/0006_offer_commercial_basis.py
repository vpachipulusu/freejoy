"""Offer commercial basis

Adds ``offers.commercial_basis`` (``EDITORIAL``, ``AFFILIATE`` or ``SPONSORED``; model in
``app/models/platform/offer.py``): the honest statement of the commercial relationship behind a listing.
It is NOT NULL with no server default, so every writer must state it.

The backfill runs inside this migration, in three steps (add nullable, fill, then ``SET NOT NULL``):
an offer with at least one *active* link whose provider is an affiliate network becomes ``AFFILIATE``;
every other existing offer (only ``direct`` links, only inactive links, ``sample`` links or no links)
becomes ``EDITORIAL``. Nothing is ever backfilled to ``SPONSORED``: that is a claim only an editor can make.

The network list below is FROZEN on purpose. It is the registry in ``app/core/affiliate_hosts.py``
(``PROVIDER_HOSTS``) as it stood when this revision was written. It is not imported, so adding a network
to that registry later cannot change what this migration did to a database it has already run on, or
would do to one that has not run it yet.

The downgrade drops the column and its CHECK, which loses every editor's choice of basis (a later upgrade
backfills again from the links, so a ``SPONSORED`` marking does not come back): run it only against a
throwaway database.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the affiliate networks in ``PROVIDER_HOSTS`` at the time of writing (see the docstring).
NETWORK_PROVIDERS = ("awin", "cj", "partnerize", "rakuten", "tradedoubler")
CHECK_NAME = "ck_offers_commercial_basis_valid"
CHECK = "commercial_basis IN ('EDITORIAL', 'AFFILIATE', 'SPONSORED')"


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column(
            "commercial_basis",
            sa.Enum(
                "EDITORIAL",
                "AFFILIATE",
                "SPONSORED",
                name="commercialbasis",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=True,
        ),
    )
    networks = ", ".join(f"'{provider}'" for provider in NETWORK_PROVIDERS)
    op.execute(
        sa.text(
            "UPDATE offers SET commercial_basis = CASE WHEN EXISTS ("
            "SELECT 1 FROM affiliate_links"
            " WHERE affiliate_links.offer_id = offers.id"
            f" AND affiliate_links.active AND affiliate_links.provider IN ({networks})"
            ") THEN 'AFFILIATE' ELSE 'EDITORIAL' END"
        )
    )
    op.alter_column("offers", "commercial_basis", nullable=False)
    op.create_check_constraint(CHECK_NAME, "offers", CHECK)


def downgrade() -> None:
    op.drop_constraint(CHECK_NAME, "offers", type_="check")
    op.drop_column("offers", "commercial_basis")
