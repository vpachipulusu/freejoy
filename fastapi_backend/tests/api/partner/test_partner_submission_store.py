"""Every partner repository method takes the merchant, and the SQL it sends carries it (ADR-013, FREEJ-58).

The behaviour against Postgres, with two real merchants, is ``test_partner_tenancy_e2e.py``. These checks need no
database: the signature of every public method of the store, the statements a recording session is asked to run, and
the things the store must never contain (a delete, the status column in a write).
"""

import dataclasses
import inspect
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import NoResultFound

from app.partner import submission_store
from app.partner.submission_store import (
    CategoryReader,
    ImportRow,
    LinkRow,
    MerchantFacts,
    OfferRow,
    PartnerSubmissionStore,
    SubmissionRow,
    ViewRow,
)

MERCHANT = "merchant-under-test"
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def public_methods(cls: type) -> dict[str, Any]:
    return {
        name: member
        for name, member in inspect.getmembers(cls, inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_public_method_of_the_store_takes_merchant_id_first() -> None:
    methods = public_methods(PartnerSubmissionStore)

    assert methods  # the check is looking at something
    for name, method in methods.items():
        parameters = list(inspect.signature(method).parameters)
        assert parameters[:2] == ["self", "merchant_id"], name


def test_merchant_id_is_required_not_defaulted() -> None:
    for name, method in public_methods(PartnerSubmissionStore).items():
        parameter = inspect.signature(method).parameters["merchant_id"]
        assert parameter.default is inspect.Parameter.empty, name


def test_the_category_reader_is_the_one_reader_with_no_tenant_and_reads_one_table() -> (
    None
):
    assert list(public_methods(CategoryReader)) == ["enabled_category_id"]


def dummy(cls: type) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        values[field.name] = (
            NOW
            if "datetime" in str(field.type)
            else False
            if field.type is bool
            else 1
            if field.type is int
            else "x"
        )
    return values


class Rows:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def first(self) -> SimpleNamespace | None:
        return self._rows[0] if self._rows else None

    def one(self) -> SimpleNamespace:
        if not self._rows:
            raise NoResultFound
        return self._rows[0]

    def __iter__(self) -> Any:
        return iter(self._rows)


def row(cls: type) -> SimpleNamespace:
    values = dummy(cls)
    return SimpleNamespace(_mapping=values, **values)


class RecordingSession:
    def __init__(self, cls: type | None = None) -> None:
        self.statements: list[Any] = []
        self._cls = cls

    async def execute(self, statement: Any) -> Rows:
        self.statements.append(statement)
        return Rows([row(self._cls)] if self._cls else [])

    async def scalar(self, statement: Any) -> int:
        self.statements.append(statement)
        return 0

    def compiled_params(self) -> list[Any]:
        out: list[Any] = []
        for statement in self.statements:
            out.extend(statement.compile(dialect=postgresql.dialect()).params.values())
        return out

    def sql(self) -> str:
        return " ".join(
            str(s.compile(dialect=postgresql.dialect())) for s in self.statements
        )


# Plain ``INSERT ... VALUES`` of a row that has its own ``merchant_id`` column (or a lock keyed by it): the merchant is
# a value there, not a filter. Every other statement must put it in its WHERE clause.
MERCHANT_IS_A_VALUE = {
    "insert_offer",
    "audit",
    "lock_creation",
    "merchant_facts",
}  # merchant_facts reads the merchant by its own id


def carries_the_merchant(session: RecordingSession) -> bool:
    return any(MERCHANT in str(value) for value in session.compiled_params())


def merchant_is_in_every_where(session: RecordingSession) -> bool:
    """Every statement filters on a ``merchant_id`` bound to the calling merchant, in its WHERE clause."""
    for statement in session.statements:
        compiled = statement.compile(dialect=postgresql.dialect())
        text = str(compiled)
        parts = re.split(r"\s+WHERE\s+", text, maxsplit=1)
        where = parts[1] if len(parts) == 2 else ""
        bound = [
            name
            for name, value in compiled.params.items()
            if value == MERCHANT and f"%({name})s" in where
        ]
        if "merchant_id" not in where or not bound:
            return False
    return True


CALLS: dict[str, tuple[type | None, Any]] = {
    "merchant_facts": (MerchantFacts, lambda s: s.merchant_facts(MERCHANT)),
    "find_submission": (SubmissionRow, lambda s: s.find_submission(MERCHANT, "ref")),
    "find_submission_locked": (
        SubmissionRow,
        lambda s: s.find_submission(MERCHANT, "ref", lock=True),
    ),
    "lock_offer": (OfferRow, lambda s: s.lock_offer(MERCHANT, "offer")),
    "links": (LinkRow, lambda s: s.links(MERCHANT, "offer")),
    "import_row": (ImportRow, lambda s: s.import_row(MERCHANT, "offer", lock=True)),
    "lock_creation": (None, lambda s: s.lock_creation(MERCHANT)),
    "count_open": (None, lambda s: s.count_open(MERCHANT)),
    "count_created_today": (None, lambda s: s.count_created_today(MERCHANT)),
    "insert_offer": (OfferRow, lambda s: s.insert_offer(MERCHANT, {"title": "t"})),
    "update_offer": (
        OfferRow,
        lambda s: s.update_offer(MERCHANT, "offer", {"title": "t"}),
    ),
    "end_offer": (ViewRow, lambda s: s.end_offer(MERCHANT, "offer")),
    "insert_link": (
        LinkRow,
        lambda s: s.insert_link(MERCHANT, "offer", "https://x.example.com/"),
    ),
    "update_link": (
        LinkRow,
        lambda s: s.update_link(MERCHANT, "offer", "link", "https://x.example.com/"),
    ),
    "insert_import": (
        ImportRow,
        lambda s: s.insert_import(
            MERCHANT,
            offer_id="offer",
            external_id="e",
            digest="d",
            applied_content_hash="h",
        ),
    ),
    "update_import": (
        None,
        lambda s: s.update_import(MERCHANT, "offer", {"source_hash": "h"}),
    ),
    "insert_submission": (
        SubmissionRow,
        lambda s: s.insert_submission(
            MERCHANT,
            submission_id="s",
            ref="r",
            offer_id="o",
            content_hash="h",
            key_id="k",
        ),
    ),
    "update_submission": (
        None,
        lambda s: s.update_submission(MERCHANT, "sub", {"content_hash": "h"}),
    ),
    "audit": (
        None,
        lambda s: s.audit(
            MERCHANT, action="partner.offers.submit", offer_id="o", metadata={}
        ),
    ),
    "view": (ViewRow, lambda s: s.view(MERCHANT, "ref")),
    "list_views": (ViewRow, lambda s: s.list_views(MERCHANT, 10, 0)),
}


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_the_statement_each_method_sends_carries_the_merchant(name: str) -> None:
    cls, call = CALLS[name]
    session = RecordingSession(cls)
    store = PartnerSubmissionStore(session)  # type: ignore[arg-type]

    await call(store)

    assert session.statements, name
    assert carries_the_merchant(session), name
    if name not in MERCHANT_IS_A_VALUE:
        assert merchant_is_in_every_where(session), name


async def test_the_set_of_methods_checked_is_every_public_method() -> None:
    covered = {name.removesuffix("_locked") for name in CALLS}

    assert covered == set(public_methods(PartnerSubmissionStore))


async def test_a_write_by_id_for_another_merchant_finds_no_row_to_return() -> None:
    session = RecordingSession(None)
    store = PartnerSubmissionStore(session)  # type: ignore[arg-type]

    with pytest.raises(NoResultFound):
        await store.update_offer(MERCHANT, "offer", {"title": "t"})
    with pytest.raises(NoResultFound):
        await store.insert_link(MERCHANT, "offer", "https://x.example.com/")
    with pytest.raises(NoResultFound):
        await store.insert_import(
            MERCHANT,
            offer_id="offer",
            external_id="e",
            digest="d",
            applied_content_hash="h",
        )
    assert await store.end_offer(MERCHANT, "offer") is False


async def test_a_new_offer_never_names_the_status_the_featured_flag_or_the_basis() -> (
    None
):
    session = RecordingSession(OfferRow)
    store = PartnerSubmissionStore(session)  # type: ignore[arg-type]

    await store.insert_offer(MERCHANT, {"title": "t", "slug": "s"})
    insert = str(session.statements[0].compile(dialect=postgresql.dialect())).split(
        "RETURNING"
    )[0]

    for forbidden in (
        "status",
        "featured",
        "last_verified_at",
        "commercial_basis",
        "freshness",
        "publisher_notes",
    ):
        assert forbidden not in insert, forbidden


async def test_updating_and_ending_an_offer_never_names_the_status_or_the_editors_columns() -> (
    None
):
    session = RecordingSession(OfferRow)
    store = PartnerSubmissionStore(session)  # type: ignore[arg-type]

    await store.update_offer(MERCHANT, "offer", {"title": "t"})
    await store.end_offer(MERCHANT, "offer")
    set_clauses = [
        str(s.compile(dialect=postgresql.dialect()))
        .split("RETURNING")[0]
        .split(" WHERE ")[0]
        for s in session.statements
    ]

    for clause in set_clauses:
        for forbidden in (
            "status",
            "featured",
            "last_verified_at",
            "commercial_basis",
            "slug",
            "merchant_id",
        ):
            assert forbidden not in clause, (forbidden, clause)


def test_the_store_has_no_delete_and_no_provider_other_than_partner_and_direct() -> (
    None
):
    source = inspect.getsource(submission_store)

    assert "delete(" not in source and "DELETE" not in source
    assert (
        submission_store.PROVIDER == "partner" and submission_store.DIRECT == "direct"
    )


def test_every_column_the_store_selects_is_a_column_of_the_core_definition() -> None:
    from app.partner.tables import offers

    assert set(submission_store.OFFER_FIELDS) <= {c.name for c in offers.columns}
