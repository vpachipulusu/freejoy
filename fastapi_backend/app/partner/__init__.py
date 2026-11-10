"""The partner surface (ADR-013): a mounted sub-app that retailers will call with a key.

Everything under ``/partner/v1`` is served from here and nowhere else. The package imports only
``app.core`` (never ``app.api``), so partner code cannot reach the internal routers.
"""
