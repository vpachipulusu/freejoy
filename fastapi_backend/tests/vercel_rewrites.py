"""The root ``vercel.json`` rewrites, matched the way Vercel matches a request path (ADR-011, ADR-013).

Shared by ``tests/core/test_vercel_routing.py`` and ``tests/api/test_route_confinement.py``.

What is documented (vercel.com/docs/project-configuration/vercel-json): rewrites are tried in
the order written and the first match wins; a rewrite ``source`` is case-sensitive; a rewrite to a
service hands the service the original path.

ASSUMPTIONS that cannot be verified offline, and that the matcher below makes:

* A rewrite with a ``has`` or ``missing`` condition (a host, header, cookie or query match) is refused,
  not matched as if it were unconditional.
* ``source`` is path-to-regexp. The only syntax understood here is a literal path and the
  unnamed wildcard ``(.*)``; any other syntax makes :func:`source_pattern` raise, so a new kind of
  rewrite fails loudly instead of being matched wrongly.
* Matching is strict about a trailing slash (``/a`` does not match ``/a/``) and ``(.*)`` matches
  any remainder, including none and including slashes.

If Vercel were looser than that (for example ignoring a trailing slash), the exact Stripe source
would also catch ``/webhooks/stripe/``. That still reaches only the Stripe route, which the confinement
test also checks. The first manual check on a real deployment is listed in ADR-013.
"""

import json
import re
from pathlib import Path
from typing import Any

VERCEL_JSON = Path(__file__).resolve().parents[2] / "vercel.json"
BACKEND_SERVICE = "fastapi_backend"
FRONTEND_SERVICE = "nextjs-frontend"
WILDCARD = "(.*)"
UNSUPPORTED_SYNTAX = re.compile(r"[:()*?+{}\[\]\\|^$]")


def load_rewrites(path: Path = VERCEL_JSON) -> list[dict[str, Any]]:
    rewrites: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))[
        "rewrites"
    ]
    return rewrites


def source_pattern(source: str) -> re.Pattern[str]:
    """The regular expression of a rewrite ``source`` (literal text and ``(.*)`` only)."""
    literals = source.split(WILDCARD)
    for literal in literals:
        if UNSUPPORTED_SYNTAX.search(literal):
            raise ValueError(
                f"rewrite source uses syntax this matcher does not know: {source}"
            )
    return re.compile(".*".join(re.escape(literal) for literal in literals))


def destination_service(path: str, rewrites: list[dict[str, Any]]) -> str | None:
    """The service of the first rewrite whose source matches ``path`` (None when none does)."""
    for rule in rewrites:
        if "has" in rule or "missing" in rule:
            raise ValueError(
                f"conditional rewrite this matcher does not know: {rule['source']}"
            )
        if source_pattern(str(rule["source"])).fullmatch(path):
            return str(rule["destination"]["service"])
    return None


def backend_sources(rewrites: list[dict[str, Any]]) -> list[str]:
    return [
        str(rule["source"])
        for rule in rewrites
        if rule["destination"] == {"service": BACKEND_SERVICE}
    ]
