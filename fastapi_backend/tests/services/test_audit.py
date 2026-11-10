import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from app.core.permissions import AdminContext
from app.services.audit import REDACTED, AuditService, sanitize

ADMIN = AdminContext("admin-1", "admin", frozenset({"users.update"}))


class RecordingWriter:
    def __init__(self):
        self.rows = []

    async def add(self, **row):
        self.rows.append(row)


async def record(writer=None, **overrides):
    writer = writer or RecordingWriter()
    kwargs = {"action": "admin.users.update", "resource": "users"} | overrides
    await AuditService(writer).record_admin_action(ADMIN, **kwargs)
    return writer.rows[0]


async def test_entry_records_actor_target_and_origin():
    row = await record(
        resource_id="user-9",
        organization_id="org-1",
        ip_address="203.0.113.7",
        user_agent="pytest",
        request_id="req-1",
    )
    assert row["user_id"] == "admin-1"
    assert (row["action"], row["resource"], row["resource_id"]) == (
        "admin.users.update",
        "users",
        "user-9",
    )
    assert row["organization_id"] == "org-1"
    assert (row["ip_address"], row["user_agent"]) == ("203.0.113.7", "pytest")
    assert row["metadata"] == {"actor_role": "admin", "request_id": "req-1"}


async def test_before_after_and_details_are_stored():
    row = await record(
        before={"role": "viewer"}, after={"role": "admin"}, details={"reason": "ops"}
    )
    assert row["metadata"]["before"] == {"role": "viewer"}
    assert row["metadata"]["after"] == {"role": "admin"}
    assert row["metadata"]["details"] == {"reason": "ops"}


async def test_secret_looking_fields_are_never_stored():
    payload = {
        "password": "hunter2",
        "idToken": "eyJ.raw.token",
        "two_factor_secret": "JBSWY3DP",
        "totp_code": "123456",
        "Authorization": "Bearer abc",
        "session-cookie": "s",
        "api_key": "k",
        "backup_codes": ["a", "b"],
        "email": "ada@example.com",
        "phone_number": "+15550100",
        "nested": {"internal_key": "x", "role": "admin"},
        "items": [{"refresh_token": "r", "name": "ok"}],
    }
    row = await record(before=payload, after=payload, details=payload)
    blob = json.dumps(row["metadata"])
    for leaked in (
        "hunter2",
        "eyJ.raw.token",
        "JBSWY3DP",
        "123456",
        "Bearer abc",
        "ada@example.com",
        "+15550100",
    ):
        assert leaked not in blob
    after = row["metadata"]["after"]
    assert after["nested"] == {"internal_key": REDACTED, "role": "admin"}
    assert after["items"] == [{"refresh_token": REDACTED, "name": "ok"}]


def test_sanitize_never_renders_unknown_objects():
    cleaned = sanitize({"value": SecretStr("hunter2"), "obj": object()})
    assert cleaned == {"value": "<SecretStr>", "obj": "<object>"}


def test_sanitize_serialises_dates_and_bounds_size():
    when = datetime(2026, 1, 1, tzinfo=UTC)
    deep: dict = {}
    node = deep
    for _ in range(12):
        node["next"] = {}
        node = node["next"]
    cleaned = sanitize(
        {"at": when, "long": "x" * 5000, "many": list(range(500)), "deep": deep}
    )
    assert cleaned["at"] == when.isoformat()
    assert len(cleaned["long"]) == 500
    assert len(cleaned["many"]) == 51
    assert cleaned["many"][-1] == "[TRUNCATED]"
    assert "[TRUNCATED]" in json.dumps(cleaned["deep"])
    json.dumps(cleaned)


async def test_user_agent_is_truncated():
    row = await record(user_agent="u" * 2000)
    assert len(row["user_agent"]) == 512


@pytest.mark.parametrize("action", ["", "update", "Admin.Users", "admin..x", "a b.c"])
async def test_invalid_action_is_rejected_and_nothing_is_written(action):
    writer = RecordingWriter()
    with pytest.raises(ValueError):
        await record(writer, action=action)
    assert writer.rows == []


@pytest.mark.parametrize("resource", ["", "Users", "users;drop", "x"])
async def test_invalid_resource_is_rejected(resource):
    with pytest.raises(ValueError):
        await record(resource=resource)


