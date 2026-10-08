import os
from pathlib import Path
from pydantic import BaseModel

class Settings(BaseModel):
    PROJECT_NAME: str = "Aereo Geospatial File Measurement API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api"
    
    # Base directories
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    DATA_DIR: Path = BASE_DIR / "data_store"
    
    # Security / file constraints
    MAX_UPLOAD_SIZE_MB: int = 50  # 50 MB limit
    ALLOWED_EXTENSIONS: set = {".zip", ".kml"}
    
    # CRS configuration defaults
    DEFAULT_GEOGRAPHIC_CRS: str = "EPSG:4326"

settings = Settings()

# Ensure storage directories exist
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
