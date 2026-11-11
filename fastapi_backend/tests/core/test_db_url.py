import ssl

from app.core.db_url import (
    CONNECT_TIMEOUT_SECONDS,
    to_async_target,
    unique_prepared_statement_name,
)

POOLER_SAFE_ARGS = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": unique_prepared_statement_name,
    "timeout": CONNECT_TIMEOUT_SECONDS,
}


def test_rewrites_the_driver_to_asyncpg():
    target = to_async_target("postgresql://user:pw@localhost:5432/app")

    assert target.url.drivername == "postgresql+asyncpg"
    assert target.url.host == "localhost"
    assert target.url.port == 5432
    assert target.url.database == "app"
    assert target.connect_args == POOLER_SAFE_ARGS


def test_keeps_the_password_even_with_special_characters():
    target = to_async_target("postgresql://user:p%40ss%2Fw@host/app")

    assert target.url.password == "p@ss/w"


def test_passes_sslmode_through_as_the_asyncpg_ssl_argument():
    target = to_async_target(
        "postgresql://user:pw@ep-x.neon.tech/app?sslmode=require&channel_binding=require"
    )

    assert target.connect_args == {**POOLER_SAFE_ARGS, "ssl": "require"}
    assert dict(target.url.query) == {}


def test_accepts_an_asyncpg_url_with_ssl_parameter():
    target = to_async_target("postgresql+asyncpg://u:p@h/app?ssl=verify-full")

    assert target.url.drivername == "postgresql+asyncpg"
    assert isinstance(target.connect_args["ssl"], ssl.SSLContext)


def test_verify_full_builds_a_default_context_that_checks_the_host_and_the_certificate(
    monkeypatch,
):
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)

    context = to_async_target(
        "postgresql://u:p@h/app?sslmode=verify-full"
    ).connect_args["ssl"]

    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_verify_full_keeps_the_string_when_a_root_certificate_is_configured(
    monkeypatch,
):
    monkeypatch.setenv("PGSSLROOTCERT", "/etc/ssl/root.crt")

    target = to_async_target("postgresql://u:p@h/app?sslmode=verify-full")

    assert target.connect_args["ssl"] == "verify-full"


def test_the_other_modes_are_passed_through_unchanged(monkeypatch):
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)

    for mode in ("require", "verify-ca", "prefer", "disable"):
        target = to_async_target(f"postgresql://u:p@h/app?sslmode={mode}")
        assert target.connect_args["ssl"] == mode


def test_disables_both_statement_caches_for_pgbouncer():
    target = to_async_target("postgresql://u:pw@ep-x-pooler.neon.tech/app")

    assert target.connect_args["statement_cache_size"] == 0
    assert target.connect_args["prepared_statement_cache_size"] == 0


def test_prepared_statement_names_are_unique_per_statement():
    names = {unique_prepared_statement_name() for _ in range(100)}

    assert len(names) == 100
    assert all(name.startswith("__asyncpg_") for name in names)


def test_sets_a_bounded_connect_timeout():
    target = to_async_target("postgresql://u:pw@ep-x.neon.tech/app")

    assert 0 < CONNECT_TIMEOUT_SECONDS <= 30
    assert target.connect_args["timeout"] == CONNECT_TIMEOUT_SECONDS
