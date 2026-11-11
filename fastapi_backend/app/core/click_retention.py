"""How long outbound click rows are kept.

One value for the retention job (``commands.prune_click_events``) and for the click reports (the admin report and
the partner API), which tell the reader when a date range reaches back past the data. The period is also stated on
the privacy page ("If you use an offer button"): change both together.
"""

from datetime import UTC, date, datetime, time, timedelta

CLICK_RETENTION_DAYS = 395  # 13 months


def earliest_complete_date(now: datetime) -> date:
    """The first UTC day all of whose click rows are still kept, as of ``now`` (timezone aware).

    The job deletes rows older than the cutoff, so the day holding the cutoff is whole only when the cutoff
    falls exactly on midnight UTC.
    """
    cutoff = now.astimezone(UTC) - timedelta(days=CLICK_RETENTION_DAYS)
    if cutoff == datetime.combine(cutoff.date(), time.min, tzinfo=UTC):
        return cutoff.date()
    return cutoff.date() + timedelta(days=1)
