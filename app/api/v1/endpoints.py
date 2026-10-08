import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, status
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

@router.post(
    "/",
    response_model=FileInfoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and process a geospatial file",
    description="Accepts either a .zip archive containing an ESRI Shapefile or a .kml file. "
                "Parses features, handles CRS projection to optimal local UTM, and calculates geometric measurements."
)
async def upload_geospatial_file(file: UploadFile = File(...)):
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()

    if suffix not in settings.ALLOWED_EXTENSIONS:
        raise FileFormatError(
            f"Unsupported file extension '{suffix}'. Only .zip (Shapefiles) and .kml files are supported."
        )

    file_id = storage_service.generate_id()
    saved_path = settings.UPLOAD_DIR / f"{file_id}_{filename}"

    # Stream file to disk safely
    try:
        with open(saved_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise FileFormatError(f"Failed to save uploaded file: {str(e)}")

    try:
        # Route to appropriate parser based on file extension
        if suffix == ".zip":
            dataset = shapefile_parser.parse(saved_path)
        elif suffix == ".kml":
            dataset = kml_parser.parse(saved_path)
        else:
            raise FileFormatError(f"Unhandled file extension: {suffix}")

        # Process each feature and calculate measurements
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

            # Accumulate summary stats
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

        measurements_response = MeasurementsResponse(
            id=file_id,
            filename=filename,
            crs=dataset.crs,
            summary=summary,
            features=feature_measurements,
        )

        # Persist record
        file_info = storage_service.save_file_record(
            file_id=file_id,
            filename=filename,
            crs=dataset.crs,
            feature_count=len(dataset.features),
            status=FileStatus.COMPLETED,
            file_type=dataset.file_type,
            measurements_response=measurements_response,
        )

        return file_info

    except FileFormatError:
        storage_service.save_file_record(
            file_id=file_id,
            filename=filename,
            crs="Unknown",
            feature_count=0,
            status=FileStatus.FAILED,
            file_type=suffix,
        )
        raise
    except Exception as e:
        storage_service.save_file_record(
            file_id=file_id,
            filename=filename,
            crs="Unknown",
            feature_count=0,
            status=FileStatus.FAILED,
            file_type=suffix,
        )
        raise FileFormatError(f"Error processing geospatial file: {str(e)}")

@router.get(
    "/{id}/",
    response_model=FileInfoResponse,
    summary="Get file metadata and processing status",
    description="Returns metadata about the uploaded file including status, CRS, and feature count."
)
async def get_file_info(id: str):
    info = storage_service.get_file_info(id)
    if not info:
        raise FileNotFoundCustomError(id)
    return info

@router.get(
    "/{id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Get calculated measurements for all features",
    description="Returns area for Polygons, length for LineStrings, and identifies all attributes and coordinate systems."
)
async def get_file_measurements(id: str):
    measurements = storage_service.get_measurements(id)
    if not measurements:
        info = storage_service.get_file_info(id)
        if not info:
            raise FileNotFoundCustomError(id)
        raise FileNotFoundCustomError(f"Measurements not available for file '{id}' (Status: {info.status})")
    return measurements

@router.get(
    "/",
    response_model=list[FileInfoResponse],
    summary="List all uploaded geospatial files",
    description="Returns a list of all processed files and their status."
)
async def list_files():
    return storage_service.list_all_files()
