"""One error body shape everywhere: {"error": {"code": str, "message": str, ...extra}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app")

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class ErrorDetail(BaseModel):
    code: str
    message: str
    file_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
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


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, **exc.extra), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Locations and messages only; never echo the offending input back.
        problems = "; ".join(_describe(err) for err in exc.errors())
        return JSONResponse(_body("validation_error", problems), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=exc)
        return JSONResponse(_body("internal_error", "Internal server error"), status_code=500)
