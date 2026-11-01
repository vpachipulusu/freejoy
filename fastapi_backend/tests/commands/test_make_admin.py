"""``commands.make_admin``: the grant rules on fakes, the CLI wrapper and its output."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Self

import pytest
from sqlalchemy.exc import OperationalError

from app.core.permissions import ADMIN_PERMISSIONS, ROLE_SUPER_ADMIN, WILDCARD
from app.repositories.admin_users import AdminUserRecord
from commands import make_admin as command
from commands.make_admin import (
    MakeAdminResult,
    Options,
    Outcome,
    describe_database,
    describe_target,
    is_valid_email,
    make_admin,
    permissions_for,
    report,
)
from tests.fakes_admin_users import user
from tests.fakes_subscribers import FakeCommitter

ALL_PERMISSIONS = sorted(ADMIN_PERMISSIONS)
TOTAL = len(ADMIN_PERMISSIONS)
DB_URL = "postgresql://owner:hunter2@db.example.com:5432/freejoy?sslmode=require"
LAST_LOGIN = datetime(2026, 10, 30, 9, 15, tzinfo=UTC)


def ann(
    role: str | None = None, permissions: Any = None, **changes: Any
) -> AdminUserRecord:
    return replace(
        user("u1", role=role, permissions=permissions, email="ann@example.com"),
        **changes,
    )


class FakeStore:
    """Mirrors the real repository: case-insensitive lookup, count of wildcard super admins."""

    def __init__(self, *users: AdminUserRecord, extra_super_admins: int = 0) -> None:
        self.users = {u.id: u for u in users}
        self.extra_super_admins = extra_super_admins
        self.locked: list[str] = []
        self.count_calls = 0
        self.writes: list[tuple[str, str, list[str]]] = []

    async def find_user_ids_by_email(self, email: str, limit: int = 2) -> list[str]:
        found = [u.id for u in self.users.values() if u.email.lower() == email.lower()]
        return found[:limit]

    async def get_user(
        self, user_id: str, *, lock: bool = False
    ) -> AdminUserRecord | None:
        if lock:
            self.locked.append(user_id)
        return self.users.get(user_id)

    async def lock_super_admin_count(self) -> int:
        self.count_calls += 1
        own = sum(
            1
            for u in self.users.values()
            if u.admin_role == ROLE_SUPER_ADMIN
            and WILDCARD in (u.admin_permissions or [])
        )
        return own + self.extra_super_admins

    async def upsert_admin(
        self, user_id: str, role: str, permissions: list[str]
    ) -> None:
        self.writes.append((user_id, role, permissions))
        self.users[user_id] = replace(
            self.users[user_id], admin_role=role, admin_permissions=permissions
        )


class FakeAuditor:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record_system_action(self, source: str, **row: Any) -> None:
        self.rows.append({"source": source, **row})


async def run(
    store: FakeStore, email: str = "ann@example.com", **options: Any
) -> tuple[MakeAdminResult, FakeAuditor, FakeCommitter]:
    auditor, session = FakeAuditor(), FakeCommitter()
    result = await make_admin(store, auditor, session, email, Options(**options))
    return result, auditor, session


def assert_untouched(
    store: FakeStore, auditor: FakeAuditor, session: FakeCommitter
) -> None:
    assert store.writes == []
    assert auditor.rows == []
    assert session.commits == 0


# Granting -----------------------------------------------------------------------


async def test_grants_admin_with_every_known_permission_and_audits_it() -> None:
    store = FakeStore(ann())

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
    store = FakeStore(ann())

    result, _, _ = await run(store, role="super_admin")

    assert result.outcome is Outcome.GRANTED
    assert store.writes == [("u1", "super_admin", [WILDCARD])]


async def test_the_target_row_is_locked_before_it_is_read() -> None:
    store = FakeStore(ann())

    await run(store, dry_run=True)

    assert store.locked == ["u1"]


async def test_the_matched_user_is_reported_exactly_as_stored() -> None:
    store = FakeStore(ann(email="Ann@Example.COM", last_login_at=LAST_LOGIN))

    result, _, _ = await run(store, email="  ann@example.com ")

    assert result.user is not None
    assert (result.user.id, result.user.email) == ("u1", "Ann@Example.COM")
    assert (result.user.email_verified, result.user.last_login_at) == (True, LAST_LOGIN)


async def test_an_unknown_email_changes_nothing_and_exits_non_zero() -> None:
    store = FakeStore(ann())

    result, auditor, session = await run(store, email="nobody@example.com")

    assert (result.outcome, result.exit_code, result.user) == (
        Outcome.NOT_FOUND,
        1,
        None,
    )
    assert store.locked == []
    assert_untouched(store, auditor, session)


async def test_the_email_is_trimmed_and_compared_ignoring_case() -> None:
    store = FakeStore(ann(email="Ann@Example.COM"))

    result, _, _ = await run(store, email="  ANN@example.com \n")

    assert result.outcome is Outcome.GRANTED


async def test_a_database_match_python_does_not_confirm_is_not_found() -> None:
    class LenientStore(FakeStore):
        async def find_user_ids_by_email(self, email: str, limit: int = 2) -> list[str]:
            return ["u1"]  # a locale fold the database made but Python does not

    store = LenientStore(ann(email="İvan@example.com"))  # capital I with dot

    result, auditor, session = await run(store, email="ivan@example.com")

    assert result.outcome is Outcome.NOT_FOUND
    assert_untouched(store, auditor, session)


async def test_an_email_matching_two_users_is_refused() -> None:
    other = replace(ann(email="Ann@example.com"), id="u2")
    store = FakeStore(ann(), other)

    result, _, _ = await run(store)

    assert (result.outcome, result.exit_code, result.user) == (
        Outcome.AMBIGUOUS,
        1,
        None,
    )
    assert store.writes == []


# Unverified accounts --------------------------------------------------------------


async def test_an_unverified_email_is_refused_without_the_flag() -> None:
    store = FakeStore(ann(email_verified=False))

    result, auditor, session = await run(store)

    assert (result.outcome, result.exit_code) == (Outcome.UNVERIFIED, 1)
    assert result.user is not None and result.user.email_verified is False
    assert_untouched(store, auditor, session)


async def test_an_unverified_email_is_granted_with_allow_unverified() -> None:
    store = FakeStore(ann(email_verified=False))

    result, _, _ = await run(store, allow_unverified=True)

    assert result.outcome is Outcome.GRANTED
    assert len(store.writes) == 1


async def test_a_dry_run_shows_the_unverified_refusal() -> None:
    store = FakeStore(ann(email_verified=False))

    result, _, _ = await run(store, dry_run=True)

    assert result.outcome is Outcome.UNVERIFIED


async def test_an_unverified_user_who_already_has_the_role_is_a_plain_no_op() -> None:
    store = FakeStore(ann("admin", ALL_PERMISSIONS, email_verified=False))

    result, _, _ = await run(store)

    assert (result.outcome, result.exit_code) == (Outcome.ALREADY_ADMIN, 0)


# Existing grants ------------------------------------------------------------------


async def test_a_complete_admin_is_left_alone() -> None:
    store = FakeStore(ann("admin", ALL_PERMISSIONS))

    result, auditor, session = await run(store)

    assert (result.outcome, result.held) == (Outcome.ALREADY_ADMIN, TOTAL)
    assert_untouched(store, auditor, session)


async def test_an_admin_with_fewer_permissions_is_not_widened_by_default() -> None:
    store = FakeStore(ann("admin", ["users.read", "offers.read"]))

    result, auditor, session = await run(store)

    assert (result.outcome, result.held, result.exit_code) == (
        Outcome.ALREADY_ADMIN,
        2,
        0,
    )
    assert_untouched(store, auditor, session)


async def test_refresh_permissions_gives_a_narrow_admin_the_full_set() -> None:
    store = FakeStore(ann("admin", ["users.read"]))

    result, auditor, _ = await run(store, refresh_permissions=True)

    assert (result.outcome, result.held) == (Outcome.REFRESHED, 1)
    assert store.writes == [("u1", "admin", ALL_PERMISSIONS)]
    assert auditor.rows[0]["before"] == {"role": "admin", "permissions": ["users.read"]}


async def test_refresh_permissions_on_a_complete_admin_changes_nothing() -> None:
    store = FakeStore(ann("admin", ALL_PERMISSIONS))

    result, _, _ = await run(store, refresh_permissions=True)

    assert result.outcome is Outcome.ALREADY_ADMIN
    assert store.writes == []


@pytest.mark.parametrize("hollow", [[], None, "users.read", ["bogus.permission"]])
async def test_an_admin_row_without_usable_permissions_is_repaired(hollow: Any) -> None:
    store = FakeStore(ann("admin", hollow))

    result, auditor, _ = await run(store)

    assert result.outcome is Outcome.REPAIRED
    assert store.writes == [("u1", "admin", ALL_PERMISSIONS)]
    assert auditor.rows[0]["before"] == {"role": "admin", "permissions": []}


async def test_a_promotion_to_super_admin_is_an_update() -> None:
    store = FakeStore(ann("admin", ["users.read"]))

    result, auditor, session = await run(store, role="super_admin")

    assert (result.outcome, result.previous_role) == (Outcome.UPDATED, "admin")
    assert store.writes == [("u1", "super_admin", [WILDCARD])]
    assert auditor.rows[0]["after"] == {"role": "super_admin", "permissions": ["*"]}
    assert session.commits == 1


async def test_a_super_admin_with_exactly_the_wildcard_is_already_one() -> None:
    store = FakeStore(ann("super_admin", ["*"]))

    result, _, _ = await run(store, role="super_admin")

    assert result.outcome is Outcome.ALREADY_ADMIN
    assert store.writes == []


@pytest.mark.parametrize(
    "stored", [sorted(ADMIN_PERMISSIONS), ["users.read"], ["*", "users.read"], None]
)
async def test_a_super_admin_row_that_is_not_exactly_the_wildcard_is_repaired(
    stored: Any,
) -> None:
    store = FakeStore(ann("super_admin", stored))

    result, _, _ = await run(store, role="super_admin")

    assert result.outcome is Outcome.REPAIRED
    assert store.writes == [("u1", "super_admin", [WILDCARD])]


# Super admins and the default role ---------------------------------------------------


async def test_a_super_admin_already_covers_the_default_admin_role() -> None:
    store = FakeStore(ann("super_admin", ["*"]))

    result, auditor, session = await run(store)

    assert (result.outcome, result.exit_code) == (Outcome.ALREADY_COVERED, 0)
    assert store.count_calls == 0
    assert_untouched(store, auditor, session)


async def test_demoting_a_super_admin_needs_allow_demote_and_another_one() -> None:
    store = FakeStore(ann("super_admin", ["*"]), extra_super_admins=1)

    result, _, _ = await run(store, allow_demote=True)

    assert result.outcome is Outcome.UPDATED
    assert store.writes == [("u1", "admin", ALL_PERMISSIONS)]


async def test_the_last_super_admin_is_never_demoted_even_with_allow_demote() -> None:
    store = FakeStore(ann("super_admin", ["*"]))

    result, auditor, session = await run(store, allow_demote=True)

    assert (result.outcome, result.exit_code) == (Outcome.LAST_SUPER_ADMIN, 1)
    assert_untouched(store, auditor, session)


async def test_a_hollow_super_admin_can_be_changed_without_the_last_admin_guard() -> (
    None
):
    store = FakeStore(ann("super_admin", ["users.read"]))  # not counted: no wildcard

    result, _, _ = await run(store)

    assert result.outcome is Outcome.UPDATED
    assert store.writes == [("u1", "admin", ALL_PERMISSIONS)]
    assert store.count_calls == 0


async def test_the_super_admin_count_is_only_read_when_demoting() -> None:
    store = FakeStore(ann("admin", ["users.read"]))

    await run(store, role="super_admin")
    await run(store, role="admin", refresh_permissions=True)

    assert store.count_calls == 0


@pytest.mark.parametrize(
    ("record", "options", "outcome"),
    [
        (ann(), {}, Outcome.GRANTED),
        (ann("admin", ["users.read"]), {"role": "super_admin"}, Outcome.UPDATED),
        (ann("admin", ["users.read"]), {}, Outcome.ALREADY_ADMIN),
        (
            ann("admin", ["users.read"]),
            {"refresh_permissions": True},
            Outcome.REFRESHED,
        ),
        (ann("admin", []), {}, Outcome.REPAIRED),
        (ann("super_admin", ["*"]), {"allow_demote": True}, Outcome.LAST_SUPER_ADMIN),
        (ann(email_verified=False), {}, Outcome.UNVERIFIED),
    ],
)
async def test_a_dry_run_reports_the_same_outcome_but_writes_nothing(
    record: AdminUserRecord, options: dict[str, Any], outcome: Outcome
) -> None:
    store = FakeStore(record)

    result, auditor, session = await run(store, dry_run=True, **options)

    assert (result.outcome, result.dry_run) == (outcome, True)
    assert_untouched(store, auditor, session)


async def test_an_unknown_role_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="Unknown role"):
        await run(FakeStore(ann()), role="owner")


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


# Output ---------------------------------------------------------------------------------


def test_database_description_hides_user_and_password() -> None:
    described = describe_database(DB_URL)

    assert described == "db.example.com:5432/freejoy"
    assert "hunter2" not in described and "owner" not in described


def test_database_description_copes_with_a_socket_and_garbage() -> None:
    assert describe_database("postgresql:///freejoy") == "(local socket)/freejoy"
    assert describe_database("not a url") == "an unreadable DATABASE_URL"


def test_database_description_refuses_a_host_that_swallowed_the_password() -> None:
    leaky = "postgresql://owner:p@ss@db.example.com:5432/freejoy"

    described = describe_database(leaky)

    assert described == "an unreadable DATABASE_URL"
    assert "ss" not in described


def test_the_first_line_names_the_environment_and_the_database() -> None:
    assert describe_target("production", None, DB_URL) == (
        "make-admin: environment production, database db.example.com:5432/freejoy"
    )
    assert describe_target("production", "preview", DB_URL) == (
        "make-admin: environment production, vercel_env preview, "
        "database db.example.com:5432/freejoy"
    )


def result_for(
    outcome: Outcome,
    role: str = "admin",
    previous: str | None = None,
    *,
    dry_run: bool = False,
    held: int | None = None,
    matched: bool = True,
) -> MakeAdminResult:
    matched_user = (
        command.MatchedUser("u1", "Ann@Example.com", True, LAST_LOGIN, None)
        if matched
        else None
    )
    return MakeAdminResult(outcome, role, previous, dry_run, matched_user, held)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (result_for(Outcome.GRANTED), "ann@example.com made admin."),
        (
            result_for(Outcome.GRANTED, dry_run=True),
            "would be made admin. (dry run, nothing written)",
        ),
        (
            result_for(Outcome.UPDATED, "super_admin", "admin"),
            "changed from admin to super_admin.",
        ),
        (
            result_for(Outcome.REPAIRED, "super_admin", "super_admin"),
            "had super_admin without its permissions; repaired",
        ),
        (
            result_for(Outcome.REFRESHED, held=3),
            f"refreshed from 3 to the full {TOTAL} admin permissions.",
        ),
        (
            result_for(Outcome.ALREADY_ADMIN, held=TOTAL),
            "is already admin. No change.",
        ),
        (
            result_for(Outcome.ALREADY_ADMIN, held=3),
            (
                f"already admin with 3 of {TOTAL} permissions. No change; pass "
                "--refresh-permissions to grant the full set."
            ),
        ),
        (
            result_for(Outcome.ALREADY_ADMIN, "super_admin", held=TOTAL),
            "is already super_admin. No change.",
        ),
        (
            result_for(Outcome.ALREADY_COVERED, previous="super_admin"),
            "already super_admin (covers admin). No change.",
        ),
        (result_for(Outcome.NOT_FOUND, matched=False), "no user found"),
        (result_for(Outcome.AMBIGUOUS, matched=False), "more than one"),
        (result_for(Outcome.UNVERIFIED), "--allow-unverified"),
        (result_for(Outcome.LAST_SUPER_ADMIN), "last super_admin"),
    ],
)
def test_report_says_what_happened(result: MakeAdminResult, expected: str) -> None:
    line = report("ann@example.com", result)

    assert expected in line


def test_the_report_shows_the_matched_user_for_every_outcome_that_has_one() -> None:
    for outcome in Outcome:
        matched = outcome not in {Outcome.NOT_FOUND, Outcome.AMBIGUOUS}
        text = report("ann@example.com", result_for(outcome, matched=matched))

        assert ("matched user id=u1" in text) is matched


def test_the_matched_user_line_has_the_stored_email_and_dates() -> None:
    text = report("ann@example.com", result_for(Outcome.GRANTED))

    assert text.splitlines()[0] == (
        "make-admin: matched user id=u1 email=Ann@Example.com email_verified=true "
        "created_at=2026-10-30T09:15:00+00:00 last_login_at=never"
    )


# The CLI wrapper --------------------------------------------------------------------------


class RecordingSession(FakeCommitter):
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


SETTINGS = SimpleNamespace(ENVIRONMENT="local", VERCEL_ENV=None, DATABASE_URL=DB_URL)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> RecordingSession:
    opened = RecordingSession()
    monkeypatch.setattr(command, "async_session_maker", lambda: opened)
    monkeypatch.setattr(
        command, "AdminUsersRepository", lambda _s: FakeStore(ann(last_login_at=None))
    )
    monkeypatch.setattr(command, "AuditService", lambda _repository: FakeAuditor())
    monkeypatch.setattr(command, "AuditRepository", lambda _session: object())
    monkeypatch.setattr(command, "settings", SETTINGS)
    return opened


def test_main_grants_commits_and_prints_where_it_ran(
    session: RecordingSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = command.main(["ann@example.com"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == (
        "make-admin: environment local, database db.example.com:5432/freejoy"
    )
    assert "matched user id=u1 email=ann@example.com" in out
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


def test_main_passes_the_flags_through(
    session: RecordingSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = command.main(["ann@example.com", "--refresh-permissions", "--allow-demote"])

    assert code == 0
    assert "made admin" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["not-an-email"],
        ["ann@example.com", "--role", "owner"],
        ["ann@example.com", "--bogus"],
        ["ann@example.com", "bob@example.com"],
        ["ann@example.com", "--role", "super_admin", "--refresh-permissions"],
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


def failing_maker(error: Exception) -> Any:
    def maker() -> Any:
        raise error

    return maker


LEAKY = "postgresql://owner:hunter2@db.example.com/freejoy"


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception(LEAKY)),
        ConnectionRefusedError(LEAKY),
    ],
)
def test_main_reports_a_database_failure_by_type_on_stderr(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(command, "async_session_maker", failing_maker(error))
    monkeypatch.setattr(command, "settings", SETTINGS)

    code = command.main(["ann@example.com"])

    captured = capsys.readouterr()
    assert code == 1
    assert f"failed ({type(error).__name__})" in captured.err
    assert "may not have been written; re-run with --dry-run to check" in captured.err
    assert "Nothing was written" not in captured.err
    assert "hunter2" not in captured.out + captured.err


def test_main_says_nothing_was_written_when_a_dry_run_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        command, "async_session_maker", failing_maker(ConnectionRefusedError())
    )
    monkeypatch.setattr(command, "settings", SETTINGS)

    code = command.main(["ann@example.com", "--dry-run"])

    assert code == 1
    assert "Nothing was written." in capsys.readouterr().err


def test_main_verbose_prints_the_traceback_on_stderr_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        command, "async_session_maker", failing_maker(ConnectionRefusedError("down"))
    )
    monkeypatch.setattr(command, "settings", SETTINGS)

    command.main(["ann@example.com", "--verbose"])

    captured = capsys.readouterr()
    assert "Traceback" in captured.err and "ConnectionRefusedError" in captured.err
    assert "Traceback" not in captured.out


def test_main_lets_a_programming_error_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(command, "async_session_maker", failing_maker(KeyError("bug")))
    monkeypatch.setattr(command, "settings", SETTINGS)

    with pytest.raises(KeyError):
        command.main(["ann@example.com"])
