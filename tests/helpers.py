"""Builders for in-memory test files, so the repo needs no binary fixtures."""

import io
import tempfile
import uuid
import zipfile
from pathlib import Path

import shapefile
from pyproj import CRS
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

WGS84_ESRI_WKT = CRS.from_epsg(4326).to_wkt(version="WKT1_ESRI")


def square(x: float, y: float, size: float) -> list[tuple[float, float]]:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]


def _clockwise(ring):
    return list(orient(Polygon(ring), sign=-1.0).exterior.coords)


def make_shapefile_zip(
    shapes,
    records=None,
    *,
    fields=(("NAME", "C", 40),),
    shape_type=shapefile.POLYGON,
    crs: CRS | str | None = None,
    name="layer",
    upper_case=False,
    mac_junk=False,
    cpg: str | None = None,
    omit: tuple[str, ...] = (),
    extra_layers: dict[str, bytes] | None = None,
    encoding="utf-8",
) -> bytes:
    """Build a zipped shapefile.

    ``shapes``: for POLYGON a list of rings (list of (x, y)); for POLYLINE a list of
    coordinate lists; for POINT a list of (x, y); ``None`` writes a null shape.
    """
    records = records or [(f"feature-{i}-{uuid.uuid4().hex[:6]}",) for i in range(len(shapes))]
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / name
        with shapefile.Writer(str(base), shapeType=shape_type, encoding=encoding) as writer:
            for field in fields:
                writer.field(*field)
            for shp, rec in zip(shapes, records, strict=True):
                if shp is None:
                    writer.null()
                elif shape_type == shapefile.POLYGON:
                    writer.poly([_clockwise(shp)])
                elif shape_type == shapefile.POLYLINE:
                    writer.line([shp])
                else:
                    writer.point(*shp)
                writer.record(*rec)
        if crs is not None:
            wkt = crs if isinstance(crs, str) else CRS.from_user_input(crs).to_wkt(version="WKT1_ESRI")
            base.with_suffix(".prj").write_text(wkt)
        if cpg:
            base.with_suffix(".cpg").write_text(cpg)

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for ext in ("shp", "shx", "dbf", "prj", "cpg"):
                part = base.with_suffix(f".{ext}")
                if ext in omit or not part.exists():
                    continue
                arc = f"{name}.{ext.upper() if upper_case else ext}"
                archive.write(part, arcname=arc)
            if mac_junk:
                archive.writestr(f"__MACOSX/._{name}.shp", b"\x00\x05\x16\x07 not a shapefile")
            for extra_name, data in (extra_layers or {}).items():
                archive.writestr(extra_name, data)
        return out.getvalue()


def zip_of(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, data in files.items():
            archive.writestr(filename, data)
    return out.getvalue()


def kml(*placemarks: str, unique: bool = True) -> bytes:
    """Wrap Placemark XML in a KML document. A unique comment keeps the SHA-256 distinct per call."""
    marker = f"<!-- {uuid.uuid4().hex} -->" if unique else ""
    body = "\n".join(placemarks)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>{marker}{body}</Document></kml>'
    ).encode()


def kml_polygon(name="poly", x=77.58, y=12.97, size=0.01, extra="") -> str:
    ring = " ".join(f"{px},{py},0" for px, py in square(x, y, size))
    return (
        f"<Placemark><name>{name}</name>{extra}<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )


def kml_line(name="line", coords="77.58,12.97,0 77.59,12.98,0") -> str:
    return f"<Placemark><name>{name}</name><LineString><coordinates>{coords}</coordinates></LineString></Placemark>"


def kml_point(name="pt", coords="77.59,12.975,0") -> str:
    return f"<Placemark><name>{name}</name><Point><coordinates>{coords}</coordinates></Point></Placemark>"
