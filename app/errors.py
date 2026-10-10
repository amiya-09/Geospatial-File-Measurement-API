"""One error body shape everywhere: {"error": {"code": str, "message": str, ...extra}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from app.db.session import BUSY_TIMEOUT_MS

logger = logging.getLogger("app")

RETRY_AFTER_SECONDS = str(BUSY_TIMEOUT_MS // 1000)
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class ErrorDetail(BaseModel):
    code: str
    message: str
    file_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers
        self.extra = extra


def _body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}


_MISSING_UPLOAD = (
    'missing required multipart form field "file": send the upload as a form field named '
    '"file", for example curl -F "file=@samples/sample.kml"'
)


def _describe(err: dict[str, Any]) -> str:
    if tuple(err["loc"]) == ("body", "file") and err["type"] == "missing":
        return _MISSING_UPLOAD
    return f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"


_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")


def _allowed_methods(request: Request) -> str:
    """Methods some route accepts at this request's path, found by asking every route.

    FastAPI keeps included routers as nested route objects, so asking each method in turn is
    simpler and safer than reading the routes' method sets.
    """
    allowed = []
    for method in _METHODS:
        scope = {**request.scope, "method": method}
        if any(route.matches(scope)[0] == Match.FULL for route in request.app.router.routes):
            allowed.append(method)
    return ", ".join(allowed)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            _body(exc.code, exc.message, **exc.extra),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Locations and messages only; never echo the offending input back.
        problems = "; ".join(_describe(err) for err in exc.errors())
        return JSONResponse(_body("validation_error", problems), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        # Keep headers Starlette sets. Allow on a 405 is required by RFC 9110, but Starlette
        # fills it from the first matching route only, so a path served by two routes
        # (GET and POST /api/files/) would advertise one method. List all of them.
        headers = exc.headers
        if exc.status_code == 405:
            headers = {**(exc.headers or {}), "Allow": _allowed_methods(request)}
        return JSONResponse(
            _body(code, str(exc.detail)), status_code=exc.status_code, headers=headers
        )

    # SQLite allows one writer at a time. Under heavy load a request can wait too long for the
    # write lock or for a free pooled connection. That is the server being busy, not a bug, so
    # say "try again" rather than 500.
    def database_busy(reason: str) -> JSONResponse:
        logger.warning("database busy: %s", reason)
        return JSONResponse(
            _body("database_busy", "The database is busy; retry shortly"),
            status_code=503,
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )

    @app.exception_handler(OperationalError)
    async def database_error(request: Request, exc: OperationalError) -> JSONResponse:
        if "database is locked" in str(exc.orig):
            return database_busy("database is locked")
        return await unexpected_error(request, exc)

    @app.exception_handler(PoolTimeoutError)
    async def pool_timeout(_: Request, exc: PoolTimeoutError) -> JSONResponse:
        return database_busy("no free connection in the pool")

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=exc)
        return JSONResponse(_body("internal_error", "Internal server error"), status_code=500)
