"""``commands.make_admin``: the grant rules on fakes, the CLI wrapper and its output."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Self

import pytest

from app.core.permissions import ADMIN_PERMISSIONS, ROLE_SUPER_ADMIN, WILDCARD
from commands import make_admin as command
from commands.make_admin import (
    MakeAdminResult,
    Outcome,
    describe_database,
    is_valid_email,
    make_admin,
    permissions_for,
    report,
)
from tests.fakes_subscribers import FakeCommitter

ALL_PERMISSIONS = sorted(ADMIN_PERMISSIONS)
DB_URL = "postgresql://owner:hunter2@db.example.com:5432/freejoy?sslmode=require"


class FakeStore:
    def __init__(
        self,
        users: dict[str, str] | None = None,
        grants: dict[str, tuple[str, Any]] | None = None,
        super_admins: int = 1,
    ) -> None:
        self.users = users if users is not None else {"u1": "ann@example.com"}
        self.grants = grants if grants is not None else {}
        self.super_admins = super_admins
        self.lookups: list[str] = []
        self.writes: list[tuple[str, str, list[str]]] = []

    async def find_user_ids_by_email(self, email: str, limit: int = 2) -> list[str]:
        self.lookups.append(email)
        return [uid for uid, mail in self.users.items() if mail.lower() == email][
            :limit
        ]

    async def get_admin_grant(self, user_id: str) -> tuple[str, Any] | None:
        return self.grants.get(user_id)

    async def lock_super_admin_count(self) -> int:
        return self.super_admins

    async def upsert_admin(
        self, user_id: str, role: str, permissions: list[str]
    ) -> None:
        self.writes.append((user_id, role, permissions))
        self.grants[user_id] = (role, permissions)


class FakeAuditor:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record_system_action(self, source: str, **row: Any) -> None:
        self.rows.append({"source": source, **row})


async def run(
    store: FakeStore,
    email: str = "ann@example.com",
    role: str = "admin",
    *,
    dry_run: bool = False,
) -> tuple[MakeAdminResult, FakeAuditor, FakeCommitter]:
    auditor, session = FakeAuditor(), FakeCommitter()
    result = await make_admin(store, auditor, session, email, role, dry_run=dry_run)
    return result, auditor, session


async def test_grants_admin_with_every_known_permission_and_audits_it() -> None:
    store = FakeStore()

    result, auditor, session = await run(store)

    assert (result.outcome, result.exit_code) == (Outcome.GRANTED, 0)
    assert store.writes == [("u1", "admin", ALL_PERMISSIONS)]
    assert session.commits == 1
    assert auditor.rows == [
        {
            "source": "commands.make_admin",
            "action": "admin.users.grant_admin",
            "resource": "admin_users",
            "resource_id": "u1",
            "before": None,
            "after": {"role": "admin", "permissions": ALL_PERMISSIONS},
        }
    ]


async def test_super_admin_is_stored_as_exactly_the_wildcard() -> None:
    store = FakeStore()

    result, _, _ = await run(store, role="super_admin")

    assert result.outcome is Outcome.GRANTED
    assert store.writes == [("u1", "super_admin", [WILDCARD])]


async def test_a_user_who_already_has_the_role_is_left_alone() -> None:
    store = FakeStore(grants={"u1": ("admin", ["users.read"])})

    result, auditor, session = await run(store)

    assert (result.outcome, result.exit_code) == (Outcome.ALREADY_ADMIN, 0)
    assert store.writes == []  # narrower permissions are not widened
    assert auditor.rows == []
    assert session.commits == 0


async def test_a_different_role_is_updated_and_the_old_state_is_audited() -> None:
    store = FakeStore(grants={"u1": ("admin", ["users.read"])})

    result, auditor, session = await run(store, role="super_admin")

    assert (result.outcome, result.previous_role) == (Outcome.UPDATED, "admin")
    assert store.writes == [("u1", "super_admin", [WILDCARD])]
    assert auditor.rows[0]["before"] == {"role": "admin", "permissions": ["users.read"]}
    assert auditor.rows[0]["after"] == {"role": "super_admin", "permissions": ["*"]}
    assert session.commits == 1


async def test_a_super_admin_row_without_the_wildcard_is_repaired() -> None:
    store = FakeStore(grants={"u1": ("super_admin", ["users.read"])}, super_admins=2)

    result, _, _ = await run(store, role="super_admin")

    assert result.outcome is Outcome.UPDATED
    assert store.writes == [("u1", "super_admin", [WILDCARD])]


async def test_a_super_admin_can_be_demoted_when_another_remains() -> None:
    store = FakeStore(grants={"u1": ("super_admin", ["*"])}, super_admins=2)

    result, _, _ = await run(store, role="admin")

    assert result.outcome is Outcome.UPDATED
    assert store.writes == [("u1", "admin", ALL_PERMISSIONS)]


async def test_the_last_super_admin_is_never_demoted() -> None:
    store = FakeStore(grants={"u1": ("super_admin", ["*"])}, super_admins=1)

    result, auditor, session = await run(store, role="admin")

    assert (result.outcome, result.exit_code) == (Outcome.LAST_SUPER_ADMIN, 1)
    assert store.writes == []
    assert auditor.rows == []
    assert session.commits == 0


async def test_an_unknown_email_changes_nothing_and_exits_non_zero() -> None:
    store = FakeStore()

    result, auditor, session = await run(store, email="nobody@example.com")

    assert (result.outcome, result.exit_code) == (Outcome.NOT_FOUND, 1)
    assert store.writes == []
    assert auditor.rows == []
    assert session.commits == 0


async def test_the_email_is_trimmed_and_compared_ignoring_case() -> None:
    store = FakeStore(users={"u1": "Ann@Example.COM"})

    result, _, _ = await run(store, email="  ANN@example.com \n")

    assert result.outcome is Outcome.GRANTED
    assert store.lookups == ["ann@example.com"]


async def test_an_email_matching_two_users_is_refused() -> None:
    store = FakeStore(users={"u1": "ann@example.com", "u2": "Ann@example.com"})

    result, _, _ = await run(store)

    assert (result.outcome, result.exit_code) == (Outcome.AMBIGUOUS, 1)
    assert store.writes == []


@pytest.mark.parametrize(
    ("grants", "role", "outcome"),
    [
        ({}, "admin", Outcome.GRANTED),
        ({"u1": ("admin", ["users.read"])}, "super_admin", Outcome.UPDATED),
        ({"u1": ("admin", ["users.read"])}, "admin", Outcome.ALREADY_ADMIN),
        ({"u1": ("super_admin", ["*"])}, "admin", Outcome.LAST_SUPER_ADMIN),
    ],
)
async def test_a_dry_run_reports_the_same_outcome_but_writes_nothing(
    grants: dict[str, tuple[str, Any]], role: str, outcome: Outcome
) -> None:
    store = FakeStore(grants=grants)

    result, auditor, session = await run(store, role=role, dry_run=True)

    assert (result.outcome, result.dry_run) == (outcome, True)
    assert store.writes == []
    assert auditor.rows == []
    assert session.commits == 0


async def test_an_unknown_role_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="Unknown role"):
        await run(FakeStore(), role="owner")


def test_permissions_match_what_the_app_stores_for_each_role() -> None:
    assert permissions_for("admin") == ALL_PERMISSIONS
    assert "*" not in permissions_for("admin")
    assert permissions_for(ROLE_SUPER_ADMIN) == ["*"]


@pytest.mark.parametrize(
    ("raw", "valid"),
    [
        ("ann@example.com", True),
        ("  ann@example.com ", True),
        ("ann@example", False),
        ("ann example@x.com", False),
        ("@example.com", False),
        ("ann@@example.com", False),
        ("", False),
        (f"{'a' * 250}@x.com", False),
    ],
)
def test_email_shape_check(raw: str, valid: bool) -> None:
    assert is_valid_email(raw) is valid


def test_database_description_hides_user_and_password() -> None:
    described = describe_database(DB_URL)

    assert described == "db.example.com:5432/freejoy"
    assert "hunter2" not in described and "owner" not in described


def test_database_description_copes_with_a_socket_and_garbage() -> None:
    assert describe_database("postgresql:///freejoy") == "(local socket)/freejoy"
    assert describe_database("not a url") == "an unreadable DATABASE_URL"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (MakeAdminResult(Outcome.GRANTED, "admin", None, False), "made admin."),
        (
            MakeAdminResult(Outcome.GRANTED, "admin", None, True),
            "would be made admin. (dry run, nothing written)",
        ),
        (
            MakeAdminResult(Outcome.UPDATED, "super_admin", "admin", False),
            "changed from admin to super_admin.",
        ),
        (
            MakeAdminResult(Outcome.ALREADY_ADMIN, "admin", "admin", False),
            "is already admin. No change.",
        ),
        (MakeAdminResult(Outcome.NOT_FOUND, "admin", None, False), "no user found"),
        (MakeAdminResult(Outcome.AMBIGUOUS, "admin", None, False), "more than one"),
        (
            MakeAdminResult(Outcome.LAST_SUPER_ADMIN, "admin", "super_admin", False),
            "last super_admin",
        ),
    ],
)
def test_report_says_what_happened(result: MakeAdminResult, expected: str) -> None:
    line = report("ann@example.com", result)

    assert line.startswith("make-admin: ")
    assert expected in line


# The CLI wrapper ---------------------------------------------------------------


class RecordingSession(FakeCommitter):
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> RecordingSession:
    opened = RecordingSession()
    monkeypatch.setattr(command, "async_session_maker", lambda: opened)
    monkeypatch.setattr(command, "AdminUsersRepository", lambda _session: FakeStore())
    monkeypatch.setattr(command, "AuditService", lambda _repository: FakeAuditor())
    monkeypatch.setattr(command, "AuditRepository", lambda _session: object())
    monkeypatch.setattr(command, "settings", SimpleNamespace(DATABASE_URL=DB_URL))
    return opened


def test_main_grants_and_commits_and_prints_where_it_ran(
    session: RecordingSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = command.main(["ann@example.com"])

    out = capsys.readouterr().out
    assert code == 0
    assert "database db.example.com:5432/freejoy" in out
    assert "ann@example.com made admin." in out
    assert "hunter2" not in out and "owner" not in out
    assert (session.commits, session.rollbacks) == (1, 0)


def test_main_dry_run_rolls_back_and_commits_nothing(
    session: RecordingSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = command.main(["ann@example.com", "--dry-run", "--role", "super_admin"])

    assert code == 0
    assert "would be made super_admin" in capsys.readouterr().out
    assert (session.commits, session.rollbacks) == (0, 1)


def test_main_unknown_email_exits_1_without_committing(
    session: RecordingSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = command.main(["  Nobody@Example.com "])

    out = capsys.readouterr().out
    assert code == 1
    assert "no user found for Nobody@Example.com." in out
    assert (session.commits, session.rollbacks) == (0, 1)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["not-an-email"],
        ["ann@example.com", "--role", "owner"],
        ["ann@example.com", "--bogus"],
        ["ann@example.com", "bob@example.com"],
    ],
)
def test_main_rejects_bad_arguments_with_exit_2_before_touching_the_database(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> None:
        pytest.fail("the database must not be opened")

    monkeypatch.setattr(command, "async_session_maker", forbidden)

    assert command.main(argv) == 2


def test_main_help_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert command.main(["--help"]) == 0
    assert "--dry-run" in capsys.readouterr().out


def test_main_reports_a_database_failure_by_type_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @asynccontextmanager
    async def broken() -> AsyncIterator[None]:
        raise RuntimeError("postgresql://owner:hunter2@db.example.com/freejoy")
        yield

    monkeypatch.setattr(command, "async_session_maker", broken)
    monkeypatch.setattr(command, "settings", SimpleNamespace(DATABASE_URL=DB_URL))

    code = command.main(["ann@example.com"])

    out = capsys.readouterr().out
    assert code == 1
    assert "failed (RuntimeError)" in out
    assert "hunter2" not in out
