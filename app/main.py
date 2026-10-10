"""Application factory and entry point (``uvicorn app.main:app``)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_exception_handlers
from app.api.middleware import register_middleware
from app.api.v1.endpoints import router as files_router
from app.core.config import settings
from app.core.database import check_db, dispose_db, init_db
from app.core.logging import configure_logging
from app.services.file_service import file_service

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "sample_data"

DESCRIPTION = """
Upload a **Shapefile (.zip)**, **KML** or **KMZ** and get per-feature **area** (polygons) and
**length** (lines) in metres.

* Measurements are taken in a **local metric projection** (UTM, UPS near the poles) chosen per
  feature - never in lat/lon degrees - and cross-checked against **geodesic** values on WGS-84.
* Bad features never fail the whole file: each feature reports its own `status` and `notes`.
* Large files can be processed in the background with `?background=true`.
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    await init_db()
    await file_service.recover_interrupted()
    logger.info("Started %s v%s", settings.PROJECT_NAME, settings.VERSION)
    yield
    await dispose_db()


def create_app() -> FastAPI:
    configure_logging(settings.LOG_LEVEL)
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    # "*" origins cannot be combined with credentials (browsers reject it); we use no cookies anyway.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Total-Count", "Location"],
    )
    register_middleware(app)
    register_exception_handlers(app)
    app.include_router(files_router, prefix=settings.API_V1_STR)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    if SAMPLE_DATA_DIR.exists():
        app.mount("/sample_data", StaticFiles(directory=str(SAMPLE_DATA_DIR)), name="sample_data")

    @app.get("/", include_in_schema=False)
    async def dashboard():
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return {"message": f"{settings.PROJECT_NAME}. See /docs for the API documentation."}

    @app.get("/health", tags=["health"], summary="Liveness + database readiness")
    async def health() -> JSONResponse:
        db_ok = await check_db()
        return JSONResponse(
            status_code=200 if db_ok else 503,
            content={
                "status": "HEALTHY" if db_ok else "UNHEALTHY",
                "service": settings.PROJECT_NAME,
                "version": settings.VERSION,
                "database": "CONNECTED" if db_ok else "UNAVAILABLE",
            },
        )

    return app


app = create_app()
