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

class AdvancedGISMetrics(BaseModel):
    geodesic_value: Optional[float] = Field(None, description="Ellipsoidal calculation via WGS-84 ellipsoid")
    distortion_percentage: Optional[float] = Field(None, description="Scale variance between planar projected and geodesic")
    perimeter_meters: Optional[float] = Field(None, description="Boundary perimeter for polygons")
    vertex_count: Optional[int] = Field(None, description="Total number of coordinate vertices")

class MeasurementDetail(BaseModel):
    measurement_type: str = Field(..., description="'area' for Polygons, 'length' for LineStrings, 'none' for Points/Unsupported")
    value: Optional[float] = Field(None, description="Primary measurement value in standard SI units (m² or m)")
    unit: MeasurementUnit = Field(MeasurementUnit.NONE, description="Standard SI unit")
    formatted: Optional[str] = Field(None, description="Human-readable formatted string with multiple scales")
    alternative_units: Dict[str, float] = Field(default_factory=dict, description="Pre-computed units like hectares, km², km")
    advanced_metrics: Optional[AdvancedGISMetrics] = Field(None, description="Detailed GIS metrics (Geodesic, Perimeter, Vertices)")

class FeatureMeasurement(BaseModel):
    feature_id: Union[str, int] = Field(..., description="Unique identifier or index of the feature")
    geometry_type: str = Field(..., description="Type of geometry (e.g. Polygon, LineString, Point)")
    geometry: Dict[str, Any] = Field(..., description="GeoJSON geometry representation in source CRS")
    geometry_wgs84: Optional[Dict[str, Any]] = Field(None, description="Geometry reprojected to EPSG:4326 for map rendering and RFC 7946")
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
    progress_pct: int = Field(100, description="Processing completion percentage (0 to 100)")
    file_type: Optional[str] = Field(None, description="Detected format (Shapefile (.zip) or KML)")
    file_size_bytes: Optional[int] = Field(None, description="File size in bytes")
    file_hash_sha256: Optional[str] = Field(None, description="Cryptographic SHA-256 hash for deduplication")
    error_message: Optional[str] = Field(None, description="Error detail if status is FAILED")
    warnings: List[str] = Field(default_factory=list, description="Diagnostic warnings (e.g. assumed CRS, high distortion)")
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
    average_polygon_area_sq_m: Optional[float] = 0.0
    average_linestring_length_m: Optional[float] = 0.0

class MeasurementsResponse(BaseModel):
    id: str
    filename: str
    crs: str
    summary: MeasurementsSummary
    features: List[FeatureMeasurement]
    warnings: List[str] = Field(default_factory=list, description="Diagnostic warnings")
    total_count: Optional[int] = None
    page: Optional[int] = 1
    limit: Optional[int] = 100

class GeoJSONFeatureCollection(BaseModel):
    type: str = "FeatureCollection"
    features: List[Dict[str, Any]]
