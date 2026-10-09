from typing import Any, Dict, Optional
from fastapi import HTTPException, status

class AppError(Exception):
    """Base domain exception with status_code, message, error_code, and extra metadata."""
    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        error_code: str = "BAD_REQUEST",
        file_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.file_id = file_id
        self.extra = extra or {}
        if file_id:
            self.extra["file_id"] = file_id

class FileFormatError(AppError):
    def __init__(self, detail: str = "Invalid or corrupted geospatial file.", file_id: Optional[str] = None):
        super().__init__(
            message=detail,
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_FILE_FORMAT",
            file_id=file_id,
        )

class FileTooLargeError(AppError):
    def __init__(self, detail: str = "Uploaded file exceeds maximum allowed size.", file_id: Optional[str] = None):
        super().__init__(
            message=detail,
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            error_code="FILE_TOO_LARGE",
            file_id=file_id,
        )

class FileNotFoundCustomError(AppError):
    def __init__(self, file_id: str):
        super().__init__(
            message=f"Geospatial file with ID '{file_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="FILE_NOT_FOUND",
            file_id=file_id,
        )

class MissingCRSError(AppError):
    def __init__(self, detail: str = "Missing CRS definition and coordinates are not valid lon/lat.", file_id: Optional[str] = None):
        super().__init__(
            message=detail,
            status_code=422,
            error_code="CRS_MISSING",
            file_id=file_id,
        )

class CRSTransformationError(AppError):
    def __init__(self, detail: str = "Failed to transform coordinates to projected CRS.", file_id: Optional[str] = None):
        super().__init__(
            message=detail,
            status_code=422,
            error_code="CRS_TRANSFORMATION_ERROR",
            file_id=file_id,
        )

class InvalidCoordinatesError(AppError):
    def __init__(self, detail: str = "Geometry coordinates are outside valid bounds.", file_id: Optional[str] = None):
        super().__init__(
            message=detail,
            status_code=422,
            error_code="INVALID_COORDINATES",
            file_id=file_id,
        )
