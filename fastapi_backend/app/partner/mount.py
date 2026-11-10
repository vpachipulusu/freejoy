"""The mount of the partner surface (ADR-013)."""

from starlette.routing import Match, Mount
from starlette.types import Scope


def _route_path(scope: Scope) -> str:
    """The path below the root path the application is served under (what Starlette routes on)."""
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    return path.removeprefix(root_path) if root_path else path


class PartnerMount(Mount):
    """A ``Mount`` that also takes the bare prefix, ``/partner/v1`` with no trailing slash.

    A plain ``Mount`` matches only ``/partner/v1/...``. For the bare prefix the main app's router
    would answer a 307 with a ``Location`` header, outside the surface wrapper and so without its
    headers. Matching it here sends it to the sub-app like every other path under the prefix.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if (
            match is Match.NONE
            and scope["type"] == "http"
            and _route_path(scope) == self.path
        ):
            return super().matches({**scope, "path": f"{scope['path']}/"})
        return match, child_scope
