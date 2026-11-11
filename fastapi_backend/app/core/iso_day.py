"""The query type for a calendar day written exactly ``YYYY-MM-DD``.

Shared by the internal click report and the partner click report, so both accept the same text and nothing else.
"""

import re
from datetime import date
from typing import Annotated

from pydantic import BeforeValidator

_ISO_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _only_iso_day(value: object) -> object:
    """Accept only ``YYYY-MM-DD`` text: not a Unix timestamp, not a date-time, not ``20260310``."""
    if isinstance(value, str) and _ISO_DAY.fullmatch(value):
        return value
    raise ValueError("Use a date written YYYY-MM-DD")


# The query type of ``from`` and ``to``: a real calendar day written exactly YYYY-MM-DD.
IsoDay = Annotated[date, BeforeValidator(_only_iso_day)]
