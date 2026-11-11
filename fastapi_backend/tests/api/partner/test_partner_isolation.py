"""``app/partner`` imports only ``app.core`` and itself (ADR-013), and its mounted routes are its own.

The partner package must not be able to reach an internal router, service or repository by import. The
check reads the source (an AST), so it covers a module that is never imported by the tests. It also
refuses the ways round it: dynamic imports, and a relative import that climbs out of the package.
"""

import ast
from pathlib import Path

import pytest
from fastapi.routing import iter_route_contexts

from app.main import app
from app.partner.app import create_partner_api
from app.partner.mount import PartnerMount

PARTNER_DIR = Path(__file__).resolve().parents[3] / "app" / "partner"
ALLOWED_APP_PREFIXES = ("app.core", "app.partner")
DYNAMIC_IMPORTS = {"importlib", "__import__"}


def _allowed(module: str) -> bool:
    return any(module == p or module.startswith(f"{p}.") for p in ALLOWED_APP_PREFIXES)


def forbidden_imports(source: str) -> list[str]:
    """What the module imports that ``app/partner`` may not, as readable strings."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if (name == "app" or name.startswith("app.")) and not _allowed(name):
                    problems.append(f"import {name}")
                if name.split(".")[0] in DYNAMIC_IMPORTS:
                    problems.append(f"import {name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level > 1:
                problems.append(
                    f"relative import climbs out of the package: level {node.level}"
                )
            elif node.level == 0 and (module == "app" or module.startswith("app.")):
                names = [a.name for a in node.names]
                if module == "app":
                    problems.extend(
                        f"from app import {n}"
                        for n in names
                        if not _allowed(f"app.{n}")
                    )
                elif not _allowed(module):
                    problems.append(f"from {module} import {', '.join(names)}")
            if module.split(".")[0] in DYNAMIC_IMPORTS:
                problems.append(f"from {module} import ...")
        elif isinstance(node, ast.Name) and node.id == "__import__":
            problems.append("__import__")
    return problems


def test_there_are_partner_modules_to_check() -> None:
    names = {p.name for p in PARTNER_DIR.glob("*.py")}

    assert {
        "__init__.py",
        "app.py",
        "auth.py",
        "deps.py",
        "keys.py",
        "limits.py",
        "mount.py",
        "routes.py",
        "schema.py",
        "surface.py",
    } <= names


@pytest.mark.parametrize(
    "path", sorted(PARTNER_DIR.rglob("*.py")), ids=lambda p: p.name
)
def test_a_partner_module_imports_only_core_and_partner(path: Path) -> None:
    assert forbidden_imports(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from app.core.config import settings", []),
        ("from app.partner.surface import PartnerSurface", []),
        ("from app.partner import mount", []),
        ("import app.core.errors", []),
        ("from . import surface", []),
        ("from fastapi import FastAPI", []),
        ("from app.api.v1 import health", ["from app.api.v1 import health"]),
        (
            "from app.api.auth import require_internal_key",
            ["from app.api.auth import require_internal_key"],
        ),
        (
            "from app.services.auth import AuthService",
            ["from app.services.auth import AuthService"],
        ),
        (
            "from app.repositories.auth import AuthRepository",
            ["from app.repositories.auth import AuthRepository"],
        ),
        ("from app.main import app", ["from app.main import app"]),
        ("from app import api", ["from app import api"]),
        ("import app.api.v1.health", ["import app.api.v1.health"]),
        ("from app.corex import y", ["from app.corex import y"]),
        ("from .. import api", ["relative import climbs out of the package: level 2"]),
        ("import importlib", ["import importlib"]),
        ("from importlib import import_module", ["from importlib import ..."]),
        ("__import__('app.api.v1.health')", ["__import__"]),
    ],
)
def test_the_import_check_catches_what_it_should(
    source: str, expected: list[str]
) -> None:
    assert forbidden_imports(source) == expected


def test_the_mounted_sub_app_serves_the_routes_the_schema_is_generated_from() -> None:
    """The committed partner schema comes from ``create_partner_api()``; the mounted app must serve the
    same routes, so the contract cannot describe paths the mount does not have (or miss ones it has)."""
    mount = next(r for r in app.routes if isinstance(r, PartnerMount))

    mounted = sorted(c.path or "" for c in iter_route_contexts(mount.routes))
    generated = sorted(
        c.path or "" for c in iter_route_contexts(create_partner_api().routes)
    )

    assert mounted == generated


# What partner code must never touch (ADR-013): the internal credentials, the forwarded client address and the
# owner's database session. Identifiers only: a docstring may still say why.
FORBIDDEN_NAMES = frozenset(
    {
        "require_internal_key",
        "get_session_identity",
        "get_current_identity",
        "forwarded_client_ip",
        "client_ip",
        "client_key",
        "get_session_provider",
        "get_session",
        "DefaultSessionProvider",
        "async_session_maker",
        "build_engine",
        "engine",
        "internal_authenticated",
        "client",
    }
)
FORBIDDEN_STRINGS = frozenset(
    {
        "x-client-ip",
        "x-forwarded-for",
        "x-real-ip",
        "forwarded",
        "x-vercel-forwarded-for",
        "cf-connecting-ip",
        "true-client-ip",
        "client",
    }
)


def forbidden_uses(source: str) -> list[str]:
    """Identifiers and header-name strings in ``source`` that partner code must not use."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.alias):
            names.append(node.name.split(".")[-1])
        problems.extend(n for n in names if n in FORBIDDEN_NAMES)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.lower() in FORBIDDEN_STRINGS
        ):
            problems.append(node.value)
    return problems


@pytest.mark.parametrize(
    "path", sorted(PARTNER_DIR.rglob("*.py")), ids=lambda p: p.name
)
def test_partner_code_never_uses_internal_credentials_client_addresses_or_the_owner_session(
    path: Path,
) -> None:
    assert forbidden_uses(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x = 1", []),
        ("from app.api.auth import require_internal_key", ["require_internal_key"]),
        ("Depends(get_session_identity)", ["get_session_identity"]),
        ("request.state.internal_authenticated", ["internal_authenticated"]),
        ("client_ip(request)", ["client_ip"]),
        ("headers['X-Client-IP']", ["X-Client-IP"]),
        ("h = 'X-Forwarded-For'", ["X-Forwarded-For"]),
        ("provider = get_session_provider()", ["get_session_provider"]),
        ("h = 'CF-Connecting-IP'", ["CF-Connecting-IP"]),
        ("h = 'True-Client-IP'", ["True-Client-IP"]),
        ("h = 'X-Vercel-Forwarded-For'", ["X-Vercel-Forwarded-For"]),
        ("request.client.host", ["client"]),
        ("scope['client']", ["client"]),
        ("maker = async_session_maker()", ["async_session_maker"]),
        ('"""require_internal_key is mentioned only in a docstring"""', []),
    ],
)
def test_the_forbidden_use_check_catches_what_it_should(
    source: str, expected: list[str]
) -> None:
    assert forbidden_uses(source) == expected
