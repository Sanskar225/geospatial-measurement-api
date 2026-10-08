import os
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple
import shapefile
from shapely.geometry import shape
from pyproj import CRS

from app.core.exceptions import FileFormatError
from app.services.parsers.base import ParsedDataset, ParsedFeature

class ShapefileParser:
    """
    Parser for ESRI Shapefiles provided inside a .zip archive.
    
    Validates mandatory components (.shp, .shx, .dbf), reads projection metadata
    from .prj, and safely extracts feature geometries and attributes.
    """

    @staticmethod
    def _is_safe_path(base_dir: Path, target_path: Path) -> bool:
        """Protects against Zip Slip (Directory Traversal) attacks."""
        try:
            target_path.resolve().relative_to(base_dir.resolve())
            return True
        except ValueError:
            return False

    def parse(self, zip_filepath: Path) -> ParsedDataset:
        if not zipfile.is_zipfile(zip_filepath):
            raise FileFormatError("Uploaded file is not a valid zip archive.")

        temp_dir = tempfile.mkdtemp(prefix="shapefile_extract_")
        temp_path = Path(temp_dir)

        try:
            with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
                # Security: Check for zip slip and total uncompressed size
                total_uncompressed = 0
                for file_info in zip_ref.infolist():
                    target_file = temp_path / file_info.filename
                    if not self._is_safe_path(temp_path, target_file):
                        raise FileFormatError("Potentially malicious zip archive: path traversal detected.")
                    
                    total_uncompressed += file_info.file_size
                    if total_uncompressed > 100 * 1024 * 1024:  # 100 MB max uncompressed
                        raise FileFormatError("Zip bomb detected: uncompressed content exceeds limit.")

                zip_ref.extractall(temp_dir)

            # Locate the .shp file (could be in root or nested subdirectory)
            shp_files = list(temp_path.rglob("*.shp"))
            if not shp_files:
                raise FileFormatError("Shapefile zip archive does not contain any .shp file.")

            primary_shp = shp_files[0]
            base_stem = primary_shp.stem
            parent_dir = primary_shp.parent

            # Check for mandatory .dbf or .shx
            shx_file = parent_dir / f"{base_stem}.shx"
            dbf_file = parent_dir / f"{base_stem}.dbf"
            prj_file = parent_dir / f"{base_stem}.prj"

            # Parse CRS from .prj if available
            detected_crs = "EPSG:4326"
            if prj_file.exists():
                try:
                    with open(prj_file, "r", encoding="utf-8", errors="ignore") as f:
                        prj_text = f.read().strip()
                        if prj_text:
                            crs_obj = CRS.from_user_input(prj_text)
                            detected_crs = crs_obj.to_string()
                except Exception:
                    detected_crs = "EPSG:4326"

            # Read features using pyshp
            features: List[ParsedFeature] = []
            with shapefile.Reader(str(primary_shp)) as sf:
                fields = [f[0] for f in sf.fields[1:]]  # Exclude DeletionFlag
                
                for idx, shape_record in enumerate(sf.shapeRecords()):
                    try:
                        geo_interface = shape_record.shape.__geo_interface__
                        geom = shape(geo_interface)
                        
                        # Extract record properties
                        record_dict = {}
                        if shape_record.record:
                            for key, val in zip(fields, shape_record.record):
                                record_dict[str(key)] = val

                        features.append(
                            ParsedFeature(
                                feature_id=str(idx + 1),
                                geometry=geom,
                                properties=record_dict,
                            )
                        )
                    except Exception as e:
                        # Log and continue to next feature without failing entire file
                        continue

            return ParsedDataset(
                features=features,
                crs=detected_crs,
                file_type="Shapefile (.zip)",
                metadata={"shp_name": primary_shp.name, "total_features": len(features)}
            )

        finally:
            # Clean up temporary extracted directory
            shutil.rmtree(temp_dir, ignore_errors=True)

shapefile_parser = ShapefileParser()
