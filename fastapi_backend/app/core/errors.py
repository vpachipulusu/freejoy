"""Error envelope: every error is {"success": false, "error": "<message>"}."""

import logging
from collections.abc import Mapping
from typing import Any, Final, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT

logger = logging.getLogger("yourapp.errors")


class ErrorResponse(BaseModel):
    """Body of every error response (documents the runtime envelope in OpenAPI)."""

    success: Literal[False] = False
    error: str


# Machine-readable refusal codes for sign-in. The UI branches on ``code``, never on the message.
# Owner-accepted shape: 401 with one of these codes. Change the codes, messages and status here only.
AuthChallengeCode = Literal["totp_required", "totp_invalid"]
TOTP_REQUIRED: Final[AuthChallengeCode] = "totp_required"
TOTP_INVALID: Final[AuthChallengeCode] = "totp_invalid"
TOTP_REQUIRED_MESSAGE = "Two-factor code required"
TOTP_INVALID_MESSAGE = "Invalid two-factor code"


class AuthChallengeErrorResponse(ErrorResponse):
    """401 body of sign-in: ``code`` says whether a two-factor code is missing or wrong."""

    code: AuthChallengeCode | None = None


class CodedHTTPException(HTTPException):
    """An HTTP error whose body also carries a stable machine-readable ``code``."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        code: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(status_code, detail, dict(headers) if headers else None)
        self.code = code


def totp_required() -> CodedHTTPException:
    return CodedHTTPException(401, TOTP_REQUIRED_MESSAGE, TOTP_REQUIRED)


def totp_invalid() -> CodedHTTPException:
    return CodedHTTPException(401, TOTP_INVALID_MESSAGE, TOTP_INVALID)


# Declared on the app so the generated client types match the runtime body
# (this also replaces FastAPI's default HTTPValidationError for 422).
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid request"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}

INTERNAL_ERROR_MESSAGE = "Internal server error"
INVALID_REQUEST_MESSAGE = "Invalid request"


def error_response(
    status_code: int,
    message: str,
    headers: Mapping[str, str] | None = None,
    code: str | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {"success": False, "error": message}
    if code is not None:
        content["code"] = code
    return JSONResponse(status_code=status_code, content=content, headers=headers)


async def http_exception_handler(
    _request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Error"
    code = exc.code if isinstance(exc, CodedHTTPException) else None
    return error_response(exc.status_code, message, exc.headers, code)


async def validation_exception_handler(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return error_response(HTTP_422_UNPROCESSABLE_CONTENT, INVALID_REQUEST_MESSAGE)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request.scope.get("state", {}).get("request_id")
    logger.error(
        "unhandled exception",
        exc_info=exc,
        extra={"request_id": request_id},
    )
    headers = {"X-Request-ID": request_id} if request_id else None
    return error_response(500, INTERNAL_ERROR_MESSAGE, headers)


def register_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    application.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type]
    )
    application.add_exception_handler(Exception, unhandled_exception_handler)
