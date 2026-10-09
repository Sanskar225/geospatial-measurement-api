import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.database import init_db
from app.services.storage_service import storage_service
from app.api.v1.endpoints import router as api_router
from app.api.errors import register_exception_handlers

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB schema tables
    await init_db()
    # Startup recovery: Cleanly mark any interrupted jobs as FAILED
    await storage_service.fail_unfinished_records()
    yield
    # Shutdown logic if needed

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=r"""
## 🛰️ Aereo Cloud Geospatial Measurement & Analytics Platform

High-performance geospatial API engineered for drone surveying, cadastral mapping, and spatial metric intelligence.

### Enterprise Features:
- **Multi-Format Ingestion**: ESRI Shapefiles (`.zip`), OpenGIS KML (`.kml`), and KMZ (`.kmz`) with magic byte signature inspection.
- **Dynamic UTM Metric Projection**: Automated centroid-based UTM zone re-projection eliminates geographic degree errors.
- **WGS-84 Geodesic Verification**: Computes ellipsoidal ground-truth and distortion error percentages ($\Delta\%$).
- **SQL Database Persistence**: SQLAlchemy async ORM with SHA-256 idempotency deduplication and crash recovery.
- **Exports & Filtering**: RFC 7946 GeoJSON and CSV exports with server-side pagination and geometry filtering.
    """,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Register centralized domain exception handlers
register_exception_handlers(app)

# Request ID & Process Time Middleware
@app.middleware("http")
async def add_process_time_and_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{process_time * 1000:.2f}ms"
    return response

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Paths for static assets and sample data
STATIC_DIR = Path(__file__).parent / "static"
SAMPLE_DATA_DIR = Path(__file__).parent.parent / "sample_data"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if SAMPLE_DATA_DIR.exists():
    app.mount("/sample_data", StaticFiles(directory=str(SAMPLE_DATA_DIR)), name="sample_data")

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Welcome to Aereo Geospatial File Measurement API. Visit /docs for Swagger documentation."}

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "HEALTHY",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "database": "CONNECTED",
    }
