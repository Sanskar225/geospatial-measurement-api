"""Regenerates the binary sample files in this folder.

    python sample_data/generate_samples.py

* sample_shapefile.zip     - 2 parcels in EPSG:4326 (with .prj)
* sample_utm_shapefile.zip - parcels in EPSG:32643 (UTM 43N, metres): shows that projected
                             inputs are handled and re-projected for the map
* survey_sample.kml        - hand-written, committed as text
"""

import tempfile
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

SAMPLE_DIR = Path(__file__).parent


def _clockwise(coords):
    """ESRI shapefiles store exterior rings clockwise."""
    return list(orient(Polygon(coords), sign=-1.0).exterior.coords)


def _zip_shapefile(base: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for ext in ("shp", "shx", "dbf", "prj", "cpg"):
            part = base.with_suffix(f".{ext}")
            if part.exists():
                archive.write(part, arcname=part.name)


def make_geographic_sample() -> None:
    parcels = [
        (
            "Industrial Park Alpha",
            "Commercial",
            101,
            [(77.5800, 12.9500), (77.5900, 12.9500), (77.5900, 12.9600), (77.5800, 12.9600)],
        ),
        (
            "Agri Zone Beta",
            "Agricultural",
            102,
            [(77.6000, 12.9000), (77.6200, 12.9000), (77.6200, 12.9200), (77.6000, 12.9200)],
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "bengaluru_parcels"
        with shapefile.Writer(str(base), shapeType=shapefile.POLYGON) as writer:
            writer.field("NAME", "C", 40)
            writer.field("LAND_USE", "C", 20)
            writer.field("PARCEL_ID", "N", 10)
            for name, use, pid, ring in parcels:
                writer.poly([_clockwise(ring + [ring[0]])])
                writer.record(name, use, pid)
        base.with_suffix(".prj").write_text(CRS.from_epsg(4326).to_wkt(version="WKT1_ESRI"))
        base.with_suffix(".cpg").write_text("UTF-8")
        _zip_shapefile(base, SAMPLE_DIR / "sample_shapefile.zip")


def make_utm_sample() -> None:
    to_utm = Transformer.from_crs(4326, 32643, always_xy=True)
    parcels = [
        (
            "Stockpile Yard 1",
            "Mining",
            [(77.5800, 12.9500), (77.5850, 12.9500), (77.5850, 12.9540), (77.5800, 12.9540)],
        ),
        (
            "Stockpile Yard 2",
            "Mining",
            [(77.5900, 12.9500), (77.5960, 12.9500), (77.5960, 12.9550), (77.5900, 12.9550)],
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "utm_site"
        with shapefile.Writer(str(base), shapeType=shapefile.POLYGON) as writer:
            writer.field("NAME", "C", 40)
            writer.field("CATEGORY", "C", 20)
            for name, category, ring in parcels:
                projected = [to_utm.transform(x, y) for x, y in ring + [ring[0]]]
                writer.poly([_clockwise(projected)])
                writer.record(name, category)
        base.with_suffix(".prj").write_text(CRS.from_epsg(32643).to_wkt(version="WKT1_ESRI"))
        _zip_shapefile(base, SAMPLE_DIR / "sample_utm_shapefile.zip")


if __name__ == "__main__":
    make_geographic_sample()
    make_utm_sample()
    print("Generated sample_shapefile.zip and sample_utm_shapefile.zip in", SAMPLE_DIR)
