import json
import uuid
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, List
from sqlalchemy import select, func, and_, update

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.db_models import GeospatialFileModel, GeospatialFeatureModel
from app.models.schemas import (
    FileInfoResponse,
    MeasurementsResponse,
    MeasurementsSummary,
    FeatureMeasurement,
    MeasurementDetail,
    MeasurementUnit,
    AdvancedGISMetrics,
    FileStatus,
)
from app.services.export_service import format_measurements_as_csv, format_features_as_geojson

class StorageService:
    """
    Enterprise Storage and Persistence Layer using SQLAlchemy Async ORM.
    Handles SHA-256 deduplication, query filtering, pagination, crash recovery, and export formatters.
    """

    @staticmethod
    def generate_id() -> str:
        return uuid.uuid4().hex[:8]

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(65536), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    async def fail_unfinished_records(self) -> int:
        """Startup recovery: Marks interrupted jobs as FAILED on server reboot."""
        async with AsyncSessionLocal() as session:
            stmt = (
                update(GeospatialFileModel)
                .where(GeospatialFileModel.status.in_(["PENDING", "PROCESSING"]))
                .values(
                    status="FAILED",
                    progress_pct=100,
                    error_message="Job was interrupted by application shutdown or server restart."
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def find_existing_by_hash(self, file_hash: str) -> Optional[FileInfoResponse]:
        async with AsyncSessionLocal() as session:
            stmt = select(GeospatialFileModel).where(
                and_(
                    GeospatialFileModel.file_hash_sha256 == file_hash,
                    GeospatialFileModel.status == "COMPLETED"
                )
            ).order_by(GeospatialFileModel.created_at.desc()).limit(1)
            result = await session.execute(stmt)
            file_rec = result.scalars().first()
            if file_rec:
                return self._to_file_info_response(file_rec)
        return None

    def _to_file_info_response(self, model: GeospatialFileModel) -> FileInfoResponse:
        return FileInfoResponse(
            id=model.id,
            filename=model.filename,
            feature_count=model.feature_count,
            crs=model.crs,
            status=FileStatus(model.status),
            progress_pct=model.progress_pct,
            file_type=model.file_type,
            file_size_bytes=model.file_size_bytes,
            file_hash_sha256=model.file_hash_sha256,
            error_message=model.error_message,
            warnings=model.get_warnings(),
            uploaded_at=model.created_at.isoformat() if model.created_at else None,
        )

    async def create_file_record(
        self,
        file_id: str,
        filename: str,
        file_type: str,
        file_size_bytes: int,
        file_hash: str,
        crs: str = "EPSG:4326",
    ) -> FileInfoResponse:
        async with AsyncSessionLocal() as session:
            file_model = GeospatialFileModel(
                id=file_id,
                filename=filename,
                file_type=file_type,
                file_size_bytes=file_size_bytes,
                file_hash_sha256=file_hash,
                crs=crs,
                feature_count=0,
                status="PROCESSING",
                progress_pct=10,
                warnings_json="[]",
            )
            session.add(file_model)
            await session.commit()
            await session.refresh(file_model)
            return self._to_file_info_response(file_model)

    async def update_progress(self, file_id: str, progress_pct: int, status: str = "PROCESSING"):
        async with AsyncSessionLocal() as session:
            stmt = select(GeospatialFileModel).where(GeospatialFileModel.id == file_id)
            result = await session.execute(stmt)
            rec = result.scalars().first()
            if rec:
                rec.progress_pct = progress_pct
                rec.status = status
                await session.commit()

    async def mark_file_failed(self, file_id: str, error_message: str):
        async with AsyncSessionLocal() as session:
            stmt = select(GeospatialFileModel).where(GeospatialFileModel.id == file_id)
            result = await session.execute(stmt)
            rec = result.scalars().first()
            if rec:
                rec.status = "FAILED"
                rec.progress_pct = 100
                rec.error_message = error_message
                await session.commit()

    async def complete_file_processing(
        self,
        file_id: str,
        crs: str,
        features: List[FeatureMeasurement],
        summary: MeasurementsSummary,
        warnings: Optional[List[str]] = None,
    ) -> FileInfoResponse:
        warnings = warnings or []
        async with AsyncSessionLocal() as session:
            stmt = select(GeospatialFileModel).where(GeospatialFileModel.id == file_id)
            result = await session.execute(stmt)
            file_rec = result.scalars().first()
            if not file_rec:
                raise ValueError(f"File record {file_id} not found")

            file_rec.crs = crs
            file_rec.feature_count = len(features)
            file_rec.status = "COMPLETED"
            file_rec.progress_pct = 100
            file_rec.summary_json = json.dumps(summary.model_dump())
            file_rec.warnings_json = json.dumps(warnings)

            db_features = []
            for idx, f in enumerate(features):
                adv = f.measurement.advanced_metrics if f.measurement else None
                db_feat = GeospatialFeatureModel(
                    file_id=file_id,
                    feature_index=idx + 1,
                    feature_identifier=str(f.feature_id),
                    geometry_type=f.geometry_type,
                    geometry_geojson=json.dumps(f.geometry),
                    geometry_wgs84_geojson=json.dumps(f.geometry_wgs84) if f.geometry_wgs84 else None,
                    properties_json=json.dumps(f.properties),
                    source_crs=f.crs,
                    projected_crs=f.projected_crs,
                    measurement_type=f.measurement.measurement_type if f.measurement else "none",
                    measurement_value=f.measurement.value if f.measurement else None,
                    measurement_unit=f.measurement.unit.value if f.measurement else "none",
                    alternative_units_json=json.dumps(f.measurement.alternative_units) if f.measurement else "{}",
                    geodesic_value=adv.geodesic_value if adv else None,
                    distortion_percentage=adv.distortion_percentage if adv else None,
                    status=f.status,
                    notes=f.notes,
                )
                db_features.append(db_feat)

            session.add_all(db_features)
            await session.commit()
            await session.refresh(file_rec)
            return self._to_file_info_response(file_rec)

    async def get_file_info(self, file_id: str) -> Optional[FileInfoResponse]:
        async with AsyncSessionLocal() as session:
            stmt = select(GeospatialFileModel).where(GeospatialFileModel.id == file_id)
            result = await session.execute(stmt)
            rec = result.scalars().first()
            if rec:
                return self._to_file_info_response(rec)
        return None

    async def get_measurements(
        self,
        file_id: str,
        geometry_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Optional[MeasurementsResponse]:
        async with AsyncSessionLocal() as session:
            stmt = select(GeospatialFileModel).where(GeospatialFileModel.id == file_id)
            result = await session.execute(stmt)
            file_rec = result.scalars().first()
            if not file_rec:
                return None

            summary_dict = file_rec.get_summary()
            summary = MeasurementsSummary(**summary_dict) if summary_dict else MeasurementsSummary()

            query = select(GeospatialFeatureModel).where(GeospatialFeatureModel.file_id == file_id)
            if geometry_type:
                query = query.where(GeospatialFeatureModel.geometry_type.ilike(f"%{geometry_type}%"))
            
            count_stmt = select(func.count(GeospatialFeatureModel.id)).where(GeospatialFeatureModel.file_id == file_id)
            if geometry_type:
                count_stmt = count_stmt.where(GeospatialFeatureModel.geometry_type.ilike(f"%{geometry_type}%"))
            total_matching = (await session.execute(count_stmt)).scalar() or 0

            query = query.order_by(GeospatialFeatureModel.feature_index).limit(limit).offset(offset)
            feat_results = (await session.execute(query)).scalars().all()

            features_list = []
            for f in feat_results:
                meas_detail = None
                if f.measurement_type != "none" or f.status == "SUCCESS":
                    alt_units = {}
                    if f.alternative_units_json:
                        try:
                            alt_units = json.loads(f.alternative_units_json)
                        except Exception:
                            pass
                    
                    adv = AdvancedGISMetrics(
                        geodesic_value=f.geodesic_value,
                        distortion_percentage=f.distortion_percentage,
                        perimeter_meters=alt_units.get("perimeter_meters"),
                    )

                    formatted = "N/A"
                    if f.measurement_type == "area" and f.measurement_value is not None:
                        ha = alt_units.get("hectares", round(f.measurement_value / 10000.0, 4))
                        formatted = f"{f.measurement_value:,.2f} m² ({ha:,.4f} ha)"
                    elif f.measurement_type == "length" and f.measurement_value is not None:
                        km = alt_units.get("kilometers", round(f.measurement_value / 1000.0, 4))
                        formatted = f"{f.measurement_value:,.2f} m ({km:,.4f} km)"
                    elif f.geometry_type in ("Point", "MultiPoint"):
                        formatted = "N/A (Points have zero dimension)"

                    meas_detail = MeasurementDetail(
                        measurement_type=f.measurement_type,
                        value=f.measurement_value,
                        unit=MeasurementUnit(f.measurement_unit) if f.measurement_unit in [u.value for u in MeasurementUnit] else MeasurementUnit.NONE,
                        formatted=formatted,
                        alternative_units=alt_units,
                        advanced_metrics=adv,
                    )

                wgs84_dict = None
                if f.geometry_wgs84_geojson:
                    try:
                        wgs84_dict = json.loads(f.geometry_wgs84_geojson)
                    except Exception:
                        pass

                features_list.append(
                    FeatureMeasurement(
                        feature_id=f.feature_identifier,
                        geometry_type=f.geometry_type,
                        geometry=json.loads(f.geometry_geojson),
                        geometry_wgs84=wgs84_dict,
                        crs=f.source_crs,
                        projected_crs=f.projected_crs,
                        properties=json.loads(f.properties_json) if f.properties_json else {},
                        measurement=meas_detail,
                        status=f.status,
                        notes=f.notes,
                    )
                )

            return MeasurementsResponse(
                id=file_rec.id,
                filename=file_rec.filename,
                crs=file_rec.crs,
                summary=summary,
                features=features_list,
                warnings=file_rec.get_warnings(),
                total_count=total_matching,
                page=(offset // limit) + 1,
                limit=limit,
            )

    async def list_all_files(self) -> List[FileInfoResponse]:
        async with AsyncSessionLocal() as session:
            stmt = select(GeospatialFileModel).order_by(GeospatialFileModel.created_at.desc())
            results = (await session.execute(stmt)).scalars().all()
            return [self._to_file_info_response(r) for r in results]

    async def export_geojson(self, file_id: str) -> Optional[Dict[str, Any]]:
        meas = await self.get_measurements(file_id, limit=10000, offset=0)
        if not meas:
            return None
        return format_features_as_geojson(meas)

    async def export_csv(self, file_id: str) -> Optional[str]:
        meas = await self.get_measurements(file_id, limit=10000, offset=0)
        if not meas:
            return None
        return format_measurements_as_csv(meas)

storage_service = StorageService()
