"""Maps domain exceptions to JSON error responses.

Every error body has the same shape: ``{"detail": ..., "error_code": ..., "file_id"?: ...}``
(``detail`` matches FastAPI's own convention so generic clients keep working).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        body = {"detail": exc.message, "error_code": exc.error_code, **exc.extra}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Log the traceback server-side; never leak internals (paths, SQL, stack) to the client.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error.", "error_code": "INTERNAL_ERROR"},
        )
