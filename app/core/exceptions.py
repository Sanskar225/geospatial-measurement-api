"""Domain exceptions.

These are plain Python exceptions on purpose: the geospatial/domain layer must not
know about HTTP. ``app/api/errors.py`` maps them to JSON responses using the
``status_code`` / ``error_code`` attributes declared here.
"""

from typing import Any


class AppError(Exception):
    """Base class for every error we raise deliberately."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    default_message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **extra: Any) -> None:
        self.message = message or self.default_message
        self.extra = extra
        super().__init__(self.message)


# --- input problems (4xx) -----------------------------------------------------
class UnsupportedFileTypeError(AppError):
    status_code = 415
    error_code = "UNSUPPORTED_FILE_TYPE"
    default_message = "Unsupported file type."


class FileFormatError(AppError):
    status_code = 400
    error_code = "INVALID_FILE"
    default_message = "Invalid or corrupted geospatial file."


class FileTooLargeError(AppError):
    status_code = 413
    error_code = "FILE_TOO_LARGE"
    default_message = "The uploaded file exceeds the configured size limit."


class CRSError(AppError):
    status_code = 422
    error_code = "CRS_ERROR"
    default_message = "Coordinate reference system problem."


class MissingCRSError(CRSError):
    error_code = "CRS_MISSING"


class InvalidCRSError(CRSError):
    error_code = "CRS_INVALID"


class CRSTransformationError(CRSError):
    error_code = "CRS_TRANSFORMATION_FAILED"


class InvalidCoordinatesError(CRSError):
    """Coordinates are outside the valid range for the declared CRS."""

    error_code = "COORDINATES_INVALID"


# --- resource state ---------------------------------------------------------
class FileRecordNotFoundError(AppError):
    status_code = 404
    error_code = "FILE_NOT_FOUND"

    def __init__(self, file_id: str) -> None:
        super().__init__(f"Geospatial file with ID '{file_id}' was not found.", file_id=file_id)


class FileNotReadyError(AppError):
    status_code = 409
    error_code = "FILE_NOT_READY"

    def __init__(self, file_id: str, status: str) -> None:
        super().__init__(
            f"File '{file_id}' is still {status.lower()}; poll GET /api/files/{file_id}/ "
            "until its status is COMPLETED.",
            file_id=file_id,
        )


class FileProcessingFailedError(AppError):
    status_code = 422
    error_code = "FILE_PROCESSING_FAILED"

    def __init__(self, file_id: str, reason: str | None) -> None:
        super().__init__(f"Processing of file '{file_id}' failed: {reason or 'unknown error'}", file_id=file_id)


# --- server problems (5xx) --------------------------------------------------
class ProcessingError(AppError):
    status_code = 500
    error_code = "PROCESSING_ERROR"
    default_message = "Unexpected error while processing the file."
