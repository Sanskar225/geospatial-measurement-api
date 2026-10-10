"""Use-case layer: orchestrates the whole upload -> parse -> measure -> persist flow.

    register_upload()   stream to disk, validate, de-duplicate, create a PENDING record
    process()           parse -> measure -> persist, with status/progress updates
    get_* / stream_*    read side used by the API endpoints

Parsing and measuring are CPU-bound, so they run in a worker thread
(``asyncio.to_thread``) to keep the event loop responsive for other requests.
Each repository call uses a short-lived session: no transaction is held open while
CPU work runs, and ``process`` can run unchanged inside a background task.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.database import SessionFactory
from app.core.exceptions import (
    AppError,
    FileNotReadyError,
    FileProcessingFailedError,
    FileRecordNotFoundError,
    ProcessingError,
)
from app.models.db_models import GeospatialFileModel
from app.models.schemas import (
    FeatureMeasurement,
    FeatureStatus,
    FileInfoResponse,
    FileStatus,
    GeometryType,
    MeasurementsResponse,
    MeasurementsSummary,
)
from app.repositories.file_repository import FileRepository, file_to_info
from app.services import export_service
from app.services.measurement_service import MeasurementService, measurement_service
from app.services.parsers import ParserLimits, ParserRegistry, build_registry
from app.services.parsers.base import ParsedDataset
from app.services.upload_storage import UploadStorage, sanitize_filename, validate_signature

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadResult:
    info: FileInfoResponse
    created: bool  # False when an identical, already processed file was returned instead


@dataclass(frozen=True)
class _Processed:
    crs: str
    features: list[FeatureMeasurement]
    summary: MeasurementsSummary
    warnings: list[str]


class FileService:
    def __init__(
        self,
        session_factory: async_sessionmaker = SessionFactory,
        storage: UploadStorage | None = None,
        registry: ParserRegistry | None = None,
        measurement: MeasurementService | None = None,
        config: Settings = default_settings,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage or UploadStorage()
        self._config = config
        self._registry = registry or build_registry(
            ParserLimits(
                max_features=config.MAX_FEATURES,
                max_uncompressed_bytes=config.max_uncompressed_bytes,
                max_zip_entries=config.MAX_ZIP_ENTRIES,
            )
        )
        self._measurement = measurement or measurement_service

    @asynccontextmanager
    async def _repo(self) -> AsyncIterator[FileRepository]:
        async with self._session_factory() as session:
            yield FileRepository(session)

    # ------------------------------------------------------------------ write side
    async def register_upload(self, upload: UploadFile) -> UploadResult:
        filename = sanitize_filename(upload.filename)
        suffix = Path(filename).suffix.lower()
        file_type = self._registry.file_type_for(suffix)  # 415 before touching the disk

        file_id = uuid.uuid4().hex
        saved = await self._storage.save(upload, file_id, suffix)
        try:
            validate_signature(saved.path, suffix)
            async with self._repo() as repo:
                if self._config.DEDUPLICATE_UPLOADS:
                    existing = await repo.find_completed_by_hash(saved.sha256)
                    if existing is not None:
                        self._storage.delete(saved.path)
                        return UploadResult(info=file_to_info(existing), created=False)
                record = await repo.create(
                    file_id=file_id,
                    filename=filename,
                    file_type=file_type,
                    size=saved.size,
                    sha256=saved.sha256,
                )
                return UploadResult(info=file_to_info(record), created=True)
        except BaseException:
            self._storage.delete(saved.path)
            raise

    async def process(self, file_id: str, *, raise_errors: bool = True) -> None:
        """Run the pipeline for a registered file.

        ``raise_errors=False`` is used by background tasks: there is no caller to
        receive an exception, so the outcome is recorded on the file (FAILED + message).
        """
        async with self._repo() as repo:
            record = await repo.get(file_id)
            if record is None:
                raise FileRecordNotFoundError(file_id)
            suffix = Path(record.filename).suffix.lower()
        path = self._storage.path_for(file_id, suffix)

        try:
            async with self._repo() as repo:
                await repo.update_progress(file_id, 10, FileStatus.PROCESSING)
            parser = self._registry.for_extension(suffix)

            dataset = await asyncio.to_thread(parser.parse, path)
            async with self._repo() as repo:
                await repo.update_progress(file_id, 40, FileStatus.PROCESSING)

            processed = await asyncio.to_thread(self._measure_dataset, dataset)
            async with self._repo() as repo:
                await repo.update_progress(file_id, 80, FileStatus.PROCESSING)
                await repo.complete(
                    file_id,
                    crs=processed.crs,
                    features=processed.features,
                    summary=processed.summary,
                    warnings=processed.warnings,
                )
            if not self._config.KEEP_UPLOADED_FILES:
                self._storage.delete(path)
            logger.info("Processed %s: %d features", file_id, len(processed.features))
        except asyncio.CancelledError:
            # e.g. the client disconnected during a synchronous upload: don't leave the file "PROCESSING".
            await asyncio.shield(self._fail(file_id, "Processing was cancelled before it finished.", path))
            raise
        except AppError as exc:
            await self._fail(file_id, exc.message, path)
            if raise_errors:
                exc.extra.setdefault("file_id", file_id)
                raise
        except Exception as exc:
            logger.exception("Unexpected failure while processing %s", file_id)
            await self._fail(file_id, "Unexpected error while processing the file.", path)
            if raise_errors:
                raise ProcessingError(file_id=file_id) from exc

    def _measure_dataset(self, dataset: ParsedDataset) -> _Processed:
        features = [self._measurement.measure(f) for f in dataset.features]
        summary = self._measurement.summarize(features)
        warnings = list(dataset.warnings)
        if summary.failed_count or summary.unsupported_count:
            warnings.append(
                f"{summary.failed_count} feature(s) could not be measured and "
                f"{summary.unsupported_count} have an unsupported geometry type; "
                "see each feature's status and notes."
            )
        return _Processed(crs=dataset.crs, features=features, summary=summary, warnings=warnings)

    async def _fail(self, file_id: str, message: str, path: Path) -> None:
        try:
            async with self._repo() as repo:
                await repo.mark_failed(file_id, message)
        except Exception:  # never mask the original error
            logger.exception("Could not record failure for %s", file_id)
        self._storage.delete(path)

    async def recover_interrupted(self) -> int:
        """Fail files left PENDING/PROCESSING by a previous run (restart, crash).

        Background tasks live in process memory, so after a restart nobody is working on those
        files any more. Assumes a single API process; with several replicas a shared job queue
        (see README, future scope) replaces this.
        """
        async with self._repo() as repo:
            count = await repo.fail_unfinished("Processing was interrupted by a server restart; please upload again.")
        if count:
            logger.warning("Marked %d interrupted file(s) as FAILED", count)
        return count

    async def delete(self, file_id: str) -> None:
        async with self._repo() as repo:
            record = await repo.get(file_id)
            if record is None:
                raise FileRecordNotFoundError(file_id)
            suffix = Path(record.filename).suffix.lower()
            await repo.delete(file_id)
        self._storage.delete(self._storage.path_for(file_id, suffix))

    # ------------------------------------------------------------------ read side
    async def get_info(self, file_id: str) -> FileInfoResponse:
        async with self._repo() as repo:
            return file_to_info(await self._get_or_404(repo, file_id))

    async def list_files(self, limit: int, offset: int) -> tuple[list[FileInfoResponse], int]:
        async with self._repo() as repo:
            rows, total = await repo.list_files(limit, offset)
            return [file_to_info(r) for r in rows], total

    async def get_measurements(
        self,
        file_id: str,
        *,
        geometry_type: GeometryType | None,
        status: FeatureStatus | None,
        limit: int,
        offset: int,
    ) -> MeasurementsResponse:
        async with self._repo() as repo:
            record = await self._get_completed(repo, file_id)
            features, total = await repo.query_features(
                file_id,
                geometry_type=geometry_type.value if geometry_type else None,
                status=status.value if status else None,
                limit=limit,
                offset=offset,
            )
            return MeasurementsResponse(
                id=record.id,
                filename=record.filename,
                crs=record.crs,
                summary=MeasurementsSummary.model_validate(record.summary or {}),
                features=features,
                total_count=total,
                limit=limit,
                offset=offset,
                has_more=offset + len(features) < total,
            )

    async def ensure_exportable(self, file_id: str) -> FileInfoResponse:
        async with self._repo() as repo:
            return file_to_info(await self._get_completed(repo, file_id))

    async def stream_export(self, file_id: str, fmt: str) -> AsyncIterator[str]:
        """Stream every feature in constant memory (own session, keyset pagination)."""
        async with self._repo() as repo:
            batches = repo.iter_features(file_id)
            formatter = export_service.stream_csv if fmt == "csv" else export_service.stream_geojson
            async for chunk in formatter(batches):
                yield chunk

    # ------------------------------------------------------------------ helpers
    @staticmethod
    async def _get_or_404(repo: FileRepository, file_id: str) -> GeospatialFileModel:
        record = await repo.get(file_id)
        if record is None:
            raise FileRecordNotFoundError(file_id)
        return record

    async def _get_completed(self, repo: FileRepository, file_id: str) -> GeospatialFileModel:
        record = await self._get_or_404(repo, file_id)
        if record.status in (FileStatus.PENDING.value, FileStatus.PROCESSING.value):
            raise FileNotReadyError(file_id, record.status)
        if record.status == FileStatus.FAILED.value:
            raise FileProcessingFailedError(file_id, record.error_message)
        return record


file_service = FileService()