async def test_unicode_look_alike_keys_are_redacted():
    row = await record(
        after={"ｐａｓｓｗｏｒｄ": "fullwidth", "pаssword": "cyrillic", "role": "x"}
    )
    after = row["metadata"]["after"]
    assert after["role"] == "x"
    blob = json.dumps(after)
    assert "fullwidth" not in blob and "cyrillic" not in blob


async def test_credential_looking_values_are_redacted_under_benign_keys():
    row = await record(
        details={
            "note": "Bearer eyJhbGciOiJIUzI1NiJ9.abc",
            "value": "token=abc123",
            "uri": "otpauth://totp/x?secret=ABC",
            "items": ["sk_live_abcdef", "fine"],
            "otpauth_uri": "otpauth://totp/x",
        }
    )
    details = row["metadata"]["details"]
    assert details["items"] == [REDACTED, "fine"]
    assert REDACTED == details["note"] == details["value"] == details["uri"]
    assert details["otpauth_uri"] == REDACTED


def test_nul_nan_and_oversized_input_stay_storable_and_marked():
    cleaned = sanitize(
        {
            "a": "x\x00y",
            "n": float("nan"),
            "wide": dict.fromkeys(map(str, range(80)), 1),
        }
    )
    assert cleaned["a"] == "xy"
    assert cleaned["n"] == "nan"
    assert cleaned["wide"]["_truncated"] is True
    assert "\x00" not in json.dumps(cleaned)


@pytest.mark.parametrize("action", ["admin.users.update\n"])
async def test_trailing_newline_in_action_is_rejected(action):
    with pytest.raises(ValueError):
        await record(action=action)


@pytest.mark.parametrize("bad", ["user\n9", "a" * 200, "x y", ""])
async def test_target_ids_must_be_plain_identifiers(bad):
    with pytest.raises(ValueError):
        await record(resource_id=bad)
    with pytest.raises(ValueError):
        await record(organization_id=bad)


async def test_user_agent_control_characters_are_removed():
    row = await record(user_agent="agent\r\nforged: line\x00")
    assert row["user_agent"] == "agentforged: line"


async def test_system_action_has_no_actor_user_and_names_its_source():
    writer = RecordingWriter()
    await AuditService(writer).record_system_action(
        "commands.make_admin",
        action="admin.users.grant_admin",
        resource="admin_users",
        resource_id="user-9",
        before=None,
        after={"role": "admin", "permissions": ["users.read"]},
    )
    row = writer.rows[0]
    assert row["user_id"] is None
    assert (row["organization_id"], row["ip_address"], row["user_agent"]) == (
        None,
        None,
        None,
    )
    assert (row["action"], row["resource"], row["resource_id"]) == (
        "admin.users.grant_admin",
        "admin_users",
        "user-9",
    )
    assert row["metadata"] == {
        "actor": "commands.make_admin",
        "after": {"role": "admin", "permissions": ["users.read"]},
    }


async def test_system_action_validates_names_like_an_admin_action():
    service = AuditService(RecordingWriter())
    with pytest.raises(ValueError):
        await service.record_system_action("cli", action="nodot", resource="users")
    with pytest.raises(ValueError):
        await service.record_system_action(
            "cli", action="admin.users.update", resource="users", resource_id="a b"
        )


PARTNER_KEY = "fj_test_" + "a1b2c3d4e5f6a7b8c9d0e1f2" + "_" + "0123456789abcdef" * 4


@pytest.mark.parametrize(
    "text",
    [
        PARTNER_KEY,
        f"the retailer pasted {PARTNER_KEY} into the ticket",
        f"key_{PARTNER_KEY}",
        PARTNER_KEY[:40],
        PARTNER_KEY.upper(),
    ],
)
def test_a_partner_key_pasted_into_free_text_is_redacted(text: str) -> None:
    assert sanitize({"note": text}) == {"note": REDACTED}


def test_ordinary_text_that_mentions_fj_is_kept() -> None:
    assert sanitize({"note": "fj team"}) == {"note": "fj team"}


def test_a_stored_key_hash_is_redacted_by_its_name() -> None:
    assert sanitize({"key_hash": "0" * 64}) == {"key_hash": REDACTED}
