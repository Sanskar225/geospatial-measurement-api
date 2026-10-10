"""SQLAlchemy ORM models.

Only portable column types are used (String / Integer / Float / JSON / DateTime), so the
same schema works on SQLite (default) and PostgreSQL. Geometries are stored as GeoJSON;
moving to PostGIS ``geometry`` columns is listed under future scope in the README.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GeospatialFileModel(Base):
    __tablename__ = "geospatial_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(50))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_hash_sha256: Mapped[str] = mapped_column(String(64), index=True)
    crs: Mapped[str] = mapped_column(String(255), default="EPSG:4326")
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    features: Mapped[list["GeospatialFeatureModel"]] = relationship(
        back_populates="file_record", cascade="all, delete-orphan", passive_deletes=True
    )


class GeospatialFeatureModel(Base):
    __tablename__ = "geospatial_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("geospatial_files.id", ondelete="CASCADE"), index=True)
    feature_index: Mapped[int] = mapped_column(Integer)
    feature_identifier: Mapped[str] = mapped_column(String(255))
    geometry_type: Mapped[str] = mapped_column(String(50))
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    geometry_wgs84: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_crs: Mapped[str] = mapped_column(String(255))
    projected_crs: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="SUCCESS")
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Scalar copies of the headline numbers so they stay filterable / sortable in SQL ...
    measurement_type: Mapped[str] = mapped_column(String(10), default="none")
    measurement_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ... and the full MeasurementDetail payload, round-tripped without field-by-field mapping.
    measurement: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    file_record: Mapped[GeospatialFileModel] = relationship(back_populates="features")

    __table_args__ = (
        UniqueConstraint("file_id", "feature_index", name="uq_feature_file_index"),
        Index("idx_feature_file_geom", "file_id", "geometry_type"),
        Index("idx_feature_file_status", "file_id", "status"),
    )
