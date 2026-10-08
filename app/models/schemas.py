from enum import Enum
from typing import Dict, Any, List, Optional, Union
from pydantic import BaseModel, Field

class FileStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class GeometryType(str, Enum):
    POLYGON = "Polygon"
    MULTI_POLYGON = "MultiPolygon"
    LINE_STRING = "LineString"
    MULTI_LINE_STRING = "MultiLineString"
    POINT = "Point"
    MULTI_POINT = "MultiPoint"
    GEOMETRY_COLLECTION = "GeometryCollection"
    UNKNOWN = "Unknown"

class MeasurementUnit(str, Enum):
    SQUARE_METERS = "square_meters"
    METERS = "meters"
    NONE = "none"

class MeasurementDetail(BaseModel):
    measurement_type: str = Field(..., description="'area' for Polygons, 'length' for LineStrings, 'none' for Points/Unsupported")
    value: Optional[float] = Field(None, description="Primary measurement value in standard SI units (m² or m)")
    unit: MeasurementUnit = Field(MeasurementUnit.NONE, description="Standard SI unit")
    formatted: Optional[str] = Field(None, description="Human-readable formatted string with multiple scales")
    alternative_units: Dict[str, float] = Field(default_factory=dict, description="Pre-computed units like hectares, km², km")

class FeatureMeasurement(BaseModel):
    feature_id: Union[str, int] = Field(..., description="Unique identifier or index of the feature")
    geometry_type: str = Field(..., description="Type of geometry (e.g. Polygon, LineString, Point)")
    geometry: Dict[str, Any] = Field(..., description="GeoJSON geometry representation")
    crs: str = Field(..., description="Original input CRS of the feature")
    projected_crs: Optional[str] = Field(None, description="Projected CRS applied for planar metric calculations")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Attribute table properties associated with feature")
    measurement: Optional[MeasurementDetail] = Field(None, description="Calculated geometric measurements")
    status: str = Field("SUCCESS", description="'SUCCESS', 'UNSUPPORTED_GEOMETRY', or 'CALCULATION_ERROR'")
    notes: Optional[str] = Field(None, description="Contextual note or explanation for the feature")

class FileInfoResponse(BaseModel):
    id: str = Field(..., description="Unique identifier assigned to the uploaded file")
    filename: str = Field(..., description="Original uploaded filename")
    feature_count: int = Field(..., description="Total number of geospatial features extracted")
    crs: str = Field(..., description="Detected Coordinate Reference System (e.g. EPSG:4326)")
    status: FileStatus = Field(..., description="Processing status of the file")
    file_type: Optional[str] = Field(None, description="Detected format (Shapefile (.zip) or KML)")
    uploaded_at: Optional[str] = Field(None, description="ISO timestamp of upload")

class MeasurementsSummary(BaseModel):
    total_features: int = 0
    polygon_count: int = 0
    linestring_count: int = 0
    point_count: int = 0
    unsupported_count: int = 0
    total_area_sq_meters: float = 0.0
    total_area_hectares: float = 0.0
    total_length_meters: float = 0.0
    total_length_kilometers: float = 0.0

class MeasurementsResponse(BaseModel):
    id: str
    filename: str
    crs: str
    summary: MeasurementsSummary
    features: List[FeatureMeasurement]
