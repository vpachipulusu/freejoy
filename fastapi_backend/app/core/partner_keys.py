"""Partner API keys (ADR-013): format, parsing and hashing.

A key is ``fj_<env>_<key_id>_<secret>``:

* ``env`` is the running environment (lower-case letters and digits), so a key issued in one
  environment is refused by every other;
* ``key_id`` is the row id of ``partner_api_keys`` (24 characters of ``[a-z0-9]``, see
  ``app.core.ids.new_id``): it selects the row, it is not secret;
* ``secret`` is 32 bytes from ``secrets`` written as 64 lower-case hex characters.

The alphabets contain no ``_`` and the lengths are fixed, so one anchored ASCII pattern parses a key
in exactly one way. Only the SHA-256 of the full key is stored. A plain hash is enough here
because the secret is 256 bits from a CSPRNG: there is nothing to guess and nothing for a slow hash or
a salt to protect (a password would need both). Digests are compared with ``hmac.compare_digest``,
and a key id that does not exist is compared against a dummy digest, so the work done does not
tell a caller which ids exist.

This module never logs and never keeps a key: the full key exists in the caller's hands once, at
creation.
"""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

KEY_PREFIX = "fj"
KEY_ID_LENGTH = 24
SECRET_BYTES = 32
SECRET_LENGTH = SECRET_BYTES * 2
MAX_ENVIRONMENT_LENGTH = 16
# A key is about 120 characters; anything much longer is refused before the pattern runs.
MAX_KEY_LENGTH = 160

_ENVIRONMENT = re.compile(rf"[a-z0-9]{{1,{MAX_ENVIRONMENT_LENGTH}}}", re.ASCII)
_KEY = re.compile(
    rf"{KEY_PREFIX}_(?P<env>[a-z0-9]{{1,{MAX_ENVIRONMENT_LENGTH}}})"
    rf"_(?P<key_id>[a-z0-9]{{{KEY_ID_LENGTH}}})_(?P<secret>[0-9a-f]{{{SECRET_LENGTH}}})",
    re.ASCII,
)
# What a key (whole, or cut short) looks like in free text: used to keep keys out of labels and audit rows.
# One definition: the audit redaction and the admin label check build on it, and a frontend test pins its copy.
KEY_LOOKING_SOURCE = (
    rf"{KEY_PREFIX}_[a-z0-9]{{1,{MAX_ENVIRONMENT_LENGTH}}}_[a-z0-9]{{8,}}"
)
KEY_LOOKING = re.compile(KEY_LOOKING_SOURCE, re.IGNORECASE)
# Compared when the key id is unknown, so the work is the same as for a known one.
DUMMY_DIGEST = hashlib.sha256(b"partner-key-dummy-digest").hexdigest()


@dataclass(frozen=True)
class GeneratedKey:
    """A new key. ``value`` is shown once and never stored; ``digest`` is what the database keeps."""

    key_id: str
    value: str
    digest: str

    def __repr__(self) -> str:
        return f"GeneratedKey(key_id={self.key_id!r})"


@dataclass(frozen=True)
class ParsedKey:
    """A well-formed key presented by a caller: the id to look up, the digest to compare and the ``<env>`` it names."""

    key_id: str
    digest: str
    environment: str

    def __repr__(self) -> str:
        return f"ParsedKey(key_id={self.key_id!r})"


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("ascii")).hexdigest()


def running_environment(vercel_env: str | None, environment: str) -> str | None:
    """The environment name keys carry: ``VERCEL_ENV`` when set, else ``ENVIRONMENT`` (as Sentry does).

    ``None`` when the name cannot be written into a key (not ``[a-z0-9]`` up to 16 characters); no key
    can then be issued or accepted.
    """
    name = (vercel_env or environment).strip().lower()
    return name if _ENVIRONMENT.fullmatch(name) else None


def generate_key(environment: str, key_id: str) -> GeneratedKey:
    secret = secrets.token_hex(SECRET_BYTES)
    value = f"{KEY_PREFIX}_{environment}_{key_id}_{secret}"
    if _KEY.fullmatch(value) is None:
        raise ValueError("Invalid key environment or id")
    return GeneratedKey(key_id=key_id, value=value, digest=key_digest(value))


def parse_well_formed_key(raw: str) -> ParsedKey | None:
    """The key id, digest and environment of a well-formed key, whatever environment it names; else ``None``.

    ``fullmatch`` anchors both ends and, unlike ``$``, does not accept a trailing newline. The environment is
    returned, not judged: the authenticator looks the id up and compares like for any other key, so the
    environment never selects a faster or differently limited path.
    """
    if len(raw) > MAX_KEY_LENGTH:
        return None
    match = _KEY.fullmatch(raw)
    if match is None:
        return None
    return ParsedKey(
        key_id=match["key_id"], digest=key_digest(raw), environment=match["env"]
    )


def parse_key(raw: str, environment: str) -> ParsedKey | None:
    """Like ``parse_well_formed_key`` but ``None`` for a key that names another environment."""
    parsed = parse_well_formed_key(raw)
    return parsed if parsed is not None and parsed.environment == environment else None


def key_matches(presented_digest: str, stored_digest: str | None) -> bool:
    """Constant-time comparison; an unknown key id (``None``) still does one, against a dummy."""
    expected = DUMMY_DIGEST if stored_digest is None else stored_digest
    equal = hmac.compare_digest(presented_digest.encode(), expected.encode())
    return stored_digest is not None and equal
