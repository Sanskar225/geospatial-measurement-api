"""REST endpoints. Handlers stay thin: parse the request, call the service, shape the response."""

import re
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Path, Query, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import get_file_service
from app.core.config import settings
from app.models.schemas import (
    ErrorResponse,
    FeatureStatus,
    FileInfoResponse,
    GeometryType,
    MeasurementsResponse,
)
from app.services.file_service import FileService

router = APIRouter(prefix="/files", tags=["files"])

FileId = Path(..., min_length=1, max_length=64, description="File ID returned by the upload call.")

_ERR = {"model": ErrorResponse}
UPLOAD_ERRORS = {
    400: {**_ERR, "description": "Corrupted file or invalid content"},
    413: {**_ERR, "description": "File larger than the configured limit"},
    415: {**_ERR, "description": "Unsupported file type"},
    422: {**_ERR, "description": "CRS missing/invalid, or other semantic problem"},
}
NOT_FOUND = {404: {**_ERR, "description": "File not found"}}
NOT_READY = {
    409: {**_ERR, "description": "File is still being processed"},
    422: {**_ERR, "description": "Processing of this file failed"},
}


@router.post(
    "/",
    response_model=FileInfoResponse,
    status_code=201,
    summary="Upload a Shapefile (.zip), KML or KMZ",
    responses={
        200: {"model": FileInfoResponse, "description": "Identical file already processed; existing record returned"},
        202: {"model": FileInfoResponse, "description": "Accepted for background processing (?background=true)"},
        **UPLOAD_ERRORS,
    },
)
async def upload_file(
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="A .zip containing a Shapefile, or a .kml / .kmz file."),
    background: bool = Query(
        False,
        description="Return immediately (202) and process in the background; poll GET /api/files/{id}/.",
    ),
    service: FileService = Depends(get_file_service),
) -> FileInfoResponse:
    """Upload, validate and process a geospatial file.

    By default the file is processed before the response is sent (201, status COMPLETED).
    Use `?background=true` for large files.
    """
    result = await service.register_upload(file)
    info = result.info
    if not result.created:
        response.status_code = 200  # idempotent: same bytes -> same record
    elif background:
        background_tasks.add_task(service.process, info.id, raise_errors=False)
        response.status_code = 202
    else:
        await service.process(info.id)
        info = await service.get_info(info.id)
    response.headers["Location"] = f"{settings.API_V1_STR}/files/{info.id}/"
    return info


@router.get("/", response_model=list[FileInfoResponse], summary="List uploaded files")
async def list_files(
    response: Response,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: FileService = Depends(get_file_service),
) -> list[FileInfoResponse]:
    files, total = await service.list_files(limit, offset)
    response.headers["X-Total-Count"] = str(total)
    return files


@router.get(
    "/{file_id}/",
    response_model=FileInfoResponse,
    summary="File information and processing status",
    responses=NOT_FOUND,
)
async def get_file(file_id: str = FileId, service: FileService = Depends(get_file_service)) -> FileInfoResponse:
    return await service.get_info(file_id)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Per-feature measurements",
    responses={**NOT_FOUND, **NOT_READY},
)
async def get_measurements(
    file_id: str = FileId,
    geometry_type: GeometryType | None = Query(None, description="Only features of this geometry type."),
    status: FeatureStatus | None = Query(None, description="Only features with this status."),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: FileService = Depends(get_file_service),
) -> MeasurementsResponse:
    """Areas (polygons) and lengths (lines) in metres, plus a file-level summary.

    The `summary` always covers the whole file; `features` honours the filters and pagination.
    """
    return await service.get_measurements(
        file_id, geometry_type=geometry_type, status=status, limit=limit, offset=offset
    )


@router.get(
    "/{file_id}/export",
    summary="Download results as GeoJSON or CSV (streamed)",
    responses={**NOT_FOUND, **NOT_READY},
)
async def export_file(
    file_id: str = FileId,
    fmt: Literal["geojson", "csv"] = Query("geojson", alias="format"),
    service: FileService = Depends(get_file_service),
) -> StreamingResponse:
    info = await service.ensure_exportable(file_id)  # raises 404/409/422 before streaming starts
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", info.filename.rsplit(".", 1)[0])[:80] or "export"
    media_type = "application/geo+json" if fmt == "geojson" else "text/csv; charset=utf-8"
    ext = "geojson" if fmt == "geojson" else "csv"
    return StreamingResponse(
        service.stream_export(file_id, fmt),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}_measurements.{ext}"'},
    )


@router.delete("/{file_id}/", status_code=204, summary="Delete a file and its results", responses=NOT_FOUND)
async def delete_file(file_id: str = FileId, service: FileService = Depends(get_file_service)) -> Response:
    await service.delete(file_id)
    return Response(status_code=204)
