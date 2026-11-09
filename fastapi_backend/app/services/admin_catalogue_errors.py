"""Domain errors of the admin catalogue services.

Each carries a stable machine-readable ``code`` (the admin UI branches on it, never on the
message). The routers map them to HTTP: ``CatalogueNotFound`` 404, ``SlugTaken``,
``IllegalTransition`` and ``OfferChanged`` 409, ``InvalidInput`` and ``PublishBlocked`` 422.
"""


class CatalogueError(Exception):
    """Base class: ``code`` is the stable identifier sent to the client."""

    code = "catalogue_error"


class CatalogueNotFound(CatalogueError):
    code = "not_found"

    def __init__(self, resource: str) -> None:
        super().__init__(resource)
        self.resource = resource


class SlugTaken(CatalogueError):
    code = "slug_taken"


class InvalidInput(CatalogueError):
    """A value the schema cannot judge on its own is unacceptable (422).

    ``code`` is one of ``invalid_url``, ``invalid_slug``, ``invalid_postage``,
    ``invalid_window``, ``unknown_category``, ``unknown_merchant``, ``unknown_provider``,
    ``destination_not_allowed`` or ``review_token_required``.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class IllegalTransition(CatalogueError):
    code = "illegal_transition"

    def __init__(self, status: str, action: str) -> None:
        super().__init__(f"{action} from {status}")
        self.status, self.action = status, action


class OfferChanged(CatalogueError):
    """The offer or its links changed after the editor loaded it, so the review token is stale (409).

    Raised before anything is written: the editor reloads the offer and reviews it again.
    """

    code = "offer_changed"


class PublishBlocked(CatalogueError):
    """The offer does not meet the publish rules; ``reasons`` are stable identifiers."""

    code = "publish_blocked"

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(",".join(reasons))
        self.reasons = reasons
