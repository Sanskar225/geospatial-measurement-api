import shutil
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Query, BackgroundTasks, Response, status
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.exceptions import (
    FileFormatError,
    FileNotFoundCustomError,
)
from app.models.schemas import (
    FileInfoResponse,
    MeasurementsResponse,
    MeasurementsSummary,
    FileStatus,
)
from app.services.parsers.shapefile_parser import shapefile_parser
from app.services.parsers.kml_parser import kml_parser
from app.services.measurement_service import measurement_service
from app.services.storage_service import storage_service

router = APIRouter(prefix="/files", tags=["Geospatial Files"])

def _validate_magic_bytes(file_path: Path, ext: str):
    """Deep inspection of file headers to prevent extension spoofing."""
    with open(file_path, "rb") as f:
        header = f.read(16)
        if ext == ".zip":
            # Standard ZIP magic header PK\x03\x04
            if not header.startswith(b"PK\x03\x04"):
                raise FileFormatError("File header mismatch: Not a genuine ZIP archive.")
        elif ext == ".kml":
            # KML should be XML/text based
            preview = header.decode("latin1", errors="ignore").lower()
            if not ("<" in preview or "xml" in preview or "kml" in preview):
                raise FileFormatError("File header mismatch: Not a genuine XML/KML file.")

async def process_geospatial_file_task(saved_path: Path, file_id: str, suffix: str):
    """Background or synchronous processing worker."""
    try:
        await storage_service.update_progress(file_id, 30, status="PROCESSING")

        # Parse dataset
        if suffix == ".zip":
            dataset = shapefile_parser.parse(saved_path)
        elif suffix == ".kml":
            dataset = kml_parser.parse(saved_path)
        else:
            raise FileFormatError(f"Unhandled file extension: {suffix}")

        await storage_service.update_progress(file_id, 60, status="PROCESSING")

        # Process measurements
        feature_measurements = []
        summary = MeasurementsSummary(total_features=len(dataset.features))

        for feat in dataset.features:
            meas = measurement_service.process_feature(
                feature_id=feat.feature_id,
                geom=feat.geometry,
                properties=feat.properties,
                source_crs_str=dataset.crs,
            )
            feature_measurements.append(meas)

            if meas.geometry_type in ("Polygon", "MultiPolygon"):
                summary.polygon_count += 1
                if meas.measurement and meas.measurement.value is not None:
                    summary.total_area_sq_meters += meas.measurement.value
            elif meas.geometry_type in ("LineString", "MultiLineString"):
                summary.linestring_count += 1
                if meas.measurement and meas.measurement.value is not None:
                    summary.total_length_meters += meas.measurement.value
            elif meas.geometry_type in ("Point", "MultiPoint"):
                summary.point_count += 1
            else:
                summary.unsupported_count += 1

        summary.total_area_sq_meters = round(summary.total_area_sq_meters, 2)
        summary.total_area_hectares = round(summary.total_area_sq_meters / 10000.0, 4)
        summary.total_length_meters = round(summary.total_length_meters, 2)
        summary.total_length_kilometers = round(summary.total_length_meters / 1000.0, 4)

        if summary.polygon_count > 0:
            summary.average_polygon_area_sq_m = round(summary.total_area_sq_meters / summary.polygon_count, 2)
        if summary.linestring_count > 0:
            summary.average_linestring_length_m = round(summary.total_length_meters / summary.linestring_count, 2)

        await storage_service.update_progress(file_id, 90, status="PROCESSING")

        # Persist features to database
        await storage_service.complete_file_processing(
            file_id=file_id,
            crs=dataset.crs,
            features=feature_measurements,
            summary=summary,
        )

    except Exception as e:
        await storage_service.mark_file_failed(file_id, str(e))
        raise

@router.post(
    "/",
    response_model=FileInfoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a geospatial file",
    description="Accepts .zip (Shapefile) or .kml. Performs magic byte inspection, SHA-256 deduplication, "
                "dynamic UTM projection, and geometric metric calculations."
)
async def upload_geospatial_file(
    file: UploadFile = File(...),
):
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in settings.ALLOWED_EXTENSIONS:
        raise FileFormatError(
            f"Unsupported file extension '{suffix}'. Only .zip (Shapefiles) and .kml files are supported."
        )

    file_id = storage_service.generate_id()
    saved_path = settings.UPLOAD_DIR / f"{file_id}_{filename}"

    # Stream to disk
    try:
        with open(saved_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise FileFormatError(f"Failed to stream uploaded file: {str(e)}")

    # Deep magic bytes validation
    _validate_magic_bytes(saved_path, suffix)

    # Compute SHA-256
    file_hash = storage_service.compute_sha256(saved_path)
    file_size = saved_path.stat().st_size

    # Check for deduplication (Idempotent response)
    existing = await storage_service.find_existing_by_hash(file_hash)
    if existing:
        # File was already processed previously
        saved_path.unlink(missing_ok=True)
        return existing

    # Create Initial Processing Record
    file_type = "Shapefile (.zip)" if suffix == ".zip" else "KML"
    await storage_service.create_file_record(
        file_id=file_id,
        filename=filename,
        file_type=file_type,
        file_size_bytes=file_size,
        file_hash=file_hash,
    )

    # Process immediately (fast-path)
    await process_geospatial_file_task(saved_path, file_id, suffix)

    # Retrieve and return completed record
    info = await storage_service.get_file_info(file_id)
    if not info:
        raise FileFormatError("Failed to retrieve file record after processing.")
    return info

@router.get(
    "/{id}/",
    response_model=FileInfoResponse,
    summary="Get file metadata and processing status",
    description="Returns metadata about the uploaded file including status, progress, CRS, and feature count."
)
async def get_file_info(id: str):
    info = await storage_service.get_file_info(id)
    if not info:
        raise FileNotFoundCustomError(id)
    return info

@router.get(
    "/{id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Get calculated measurements for all features",
    description="Returns area for Polygons, length for LineStrings, geodesic ground truth, and optional pagination & filtering."
)
async def get_file_measurements(
    id: str,
    geometry_type: Optional[str] = Query(None, description="Filter by geometry type (e.g. 'Polygon', 'LineString', 'Point')"),
    limit: int = Query(100, ge=1, le=1000, description="Page limit for features list"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    measurements = await storage_service.get_measurements(
        file_id=id,
        geometry_type=geometry_type,
        limit=limit,
        offset=offset,
    )
    if not measurements:
        info = await storage_service.get_file_info(id)
        if not info:
            raise FileNotFoundCustomError(id)
        raise FileNotFoundCustomError(f"Measurements not available for file '{id}' (Status: {info.status})")
    return measurements

@router.get(
    "/{id}/export",
    summary="Export features and measurements",
    description="Export features as either GeoJSON FeatureCollection or CSV tabular format."
)
async def export_file_measurements(
    id: str,
    format: str = Query("geojson", pattern="^(geojson|csv)$", description="Export format ('geojson' or 'csv')")
):
    if format == "geojson":
        geojson_data = await storage_service.export_geojson(id)
        if not geojson_data:
            raise FileNotFoundCustomError(id)
        return geojson_data
    elif format == "csv":
        csv_data = await storage_service.export_csv(id)
        if not csv_data:
            raise FileNotFoundCustomError(id)
        return PlainTextResponse(
            content=csv_data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=measurements_{id}.csv"}
        )

@router.get(
    "/",
    response_model=list[FileInfoResponse],
    summary="List all uploaded geospatial files",
    description="Returns a list of all processed files and their status."
)
async def list_files():
    return await storage_service.list_all_files()
