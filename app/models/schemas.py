"""Pydantic schemas: the public API contract and the data passed between layers."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FileStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GeometryType(StrEnum):
    POINT = "Point"
    MULTI_POINT = "MultiPoint"
    LINE_STRING = "LineString"
    MULTI_LINE_STRING = "MultiLineString"
    POLYGON = "Polygon"
    MULTI_POLYGON = "MultiPolygon"
    GEOMETRY_COLLECTION = "GeometryCollection"
    UNKNOWN = "Unknown"


class FeatureStatus(StrEnum):
    """Per-feature outcome. Anything other than SUCCESS never aborts the whole file."""

    SUCCESS = "SUCCESS"
    UNSUPPORTED_GEOMETRY = "UNSUPPORTED_GEOMETRY"  # e.g. GeometryCollection
    EMPTY_GEOMETRY = "EMPTY_GEOMETRY"  # null shape / no vertices
    INVALID_GEOMETRY = "INVALID_GEOMETRY"  # could not be parsed
    INVALID_COORDINATES = "INVALID_COORDINATES"  # outside the valid range for its CRS
    CALCULATION_ERROR = "CALCULATION_ERROR"  # unexpected failure while measuring


class MeasurementType(StrEnum):
    AREA = "area"
    LENGTH = "length"
    NONE = "none"


class MeasurementUnit(StrEnum):
    SQUARE_METERS = "square_meters"
    METERS = "meters"
    NONE = "none"


class AdvancedGISMetrics(BaseModel):
    geodesic_value: float | None = Field(
        None, description="Same measurement computed on the WGS-84 ellipsoid (verification value)."
    )
    distortion_percentage: float | None = Field(
        None, description="|planar - geodesic| / geodesic * 100. Large values flag a poor projection."
    )
    perimeter_meters: float | None = Field(None, description="Polygon perimeter in metres.")
    vertex_count: int | None = Field(None, description="Number of coordinates in the geometry.")


class MeasurementDetail(BaseModel):
    measurement_type: MeasurementType = Field(..., description="'area', 'length' or 'none'.")
    value: float | None = Field(None, description="Measured value in m² (area) or m (length).")
    unit: MeasurementUnit = MeasurementUnit.NONE
    formatted: str | None = Field(None, description="Human readable value.")
    alternative_units: dict[str, float] = Field(default_factory=dict)
    advanced_metrics: AdvancedGISMetrics | None = None


class FeatureMeasurement(BaseModel):
    feature_id: str = Field(..., description="Identifier from the source file, or the index.")
    index: int = Field(..., description="Zero-based position of the feature in the file.")
    geometry_type: str = Field(..., description="GeoJSON geometry type, e.g. Polygon.")
    geometry: dict[str, Any] | None = Field(None, description="GeoJSON geometry in the source CRS.")
    geometry_wgs84: dict[str, Any] | None = Field(
        None,
        description="GeoJSON geometry in lon/lat (WGS-84) for maps and exports. "
        "Only present when the source CRS is not already WGS-84.",
    )
    crs: str = Field(..., description="CRS of the source coordinates, e.g. EPSG:4326.")
    projected_crs: str | None = Field(None, description="Metric CRS used for the planar calculation, e.g. EPSG:32643.")
    properties: dict[str, Any] = Field(default_factory=dict)
    measurement: MeasurementDetail | None = None
    status: FeatureStatus = FeatureStatus.SUCCESS
    notes: list[str] = Field(default_factory=list)


class FileInfoResponse(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str = Field(..., description="CRS of the file (or MIXED if layers differ).")
    status: FileStatus
    progress_pct: int = Field(0, ge=0, le=100)
    file_type: str | None = None
    file_size_bytes: int | None = None
    file_hash_sha256: str | None = None
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list, description="Non-fatal findings, e.g. an assumed CRS.")
    uploaded_at: datetime | None = None


class MeasurementsSummary(BaseModel):
    total_features: int = 0
    polygon_count: int = 0
    linestring_count: int = 0
    point_count: int = 0
    unsupported_count: int = 0
    failed_count: int = Field(0, description="Features that could not be measured.")
    total_area_sq_meters: float = 0.0
    total_area_hectares: float = 0.0
    total_length_meters: float = 0.0
    total_length_kilometers: float = 0.0
    average_polygon_area_sq_m: float | None = None
    average_linestring_length_m: float | None = None


class MeasurementsResponse(BaseModel):
    id: str
    filename: str
    crs: str
    summary: MeasurementsSummary = Field(
        ..., description="Aggregates over the whole file, regardless of filters/pagination."
    )
    features: list[FeatureMeasurement]
    total_count: int = Field(..., description="Features matching the filters.")
    limit: int
    offset: int
    has_more: bool


class ErrorResponse(BaseModel):
    detail: str
    error_code: str
    file_id: str | None = None
