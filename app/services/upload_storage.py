import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import FileFormatError, FileTooLargeError

CHUNK_SIZE = 1024 * 1024  # 1MB
_ZIP_MAGIC = (b"PK\x03\x04",)
_XML_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")

@dataclass(frozen=True)
class SavedUpload:
    path: Path
    size: int
    sha256: str

def sanitize_filename(raw: str | None) -> str:
    """Extracts basename, strips directory traversal or control characters, caps at 255 chars."""
    if not raw:
        return "upload.bin"
    # Take basename
    name = Path(raw).name
    # Strip dangerous characters
    cleaned = re.sub(r'[\r\n\t\x00-\x1f\\/:*?"<>|]', '_', name).strip()
    return cleaned[:255] or "upload.bin"

def validate_content_signature(file_path: Path, ext: str) -> None:
    """Verifies magic bytes and file signatures before expensive processing."""
    with open(file_path, "rb") as f:
        header = f.read(512)
        if len(header) == 0:
            raise FileFormatError("Uploaded file is empty.")

        if ext in (".zip", ".kmz"):
            if not header.startswith(_ZIP_MAGIC):
                raise FileFormatError(f"File header mismatch: not a valid ZIP/KMZ archive for '{ext}'.")
        elif ext == ".kml":
            # Strip BOM if present
            content = header
            for bom in _XML_BOMS:
                if content.startswith(bom):
                    content = content[len(bom):]
                    break
            
            preview = content.decode("utf-8", errors="ignore").lower().strip()
            if not (preview.startswith("<?xml") or preview.startswith("<kml") or "<kml" in preview or "<document" in preview):
                raise FileFormatError("File header mismatch: not a valid XML/KML geospatial file.")

async def save_upload_stream(upload: UploadFile, target_dir: Path, file_id: str) -> SavedUpload:
    """
    Streams upload to disk safely in a single pass while calculating SHA-256
    and enforcing max file size limits.
    """
    raw_filename = upload.filename or "unknown"
    safe_name = sanitize_filename(raw_filename)
    ext = Path(safe_name).suffix.lower()
    
    # Store with unique file_id prefix to prevent name collisions
    dest_path = target_dir / f"{file_id}{ext}"
    max_bytes = int(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024)

    sha256 = hashlib.sha256()
    total_bytes = 0

    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise FileTooLargeError(
                        f"File size exceeds maximum allowed limit of {settings.MAX_UPLOAD_SIZE_MB}MB."
                    )
                sha256.update(chunk)
                out.write(chunk)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    # Content signature validation
    try:
        validate_content_signature(dest_path, ext)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise

    return SavedUpload(path=dest_path, size=total_bytes, sha256=sha256.hexdigest())
