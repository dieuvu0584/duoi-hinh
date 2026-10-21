import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

log = logging.getLogger(__name__)


class ApiError(Exception):
    """Typed error with a stable machine-readable code and a safe message."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        fields: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields
        self.headers = headers


def _envelope(code: str, message: str, fields: list[dict[str, str]] | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message, "request_id": request_id_var.get()}}
    if fields:
        body["error"]["fields"] = fields
    return body


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return JSONResponse(
            _envelope(exc.code, exc.message, exc.fields),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "issue": e["type"]}
            for e in exc.errors()
        ]
        body = _envelope("validation_error", "Invalid request", fields)
        return JSONResponse(body, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        body = _envelope(f"http_{exc.status_code}", "Request failed")
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        # Never leak internals; the stack trace goes to the (redacted) log only.
        log.exception("unhandled error")
        return JSONResponse(_envelope("internal_error", "Internal error"), status_code=500)
