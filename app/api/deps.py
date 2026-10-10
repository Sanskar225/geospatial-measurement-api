"""FastAPI dependencies (overridable in tests via ``app.dependency_overrides``)."""

from app.services.file_service import FileService, file_service


def get_file_service() -> FileService:
    return file_service
