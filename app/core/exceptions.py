from fastapi import HTTPException, status

class GeospatialAPIException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code=status_code, detail=detail)

class FileFormatError(GeospatialAPIException):
    def __init__(self, detail: str = "Invalid or corrupted geospatial file."):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

class FileNotFoundCustomError(GeospatialAPIException):
    def __init__(self, file_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"Geospatial file with ID '{file_id}' was not found."
        )

class CRSTransformationError(GeospatialAPIException):
    def __init__(self, detail: str = "Failed to transform coordinates to projected CRS."):
        super().__init__(status_code=422, detail=detail)
