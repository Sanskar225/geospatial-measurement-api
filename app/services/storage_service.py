import json
import uuid
import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from threading import Lock
from app.core.config import settings
from app.models.schemas import FileInfoResponse, MeasurementsResponse, FileStatus

class StorageService:
    """
    Persistence and caching service for uploaded geospatial files and their computed measurements.
    Uses JSON file-backed persistence + in-memory cache for fast, reliable O(1) lookups.
    """

    def __init__(self):
        self._lock = Lock()
        self._info_cache: Dict[str, Dict[str, Any]] = {}
        self._measurements_cache: Dict[str, Dict[str, Any]] = {}
        self._load_from_disk()

    def _get_info_file_path(self, file_id: str) -> Path:
        return settings.DATA_DIR / f"{file_id}_info.json"

    def _get_meas_file_path(self, file_id: str) -> Path:
        return settings.DATA_DIR / f"{file_id}_measurements.json"

    def _load_from_disk(self):
        with self._lock:
            for info_file in settings.DATA_DIR.glob("*_info.json"):
                try:
                    file_id = info_file.stem.replace("_info", "")
                    with open(info_file, "r", encoding="utf-8") as f:
                        self._info_cache[file_id] = json.load(f)
                    
                    meas_file = self._get_meas_file_path(file_id)
                    if meas_file.exists():
                        with open(meas_file, "r", encoding="utf-8") as f:
                            self._measurements_cache[file_id] = json.load(f)
                except Exception:
                    continue

    def generate_id(self) -> str:
        # Generate clean 8-character unique alphanumeric ID (e.g. "abc12345")
        return uuid.uuid4().hex[:8]

    def save_file_record(
        self,
        file_id: str,
        filename: str,
        crs: str,
        feature_count: int,
        status: FileStatus,
        file_type: str,
        measurements_response: Optional[MeasurementsResponse] = None
    ) -> FileInfoResponse:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        info_data = {
            "id": file_id,
            "filename": filename,
            "feature_count": feature_count,
            "crs": crs,
            "status": status.value,
            "file_type": file_type,
            "uploaded_at": now,
        }

        with self._lock:
            self._info_cache[file_id] = info_data
            with open(self._get_info_file_path(file_id), "w", encoding="utf-8") as f:
                json.dump(info_data, f, indent=2)

            if measurements_response:
                meas_data = measurements_response.model_dump()
                self._measurements_cache[file_id] = meas_data
                with open(self._get_meas_file_path(file_id), "w", encoding="utf-8") as f:
                    json.dump(meas_data, f, indent=2)

        return FileInfoResponse(**info_data)

    def get_file_info(self, file_id: str) -> Optional[FileInfoResponse]:
        with self._lock:
            data = self._info_cache.get(file_id)
            if data:
                return FileInfoResponse(**data)
            return None

    def get_measurements(self, file_id: str) -> Optional[MeasurementsResponse]:
        with self._lock:
            data = self._measurements_cache.get(file_id)
            if data:
                return MeasurementsResponse(**data)
            return None

    def list_all_files(self) -> List[FileInfoResponse]:
        with self._lock:
            return [FileInfoResponse(**data) for data in self._info_cache.values()]

storage_service = StorageService()
