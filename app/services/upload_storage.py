"""Persisting uploaded files safely.

* The client-supplied filename is **never** used to build a path: files are stored as
  ``<uuid><extension>``. The sanitised original name is kept only for display.
* The upload is streamed in chunks; size is enforced while streaming and the SHA-256 is
  computed in the same single pass (used for de-duplication).
* A cheap content signature check rejects e.g. a PNG renamed to ``.kml`` before parsing.
"""

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import FileFormatError, FileTooLargeError

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
_ZIP_MAGIC = (b"PK\x03\x04",)
_XML_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")


@dataclass(frozen=True)
class SavedUpload:
    path: Path
    size: int
    sha256: str


def sanitize_filename(raw: str | None) -> str:
    """Keep only the final path component, strip control characters, cap at 255 chars."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if len(name) > 255:
        suffix = Path(name).suffix[:20]
        name = name[: 255 - len(suffix)] + suffix
    return name or "unnamed"


def validate_signature(path: Path, suffix: str) -> None:
    """Raise FileFormatError when the first bytes cannot belong to the declared type."""
    with path.open("rb") as handle:
        head = handle.read(512)
    if suffix in (".zip", ".kmz"):
        if not head.startswith(_ZIP_MAGIC):
            raise FileFormatError(f"The file is not a valid zip archive (expected a {suffix} file).")
    elif suffix == ".kml":
        stripped = head
        for bom in _XML_BOMS:
            if stripped.startswith(bom):
                stripped = stripped[len(bom) :]
                break
        else:
            stripped = stripped.lstrip()
        # UTF-16 XML starts with a BOM and has NUL bytes between characters; plain XML starts with '<'.
        if not (stripped.startswith(b"<") or head.startswith(_XML_BOMS[1:])):
            raise FileFormatError("The file does not look like XML, so it cannot be a valid KML.")


class UploadStorage:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        directory = self._directory or settings.UPLOAD_DIR
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def path_for(self, file_id: str, suffix: str) -> Path:
        return self.directory / f"{file_id}{suffix}"

    async def save(self, upload: UploadFile, file_id: str, suffix: str) -> SavedUpload:
        limit = settings.max_upload_bytes
        path = self.path_for(file_id, suffix)
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("wb") as out:
                while chunk := await upload.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > limit:
                        raise FileTooLargeError(
                            f"File exceeds the maximum upload size of {settings.MAX_UPLOAD_SIZE_MB:g} MB."
                        )
                    # hashing + disk write off the event loop
                    await asyncio.to_thread(self._write, out, digest, chunk)
            if size == 0:
                raise FileFormatError("The uploaded file is empty.")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return SavedUpload(path=path, size=size, sha256=digest.hexdigest())

    @staticmethod
    def _write(out, digest, chunk: bytes) -> None:
        digest.update(chunk)
        out.write(chunk)

    def delete(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            logger.warning("Could not delete %s", path, exc_info=True)
