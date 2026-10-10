"""HTTP middleware: request correlation ids and an early upload-size guard."""

import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.logging import request_id_ctx

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MULTIPART_OVERHEAD = 64 * 1024  # boundaries + headers around the file part


class MaxBodySizeMiddleware:
    """Reject requests whose declared Content-Length is far above the upload limit.

    Starlette buffers multipart bodies before our endpoint runs, so without this a client
    could make us receive gigabytes before the size check in ``UploadStorage`` fires.
    (A reverse proxy limit, e.g. nginx ``client_max_body_size``, is still recommended.)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in ("POST", "PUT", "PATCH"):
            declared = dict(scope["headers"]).get(b"content-length", b"")
            if declared.isdigit() and int(declared) > settings.max_upload_bytes + _MULTIPART_OVERHEAD:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"Request body exceeds the {settings.MAX_UPLOAD_SIZE_MB:g} MB upload limit.",
                        "error_code": "FILE_TOO_LARGE",
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(MaxBodySizeMiddleware)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{(time.perf_counter() - started) * 1000:.2f}ms"
        return response
