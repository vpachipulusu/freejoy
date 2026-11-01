"""Admin audit trail: build one append-only entry per admin change.

Entries are staged in the caller's transaction, so the change and its audit row
commit or roll back together: if the audit write fails, the change does not stick.
Denied requests write no row (they are logged, without payload, by ``require_admin``).

Commit contract: the session is request-scoped and never auto-commits. A route calls
``record`` and the change on the same ``SessionDep`` session and commits once, after
``record``; never commit the change first. If ``record`` raises, the route exits
before its commit and the session rolls everything back.
"""

import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Protocol

from app.core.permissions import AdminContext

REDACTED = "[REDACTED]"
MAX_DEPTH = 6
MAX_ITEMS = 50
MAX_STRING = 500

# Matched against the lower-cased key with non-alphanumerics removed. Covers
# secrets, credentials, TOTP/2FA material and the raw PII the ticket excludes
# (email, phone, address); log ids and changed field names instead of values.
_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "privatekey",
    "secretkey",
    "signingkey",
    "internalkey",
    "accesskey",
    "authorization",
    "cookie",
    "session",
    "credential",
    "totp",
    "twofactor",
    "mfa",
    "backupcode",
    "recoverycode",
    "salt",
    "email",
    "phone",
    "address",
    "passphrase",
    "bearer",
    "jwt",
    "otpauth",
    "seed",
    "signature",
    "whsec",
    "creds",
    "cvv",
    "ssn",
    "iban",
    "cardnumber",
    "connectionstring",
    "databaseurl",
    "dsn",
)
_SENSITIVE_EXACT = frozenset(
    {"key", "keys", "otp", "pin", "code", "hash", "pwd", "pass", "dob", "card", "pan"}
)
# Free-text values that look like credentials, wherever they appear.
_SECRET_VALUE = re.compile(
    r"(bearer\s+\S|eyJ[A-Za-z0-9_-]{8,}|otpauth://|\b(sk|rk|pk)_(live|test)_|whsec_|postgres(ql)?(\+\w+)?://"
    r"|(token|secret|password|passwd|api[_-]?key)\s*[=:])",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
_RESOURCE_RE = re.compile(r"[a-z][a-z0-9_]{1,63}")
_TARGET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def _is_sensitive(key: object) -> bool:
    folded = unicodedata.normalize("NFKC", str(key)).casefold()
    normalised = re.sub(r"[^a-z0-9]", "", folded)
    if not normalised:
        return True  # nothing recognisable survives (non-Latin or symbol-only key)
    # A non-ASCII letter after NFKC (for example a Cyrillic look-alike) may be
    # disguising a sensitive word, so the key cannot be trusted.
    if any(ord(char) > 127 and char.isalpha() for char in folded):
        return True
    return normalised in _SENSITIVE_EXACT or any(
        part in normalised for part in _SENSITIVE_PARTS
    )


def _clean_text(value: str) -> str:
    """Drop NUL and surrogates (Postgres JSONB rejects them) and bound the length."""
    text = value.replace("\x00", "").encode("utf-8", "ignore").decode("utf-8")
    return text[:MAX_STRING]


def sanitize(value: Any, depth: int = 0) -> Any:
    """JSON-safe copy of ``value`` with secret-looking keys and values redacted.

    Keys are matched by name (callers should pass ``model_dump()`` output, not
    models); string values that look like credentials are redacted too. Size is
    bounded, and a cut is marked.
    """
    if depth > MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        items = list(value.items())
        result = {
            str(key)[:MAX_STRING]: REDACTED
            if _is_sensitive(key)
            else sanitize(item, depth + 1)
            for key, item in items[:MAX_ITEMS]
        }
        if len(items) > MAX_ITEMS:
            result["_truncated"] = True
        return result
    if isinstance(value, list | tuple | set | frozenset):
        entries = (
            sorted(value, key=repr)
            if isinstance(value, set | frozenset)
            else list(value)
        )
        cleaned = [sanitize(item, depth + 1) for item in entries[:MAX_ITEMS]]
        if len(entries) > MAX_ITEMS:
            cleaned.append("[TRUNCATED]")
        return cleaned
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return REDACTED if _SECRET_VALUE.search(value) else _clean_text(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    # Unknown objects (SecretStr, models, ...): record the type, never the value.
    return f"<{type(value).__name__}>"


def _check_names(
    action: str, resource: str, resource_id: str | None, organization_id: str | None
) -> None:
    if not _ACTION_RE.fullmatch(action):
        raise ValueError("Invalid audit action")
    if not _RESOURCE_RE.fullmatch(resource):
        raise ValueError("Invalid audit resource")
    for target in (resource_id, organization_id):
        if target is not None and not _TARGET_ID_RE.fullmatch(target):
            raise ValueError("Invalid audit target id")


def _metadata(
    base: dict[str, Any],
    *,
    request_id: str | None,
    before: Any,
    after: Any,
    details: Any,
) -> dict[str, Any]:
    metadata = dict(base)
    for key, value in (
        ("request_id", request_id),
        ("before", before),
        ("after", after),
        ("details", details),
    ):
        if value is not None:
            metadata[key] = sanitize(value)
    return metadata


class AuditWriter(Protocol):
    async def add(
        self,
        *,
        user_id: str | None,
        organization_id: str | None,
        action: str,
        resource: str,
        resource_id: str | None,
        metadata: dict[str, Any],
        ip_address: str | None,
        user_agent: str | None,
    ) -> None: ...


class AuditService:
    def __init__(self, repository: AuditWriter) -> None:
        self.repository = repository

    async def record_admin_action(
        self,
        admin: AdminContext,
        *,
        action: str,
        resource: str,
        resource_id: str | None = None,
        organization_id: str | None = None,
        before: Any = None,
        after: Any = None,
        details: Any = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Stage one audit row.

        ``action`` is ``<area>.<verb>`` (for example ``admin.users.update``),
        ``resource`` the table-like target name, ``resource_id`` the target id.
        A malformed action or resource is a programming error and raises.
        """
        _check_names(action, resource, resource_id, organization_id)
        metadata = _metadata(
            {"actor_role": admin.role},
            request_id=request_id,
            before=before,
            after=after,
            details=details,
        )
        await self.repository.add(
            user_id=admin.user_id,
            organization_id=organization_id,
            action=action,
            resource=resource,
            resource_id=resource_id,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=CONTROL_CHARS.sub("", user_agent)[:512] if user_agent else None,
        )

    async def record_system_action(
        self,
        source: str,
        *,
        action: str,
        resource: str,
        resource_id: str | None = None,
        before: Any = None,
        after: Any = None,
        details: Any = None,
    ) -> None:
        """Stage one audit row for a change made outside a request (an operator command).

        There is no signed-in admin, so ``user_id`` is null (the column allows it and the
        admin audit list shows an empty actor) and ``metadata.actor`` names the ``source``
        instead, for example ``commands.make_admin``. Same validation and redaction as
        ``record_admin_action``; the caller commits.
        """
        _check_names(action, resource, resource_id, None)
        metadata = _metadata(
            {"actor": sanitize(source)},
            request_id=None,
            before=before,
            after=after,
            details=details,
        )
        await self.repository.add(
            user_id=None,
            organization_id=None,
            action=action,
            resource=resource,
            resource_id=resource_id,
            metadata=metadata,
            ip_address=None,
            user_agent=None,
        )
