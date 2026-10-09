import json
import datetime
from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Text,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from app.core.database import Base

class GeospatialFileModel(Base):
    __tablename__ = "geospatial_files"

    id = Column(String(36), primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)
    file_size_bytes = Column(Integer, nullable=False, default=0)
    file_hash_sha256 = Column(String(64), nullable=False, index=True)
    crs = Column(String(100), nullable=False, default="EPSG:4326")
    feature_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="PENDING", index=True)
    progress_pct = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    summary_json = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True, default="[]")
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc), nullable=False)

    features = relationship(
        "GeospatialFeatureModel",
        back_populates="file_record",
        cascade="all, delete-orphan",
        order_by="GeospatialFeatureModel.feature_index"
    )

    def get_summary(self) -> dict:
        if self.summary_json:
            try:
                return json.loads(self.summary_json)
            except Exception:
                pass
        return {}

    def get_warnings(self) -> list:
        if self.warnings_json:
            try:
                return json.loads(self.warnings_json)
            except Exception:
                pass
        return []


class GeospatialFeatureModel(Base):
    __tablename__ = "geospatial_features"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(36), ForeignKey("geospatial_files.id", ondelete="CASCADE"), nullable=False, index=True)
    feature_index = Column(Integer, nullable=False)
    feature_identifier = Column(String(255), nullable=False)
    geometry_type = Column(String(50), nullable=False, index=True)
    geometry_geojson = Column(Text, nullable=False)
    geometry_wgs84_geojson = Column(Text, nullable=True)
    properties_json = Column(Text, nullable=False, default="{}")
    source_crs = Column(String(100), nullable=False)
    projected_crs = Column(String(100), nullable=True)
    
    # Measurements
    measurement_type = Column(String(20), nullable=False, default="none")
    measurement_value = Column(Float, nullable=True)
    measurement_unit = Column(String(30), nullable=False, default="none")
    alternative_units_json = Column(Text, nullable=True)
    
    # Advanced GIS Metrics
    geodesic_value = Column(Float, nullable=True)
    distortion_percentage = Column(Float, nullable=True)
    
    status = Column(String(30), nullable=False, default="SUCCESS")
    notes = Column(Text, nullable=True)

    file_record = relationship("GeospatialFileModel", back_populates="features")

    __table_args__ = (
        Index("idx_file_geom", "file_id", "geometry_type"),
    )
