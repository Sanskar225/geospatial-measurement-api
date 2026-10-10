"""Persistence layer: the only module that talks SQL.

Services depend on this class instead of on SQLAlchemy directly, which keeps
business logic testable and makes a later switch (e.g. to PostGIS) a local change.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import GeospatialFeatureModel, GeospatialFileModel
from app.models.schemas import (
    FeatureMeasurement,
    FeatureStatus,
    FileInfoResponse,
    FileStatus,
    MeasurementDetail,
    MeasurementsSummary,
)

INSERT_BATCH_SIZE = 2000


def _aware(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; everything we store is UTC, so put it back."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def file_to_info(model: GeospatialFileModel) -> FileInfoResponse:
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
        warnings=model.warnings or [],
        uploaded_at=_aware(model.created_at),
    )


def _feature_to_row(file_id: str, feature: FeatureMeasurement) -> dict:
    measurement = feature.measurement
    return {
        "file_id": file_id,
        "feature_index": feature.index,
        "feature_identifier": feature.feature_id[:255],
        "geometry_type": feature.geometry_type,
        "geometry": feature.geometry,
        "geometry_wgs84": feature.geometry_wgs84,
        "properties": feature.properties,
        "source_crs": feature.crs,
        "projected_crs": feature.projected_crs,
        "status": feature.status.value,
        "notes": feature.notes,
        "measurement_type": measurement.measurement_type.value if measurement else "none",
        "measurement_value": measurement.value if measurement else None,
        "measurement": measurement.model_dump(mode="json") if measurement else None,
    }


def _row_to_feature(row: GeospatialFeatureModel) -> FeatureMeasurement:
    return FeatureMeasurement(
        feature_id=row.feature_identifier,
        index=row.feature_index,
        geometry_type=row.geometry_type,
        geometry=row.geometry,
        geometry_wgs84=row.geometry_wgs84,
        crs=row.source_crs,
        projected_crs=row.projected_crs,
        properties=row.properties or {},
        measurement=MeasurementDetail.model_validate(row.measurement) if row.measurement else None,
        status=FeatureStatus(row.status),
        notes=row.notes or [],
    )


class FileRepository:
    """Every public method commits its own unit of work."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ files
    async def create(
        self, *, file_id: str, filename: str, file_type: str, size: int, sha256: str
    ) -> GeospatialFileModel:
        model = GeospatialFileModel(
            id=file_id,
            filename=filename,
            file_type=file_type,
            file_size_bytes=size,
            file_hash_sha256=sha256,
            status=FileStatus.PENDING.value,
            progress_pct=0,
            warnings=[],
        )
        self._session.add(model)
        await self._session.commit()
        return model

    async def get(self, file_id: str) -> GeospatialFileModel | None:
        return await self._session.get(GeospatialFileModel, file_id)

    async def find_completed_by_hash(self, sha256: str) -> GeospatialFileModel | None:
        stmt = (
            select(GeospatialFileModel)
            .where(
                GeospatialFileModel.file_hash_sha256 == sha256,
                GeospatialFileModel.status == FileStatus.COMPLETED.value,
            )
            .order_by(GeospatialFileModel.created_at)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def update_progress(self, file_id: str, progress: int, status: FileStatus) -> None:
        model = await self.get(file_id)
        if model is not None:
            model.progress_pct = progress
            model.status = status.value
            await self._session.commit()

    async def mark_failed(self, file_id: str, message: str) -> None:
        model = await self.get(file_id)
        if model is not None:
            model.status = FileStatus.FAILED.value
            model.error_message = message[:2000]
            await self._session.commit()

    async def fail_unfinished(self, message: str) -> int:
        """Mark every PENDING/PROCESSING file as FAILED. Returns how many were touched."""
        result = await self._session.execute(
            update(GeospatialFileModel)
            .where(GeospatialFileModel.status.in_([FileStatus.PENDING.value, FileStatus.PROCESSING.value]))
            .values(status=FileStatus.FAILED.value, error_message=message)
        )
        await self._session.commit()
        return result.rowcount

    async def complete(
        self,
        file_id: str,
        *,
        crs: str,
        features: Sequence[FeatureMeasurement],
        summary: MeasurementsSummary,
        warnings: list[str],
    ) -> None:
        """Persist all features and flip the file to COMPLETED in one transaction."""
        rows = [_feature_to_row(file_id, f) for f in features]
        for start in range(0, len(rows), INSERT_BATCH_SIZE):
            await self._session.execute(insert(GeospatialFeatureModel), rows[start : start + INSERT_BATCH_SIZE])
        model = await self.get(file_id)
        if model is None:
            await self._session.rollback()
            return
        model.crs = crs
        model.feature_count = len(rows)
        model.summary = summary.model_dump(mode="json")
        model.warnings = warnings
        model.status = FileStatus.COMPLETED.value
        model.progress_pct = 100
        model.error_message = None
        await self._session.commit()

    async def list_files(self, limit: int, offset: int) -> tuple[list[GeospatialFileModel], int]:
        total = (await self._session.execute(select(func.count(GeospatialFileModel.id)))).scalar_one()
        stmt = (
            select(GeospatialFileModel)
            .order_by(GeospatialFileModel.created_at.desc(), GeospatialFileModel.id)
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars()), total

    async def delete(self, file_id: str) -> bool:
        # Explicit feature delete keeps this correct even where FK cascades are not enforced.
        await self._session.execute(delete(GeospatialFeatureModel).where(GeospatialFeatureModel.file_id == file_id))
        result = await self._session.execute(delete(GeospatialFileModel).where(GeospatialFileModel.id == file_id))
        await self._session.commit()
        return result.rowcount > 0

    # ------------------------------------------------------------------ features
    async def query_features(
        self,
        file_id: str,
        *,
        geometry_type: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[FeatureMeasurement], int]:
        conditions = [GeospatialFeatureModel.file_id == file_id]
        if geometry_type:
            conditions.append(GeospatialFeatureModel.geometry_type == geometry_type)
        if status:
            conditions.append(GeospatialFeatureModel.status == status)

        total = (
            await self._session.execute(select(func.count(GeospatialFeatureModel.id)).where(*conditions))
        ).scalar_one()
        stmt = (
            select(GeospatialFeatureModel)
            .where(*conditions)
            .order_by(GeospatialFeatureModel.feature_index)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars()
        return [_row_to_feature(row) for row in rows], total

    async def iter_features(self, file_id: str, batch_size: int = 1000) -> AsyncIterator[list[FeatureMeasurement]]:
        """Keyset-paginated batches (``id > last``): constant memory and no OFFSET scans."""
        last_id = 0
        while True:
            stmt = (
                select(GeospatialFeatureModel)
                .where(GeospatialFeatureModel.file_id == file_id, GeospatialFeatureModel.id > last_id)
                .order_by(GeospatialFeatureModel.id)
                .limit(batch_size)
            )
            rows = list((await self._session.execute(stmt)).scalars())
            if not rows:
                return
            last_id = rows[-1].id
            yield [_row_to_feature(row) for row in rows]
