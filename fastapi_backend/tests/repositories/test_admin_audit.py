from sqlalchemy.dialects import postgresql

from app.models.platform.audit_log import AuditLog
from app.repositories.admin import AdminGrant, AdminRepository
from app.repositories.audit import AuditRepository


class Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class Session:
    def __init__(self, row=None):
        self.row = row
        self.statements = []
        self.added = []
        self.flushed = 0

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(self.row)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1


async def test_grant_lookup_is_by_verified_firebase_uid():
    session = Session(("user-1", "admin", ["users.read"]))
    grant = await AdminRepository(session).get_grant("firebase-user")  # type: ignore[arg-type]
    assert grant == AdminGrant("user-1", "admin", ["users.read"])
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    assert "firebase_uid" in str(compiled)
    assert "firebase-user" in compiled.params.values()


async def test_grant_lookup_returns_none_without_admin_record():
    repository = AdminRepository(Session(None))  # type: ignore[arg-type]
    assert await repository.get_grant("firebase-user") is None


async def test_audit_repository_stages_one_row_and_flushes_without_committing():
    session = Session()
    await AuditRepository(session).add(  # type: ignore[arg-type]
        user_id="admin-1",
        organization_id=None,
        action="admin.users.update",
        resource="users",
        resource_id="user-9",
        metadata={"actor_role": "admin"},
        ip_address="203.0.113.7",
        user_agent="pytest",
    )
    (row,) = session.added
    assert isinstance(row, AuditLog)
    assert (row.user_id, row.action, row.resource_id) == (
        "admin-1",
        "admin.users.update",
        "user-9",
    )
    assert row.metadata_ == {"actor_role": "admin"}
    assert session.flushed == 1


def test_audit_repository_exposes_no_update_or_delete():
    public = {name for name in dir(AuditRepository) if not name.startswith("_")}
    assert public == {"add"}


async def test_audit_repository_accepts_a_row_with_no_actor_user():
    session = Session()
    await AuditRepository(session).add(  # type: ignore[arg-type]
        user_id=None,
        organization_id=None,
        action="admin.users.grant_admin",
        resource="admin_users",
        resource_id="user-9",
        metadata={"actor": "commands.make_admin"},
        ip_address=None,
        user_agent=None,
    )
    assert session.added[0].user_id is None


class IdsResult:
    def scalars(self):
        return self

    def all(self):
        return ["user-1"]


class IdsSession:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return IdsResult()


async def test_email_lookup_compares_lower_case_and_is_bounded():
    from app.repositories.admin_users import AdminUsersRepository

    session = IdsSession()
    ids = await AdminUsersRepository(session).find_user_ids_by_email("Ann@Example.com")  # type: ignore[arg-type]
    assert ids == ["user-1"]
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    assert "lower(users.email)" in str(compiled)
    assert "ann@example.com" in compiled.params.values()
    assert 2 in compiled.params.values()  # limit
