"""Application settings.

Every value can be overridden with an environment variable (or a ``.env`` file),
e.g. ``MAX_UPLOAD_SIZE_MB=100 DATABASE_URL=postgresql+asyncpg://... uvicorn app.main:app``.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=True)

    PROJECT_NAME: str = "Geospatial File Measurement API"
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api"

    # --- storage -----------------------------------------------------------
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    DATA_DIR: Path = BASE_DIR / "data_store"
    #: Full SQLAlchemy async URL. Defaults to a SQLite file inside DATA_DIR.
    DATABASE_URL: str | None = None
    #: Keep the original upload on disk after a successful run (useful for re-processing).
    KEEP_UPLOADED_FILES: bool = True
    #: Return the existing record when an identical file (SHA-256) was already processed.
    DEDUPLICATE_UPLOADS: bool = True

    # --- limits (defence against oversized / malicious uploads) ---------------
    MAX_UPLOAD_SIZE_MB: float = 50
    MAX_UNCOMPRESSED_SIZE_MB: float = 250
    MAX_ZIP_ENTRIES: int = 500
    MAX_FEATURES: int = 250_000

    # --- measurement -------------------------------------------------------
    #: Planar-vs-geodesic difference (in %) above which a feature gets a warning note.
    DISTORTION_WARNING_PCT: float = 0.5

    # --- http --------------------------------------------------------------
    #: JSON list when set through the environment, e.g. '["https://app.example.com"]'.
    CORS_ORIGINS: list[str] = ["*"]
    LOG_LEVEL: str = "INFO"

    @property
    def database_url(self) -> str:
        return self.DATABASE_URL or f"sqlite+aiosqlite:///{self.DATA_DIR / 'geospatial.db'}"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.MAX_UPLOAD_SIZE_MB * 1024 * 1024)

    @property
    def max_uncompressed_bytes(self) -> int:
        return int(self.MAX_UNCOMPRESSED_SIZE_MB * 1024 * 1024)

    def ensure_directories(self) -> None:
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
