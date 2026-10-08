from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.api.v1.endpoints import router as api_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="""
## Enterprise Geospatial File Measurement API

Designed for high-precision drone surveying and geospatial intelligence platforms like **Aereo Cloud**.

### Key Capabilities:
- **Multi-Format Ingestion**: Ingests ESRI Shapefile archives (`.zip`) and Keyhole Markup Language (`.kml`).
- **Dynamic Projected CRS**: Auto-detects local UTM zones based on geometry centroids to perform accurate planar metric calculations rather than flawed geographic degree math.
- **Geometric Measurement**:
  - **Polygons / MultiPolygons**: Area calculation ($m^2$, hectares, $km^2$, acres).
  - **LineStrings / MultiLineStrings**: Length calculation ($m$, $km$, feet).
  - **Points**: Handled gracefully without crash or invalid measurements.
- **Resilient & Fault-Tolerant**: Self-recovers invalid polygon rings and supports MultiGeometry placemarks.
    """,
    docs_url="/docs",
    redoc_url="/redoc",
)

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
    }
