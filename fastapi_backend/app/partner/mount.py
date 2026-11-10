"""The mount of the partner surface (ADR-013)."""

from starlette.routing import Match, Mount
from starlette.types import Scope


def _under(path: str, prefix: str) -> bool:
    """``path`` is the prefix itself or something below it (a plain string comparison)."""
    return path == prefix or path.startswith(f"{prefix}/")


def _route_path(scope: Scope) -> str:
    """The path below the root path the application is served under (what Starlette routes on)."""
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    return path.removeprefix(root_path) if root_path else path


class PartnerMount(Mount):
    """A ``Mount`` that takes everything under its prefix, matched by string comparison.

    Starlette's own ``Mount`` matches a regular expression in which ``.`` does not match a newline,
    so ``/partner/v1/a%0Ab`` would fall out of the mount and be answered by the main app, without
    the surface headers. It also leaves the bare prefix ``/partner/v1`` to the router, which
    answers it with a 307. A string comparison has neither gap.

    It compares both the path below the root path (what Starlette routes on) and the full
    ``path``, so a runtime that sets a ``root_path`` of ``/partner/v1`` cannot slide a request
    such as ``/partner/v1/health`` out from under the mount and into a main-app route.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] not in ("http", "websocket"):
            return Match.NONE, {}
        if not (
            _under(_route_path(scope), self.path) or _under(scope["path"], self.path)
        ):
            return Match.NONE, {}
        root_path: str = scope.get("root_path", "")
        child_scope: Scope = {
            "path_params": dict(scope.get("path_params", {})),
            "app_root_path": scope.get("app_root_path", root_path),
            "root_path": root_path + self.path,
            "endpoint": self.app,
        }
        return Match.FULL, child_scope
