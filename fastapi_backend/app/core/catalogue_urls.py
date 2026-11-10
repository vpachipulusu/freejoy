"""Validation of the URLs an editor stores in the catalogue.

Affiliate destinations, offer source links, merchant websites and logos are written by admins
and later followed by visitors (the redirect story). A bad value is an open redirect or a
script injection, so the rules are strict and deliberately simple. A URL is accepted only if:

* it is a string of at most ``MAX_URL_LENGTH`` ASCII characters with no whitespace, control or
  format characters, no backslash and no percent-encoded control character (``%0d%0a`` could
  split a ``Location`` header);
* the scheme is exactly ``https`` (so ``javascript:``, ``data:``, ``http:`` and scheme-relative
  ``//host`` forms fail), and ``https:host`` or ``https:///host`` (no authority) fail;
* the authority has no user info (``https://trusted.example@evil.example`` fails), no IPv6
  literal and no port other than 443;
* the host is a DNS name with at least two labels of letters, digits and inner hyphens, and the
  last label is not a number (decimal digits, or ``0x`` followed by hex digits, the WHATWG
  "ends in a number" rule). That refuses IPv4 in every spelling browsers and glibc accept
  (``127.0.0.1``, ``2130706433``, ``0x7f.0.0.0x1``, ``1.0x``) and single-label names such as
  ``localhost``.

The validator looks at the text only. It cannot stop a name that resolves to a private address
(``localhost.localdomain``, ``127.0.0.1.nip.io``). Any future server-side fetcher of these URLs
must resolve the host and refuse private, loopback and link-local addresses at connect time,
and must refuse redirects to anything but https.

Non-ASCII hosts must be given in punycode. The scheme and host are returned lower-cased; the
path, query and fragment are kept byte for byte.
"""

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
HTTPS_PORT = 443
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_NUMERIC_LABEL = re.compile(r"\d+|0x[0-9a-f]*", re.IGNORECASE)
_ENCODED_CONTROL = re.compile(r"%(?:[01][0-9a-f]|7f)", re.IGNORECASE)
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Zs", "Zl", "Zp"})


class InvalidUrl(ValueError):
    """The value is not an acceptable https URL (422)."""


def _is_clean(raw: str) -> bool:
    if not raw or len(raw) > MAX_URL_LENGTH or not raw.isascii() or "\\" in raw:
        return False
    if any(unicodedata.category(char) in _BAD_CATEGORIES for char in raw):
        return False
    return _ENCODED_CONTROL.search(raw) is None


def _is_valid_host(host: str) -> bool:
    labels = host.split(".")
    if len(labels) < 2 or len(host) > 253 or _NUMERIC_LABEL.fullmatch(labels[-1]):
        return False
    return all(_LABEL.fullmatch(label) for label in labels)


def validate_https_url(raw: object) -> str:
    """Return the normalised URL or raise ``InvalidUrl``."""
    if not isinstance(raw, str) or not _is_clean(raw):
        raise InvalidUrl
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        raise InvalidUrl from None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() != "https" or "@" in parts.netloc or "[" in parts.netloc:
        raise InvalidUrl
    if port not in (None, HTTPS_PORT) or not _is_valid_host(host):
        raise InvalidUrl
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit(("https", netloc, parts.path, parts.query, parts.fragment))


def is_valid_host(host: str) -> bool:
    """The host grammar ``validate_https_url`` applies, for callers that only describe a URL."""
    return _is_valid_host(host)
