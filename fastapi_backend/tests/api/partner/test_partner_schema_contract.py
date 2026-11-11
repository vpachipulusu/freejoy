"""The retailer-facing contract explains what the fields mean, in retailer words (FREEJ-58)."""

import re

from app.partner.app import create_partner_api

INTERNAL_WORDS = re.compile(
    r"\b(fingerprint|database|trigger|audit|offer_imports|content_hash|pending_hash|first_published_at|"
    r"advisory|FOR UPDATE|merchants\.website)\b",
    re.IGNORECASE,
)
# The status enumerations are internal; the lower-case words are ordinary English, so case matters here.
INTERNAL_STATUSES = re.compile(r"\b(DRAFT|PUBLISHED|ARCHIVED|EXPIRED)\b")


def schema() -> dict[str, object]:
    return create_partner_api().openapi()


def properties() -> list[tuple[str, str, dict[str, object]]]:
    found = []
    for name, model in schema()["components"]["schemas"].items():  # type: ignore[index, attr-defined]
        for prop, definition in model.get("properties", {}).items():
            found.append((name, prop, definition))
    return found


def test_every_property_of_every_partner_model_has_a_description() -> None:
    props = properties()

    assert len(props) > 40  # the check is looking at something
    missing = [f"{m}.{p}" for m, p, d in props if not d.get("description")]
    assert missing == []


def test_the_semantics_a_retailer_needs_are_in_the_contract() -> None:
    text = str(schema())

    for phrase in (
        "declined",
        "final",
        "does not mean the update was applied",
        "is answered unchanged",
        "open_submission_cap",
        "clears only as our editors review them",
        "daily_write_budget",
        "offer_closed",
        "permanent",
        "cannot be undone",
    ):
        assert phrase in text, phrase


def test_no_internal_wording_is_in_the_public_contract() -> None:
    text = str(schema()["components"]) + str(schema()["paths"])  # type: ignore[index]

    assert INTERNAL_WORDS.findall(text) == []
    assert INTERNAL_STATUSES.findall(text) == []


def test_the_operations_have_descriptions() -> None:
    for path, item in schema()["paths"].items():  # type: ignore[attr-defined]
        for method, operation in item.items():
            assert operation.get("summary"), (path, method)
