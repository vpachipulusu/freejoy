"""``--membership`` and ``--show-shape``: the dry-run probe that looks at all programmes (FREEJ-52)."""

from collections.abc import Mapping
from typing import Any

import pytest

from app.core.awin_client import AwinError, AwinReason
from commands import import_awin_offers as command
from tests.awin_samples import CODE, NOW, PUBLISHER_ID, raw_item
from tests.fakes_awin import FakeAwinClient
from tests.fakes_awin_import import FakeDatabase

TOKEN = "fake-awin-token-0123456789abcdef"
SHAPE = "import-awin-offers: shape"


class Reporter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any], str]] = []

    def __call__(
        self, message: str, context: Mapping[str, Any], *, level: str = "error"
    ) -> None:
        self.calls.append((message, context, level))


def run(args: list[str], client: FakeAwinClient, db: FakeDatabase | None = None) -> int:
    return command.main(
        args,
        client=client,
        publisher_id=PUBLISHER_ID,
        units=(db or FakeDatabase()).unit,
        reporter=Reporter(),
        now=NOW,
    )


@pytest.fixture
def untouched(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Records every way the command could reach credentials, a client or a database."""
    calls: dict[str, list[object]] = {"credentials": [], "client": [], "database": []}

    def credentials() -> str:
        calls["credentials"].append(1)
        return "credentials were read"

    monkeypatch.setattr(command, "_credentials", credentials)
    monkeypatch.setattr(command, "build_client", lambda *a: calls["client"].append(a))
    monkeypatch.setattr(command, "_real_units", lambda: calls["database"].append(1))
    return calls


# --- usage errors --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "named"),
    [
        (["--membership", "all"], "--membership"),
        (["--membership", "notJoined"], "--membership"),
        (["--show-shape"], "--show-shape"),
        (["--membership", "all", "--show-shape"], "--membership"),
    ],
)
def test_the_probe_flags_without_dry_run_are_a_usage_error_before_anything_is_built(
    args: list[str],
    named: str,
    untouched: dict[str, list[object]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = command.main(args, reporter=Reporter(), now=NOW)

    captured = capsys.readouterr()
    assert code == 2 and captured.out == ""
    assert named in captured.err and "--dry-run" in captured.err
    assert captured.err.startswith("import-awin-offers: ")
    assert untouched == {"credentials": [], "client": [], "database": []}


def test_the_probe_flags_without_dry_run_make_no_request_and_open_no_unit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([[raw_item(1)]])
    db = FakeDatabase()

    code = run(["--membership", "all"], client, db)

    assert code == 2
    assert client.requests == [] and db.units_opened == 0
    assert db.committed.offers == {} and not client.closed
    assert TOKEN not in capsys.readouterr().err


def test_an_unknown_membership_value_exits_2_before_anything_is_built(
    untouched: dict[str, list[object]], capsys: pytest.CaptureFixture[str]
) -> None:
    code = command.main(
        ["--dry-run", "--membership", "everything"], reporter=Reporter(), now=NOW
    )

    assert code == 2 and "membership" in capsys.readouterr().err
    assert untouched == {"credentials": [], "client": [], "database": []}


def test_a_dry_run_with_the_default_membership_still_reads_credentials(
    untouched: dict[str, list[object]],
) -> None:
    code = command.main(["--dry-run"], reporter=Reporter(), now=NOW)

    assert code == 2 and untouched["credentials"] == [1]


def test_joined_without_dry_run_is_still_allowed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([[raw_item(1)]])
    db = FakeDatabase()

    code = run(["--membership", "joined"], client, db)

    assert code == 0 and len(db.committed.offers) == 1
    assert "filters sent" not in capsys.readouterr().out


# --- membership and the filters line -------------------------------------------------------


def test_the_membership_reaches_the_client_in_a_dry_run() -> None:
    client = FakeAwinClient([[]])

    code = run(["--dry-run", "--membership", "all"], client)

    assert code == 0 and client.requests[0][0].membership == "all"


def test_the_default_membership_is_joined() -> None:
    client = FakeAwinClient([[]])

    run(["--dry-run"], client)

    assert client.requests[0][0].membership == "joined"


def test_the_dry_run_names_the_filters_sent_after_the_unchanged_first_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([[raw_item(1)]])

    run(["--dry-run", "--membership", "notJoined"], client)

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "import-awin-offers: dry run, nothing was written. "
        "fetched 1 promotion(s) in 1 page(s); 1 valid, 0 skipped."
    )
    assert lines[1] == (
        "import-awin-offers: filters sent: membership=notJoined, type=all, "
        "status=active, regionCodes=GB."
    )


def test_the_filters_line_includes_updated_since_when_given(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run(["--dry-run", "--updated-since", "2026-11-01"], FakeAwinClient([[]]))

    assert "updatedSince=2026-11-01" in capsys.readouterr().out


def test_a_zero_row_dry_run_explains_itself_and_points_at_the_probe(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run(["--dry-run", "--limit", "20", "--max-pages", "1"], FakeAwinClient([[]]))

    out = capsys.readouterr().out
    assert code == 0
    assert "fetched 0 promotion(s) in 1 page(s); 0 valid, 0 skipped." in out
    assert "membership=joined" in out
    assert "--membership all --show-shape" in out
    assert SHAPE not in out


def test_a_zero_row_probe_does_not_repeat_the_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run(["--dry-run", "--membership", "all"], FakeAwinClient([[]]))

    assert "--membership all --show-shape" not in capsys.readouterr().out


def test_a_real_run_prints_no_filters_line(capsys: pytest.CaptureFixture[str]) -> None:
    run([], FakeAwinClient([[raw_item(1)]]))

    assert "filters sent" not in capsys.readouterr().out


# --- the shape report ----------------------------------------------------------------------


def test_show_shape_prints_the_report_after_the_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([[raw_item(1), raw_item(2), "junk"]], total_pages=1)
    db = FakeDatabase()

    code = run(["--dry-run", "--membership", "all", "--show-shape"], client, db)

    out = capsys.readouterr().out
    assert code == 0 and db.units_opened == 0 and client.closed
    lines = out.splitlines()
    first_shape = next(i for i, line in enumerate(lines) if line.startswith(SHAPE))
    assert lines[0].startswith("import-awin-offers: dry run")
    assert first_shape > 1
    assert all(line.startswith("import-awin-offers: ") for line in lines)
    shape = "\n".join(lines[first_shape:])
    assert "envelope: object with data" in shape
    assert "pagination.totalPages: present on 1 of 1" in shape
    assert "items seen: 3 (not objects: 1)" in shape
    assert "item keys: " in shape and "promotionId" in shape
    assert "advertiser keys: id, joined, name" in shape
    assert "voucher keys: attributable, code, exclusive" in shape
    assert "regions keys: all, list" in shape
    assert "urlTracking hosts: www.awin1.com" in shape
    assert "urlTracking paths: /cread.php" in shape
    assert "urlTracking query parameters: awinaffid, awinmid, clickref, ued" in shape
    assert "url hosts: www.acme.example" in shape
    assert "advertiser.joined: true 2, false 0, missing or not a boolean 0" in shape


def test_show_shape_on_a_bare_list_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    run(["--dry-run", "--show-shape"], FakeAwinClient([[raw_item(1)]]))

    shape = capsys.readouterr().out
    assert "envelope: bare list" in shape
    assert "totalPages" not in shape or "not applicable" in shape


def test_show_shape_with_no_items_prints_none_not_a_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run(["--dry-run", "--show-shape"], FakeAwinClient([[]]))

    out = capsys.readouterr().out
    assert "item keys: none" in out and "items seen: 0" in out


def test_show_shape_reports_more_than_the_cap_as_plus_n_more(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = {f"key{n:04d}": n for n in range(500)}

    run(["--dry-run", "--show-shape"], FakeAwinClient([[item]]))

    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if "shape: item keys:" in x)
    assert "(+460 more)" in line and len(line) < 800


def test_the_shape_report_never_prints_a_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    items = [raw_item(n, title=f"TITLE-{n}") for n in range(1, 4)]
    client = FakeAwinClient([items], total_pages=1)

    run(["--dry-run", "--membership", "all", "--show-shape"], client)

    captured = capsys.readouterr()
    text = captured.out + captured.err
    for secret in (
        CODE,
        str(PUBLISHER_ID),
        TOKEN,
        "http",
        "TITLE-",
        "Acme",
        "10% off",
        "Save",
        "New customers",
        "sale-1",
        "cread.php?",
    ):
        assert secret not in text, secret


def test_hostile_names_are_sanitised_in_the_printed_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = {"k\x1b[31m\nINJECT\u00e9": 1, "url": "https://ev\x1bil.example/"}

    run(["--dry-run", "--show-shape"], FakeAwinClient([[item]]))

    out = capsys.readouterr().out
    assert "\x1b" not in out and "\u00e9" not in out
    assert all(" " <= char <= "~" or char == "\n" for char in out)
    assert all(line.startswith("import-awin-offers: ") for line in out.splitlines()), (
        "an injected newline must not start a line of its own"
    )


def test_a_failed_dry_run_with_show_shape_still_exits_1_and_prints_no_shape(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeAwinClient([AwinError(AwinReason.AUTH_FAILED)])

    code = run(["--dry-run", "--show-shape"], client)

    captured = capsys.readouterr()
    assert code == 1 and SHAPE not in captured.out
    assert "auth_failed" in captured.err


def test_without_show_shape_the_output_has_no_shape_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run(["--dry-run"], FakeAwinClient([[raw_item(1)]]))

    assert SHAPE not in capsys.readouterr().out
