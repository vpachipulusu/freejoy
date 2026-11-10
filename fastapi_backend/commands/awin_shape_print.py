"""The dry-run probe's output for ``commands.import_awin_offers --show-shape`` (FREEJ-52).

``PageRecorder`` wraps the Awin client, hands every fetched page to a ``ShapeCollector`` and keeps nothing
itself; ``shape_output`` turns the collected report into lines. All the work of deciding what may be printed
is in ``app.services.awin_shape_report``; this module only formats it, and a failure in either step is
reported as its exception type only, never as text, so the probe cannot break or leak into the run.
"""

from app.core.awin_client import AwinClient, PromotionFilters, PromotionsPage
from app.services.awin_shape_report import NameList, ShapeCollector, ShapeReport

PROG = "import-awin-offers"


class PageRecorder:
    """An ``AwinClient`` that passes every call on and lets the collector read each page; it keeps no page."""

    def __init__(self, inner: AwinClient, collector: ShapeCollector) -> None:
        self._inner = inner
        self.collector = collector
        self.failure: str | None = (
            None  # the exception type name when the collector failed
        )

    async def fetch_promotions(
        self, filters: PromotionFilters, *, page: int, page_size: int
    ) -> PromotionsPage:
        fetched = await self._inner.fetch_promotions(
            filters, page=page, page_size=page_size
        )
        if self.failure is None:
            try:
                self.collector.add_page(fetched)
            except Exception as error:  # noqa: BLE001  (the run goes on; only the type is kept)
                self.failure = type(error).__name__
        return fetched

    async def aclose(self) -> None:
        await self._inner.aclose()


def _names_text(names: NameList) -> str:
    if not names.names:
        return "(none)"
    text = ", ".join(names.names)
    if not names.more:
        return text
    return (
        f"{text} (+{names.more} or more)"
        if names.capped
        else f"{text} (+{names.more} more)"
    )


def _name_facts(label: str, names: NameList) -> list[tuple[str, str]]:
    facts = [(label, _names_text(names))]
    if names.other:
        count = f"{names.other} or more" if names.capped else str(names.other)
        facts.append((f"{label}, other names (not printed)", count))
    return facts


def _envelope_text(report: ShapeReport) -> str:
    kinds = []
    if report.pages_object:
        kinds.append(f"object with data ({report.pages_object} page(s))")
    if report.pages_bare_list:
        kinds.append(f"bare list ({report.pages_bare_list} page(s))")
    return " and ".join(kinds) or "none (no page was fetched)"


def _outcomes_text(report: ShapeReport) -> str:
    if not report.tracking_outcomes:
        return "(none)"
    return ", ".join(f"{code} {count}" for code, count in report.tracking_outcomes)


def _facts(report: ShapeReport) -> list[tuple[str, str]]:
    objects = report.items - report.non_object_items
    facts = [("envelope", _envelope_text(report))]
    if report.pages_object:
        present = f"present on {report.total_pages_present} of {report.pages_object}"
        usable = f"usable on {report.total_pages_usable}"
        facts.append(("pagination.totalPages", f"{present} page(s), {usable}"))
    facts.append(
        (
            "items in the fetched pages",
            f"{report.items} (not objects: {report.non_object_items})",
        )
    )
    for label, names in (
        ("item keys", report.item_keys),
        ("advertiser keys", report.advertiser_keys),
        ("voucher keys", report.voucher_keys),
        ("regions keys", report.regions_keys),
        ("urlTracking hosts", report.tracking_hosts),
        ("urlTracking paths", report.tracking_paths),
        ("urlTracking query parameters", report.tracking_params),
        ("urlTracking schemes", report.tracking_schemes),
    ):
        facts += _name_facts(label, names)
    joined = (
        f"true {report.joined_true}, false {report.joined_false}, "
        f"missing or not a boolean {report.joined_other}"
    )
    facts += [
        (
            "urlTracking missing, too long or without a valid host",
            str(report.tracking_unreadable),
        ),
        (
            "urlTracking accepted as an https URL",
            f"{report.tracking_https_valid} of {objects}",
        ),
        ("urlTracking with a fragment", str(report.tracking_with_fragment)),
        (
            "urlTracking with a query that is not strictly parseable",
            str(report.tracking_query_malformed),
        ),
        ("urlTracking importer check, as if joined", _outcomes_text(report)),
        *_name_facts("url hosts", report.url_hosts),
        ("url missing, too long or without a valid host", str(report.url_unreadable)),
        ("advertiser.joined", joined),
    ]
    return facts


def shape_lines(report: ShapeReport) -> list[str]:
    """The shape report as lines; the report itself has already made every label printable."""
    header = f"{PROG}: shape of the response (counts and labels, never values):"
    return [
        header,
        *(f"{PROG}: shape: {label}: {text}" for label, text in _facts(report)),
    ]


def shape_output(recorder: PageRecorder, *, partial: bool) -> list[str]:
    """The lines to print for what the recorder saw; ``partial`` when the run failed after some pages."""
    pages = recorder.collector.pages
    if partial and pages == 0 and recorder.failure is None:
        return []
    try:
        if recorder.failure is not None:
            return [f"{PROG}: shape: not available ({recorder.failure})."]
        lines = shape_lines(recorder.collector.report())
    except Exception as error:  # noqa: BLE001  (only the type is printed, never the text)
        return [f"{PROG}: shape: not available ({type(error).__name__})."]
    if partial:
        return [f"{PROG}: partial: {pages} page(s) before the failure", *lines]
    return lines
