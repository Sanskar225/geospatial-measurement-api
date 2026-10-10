"""Parser for KML 2.2 (``.kml``) and zipped KML (``.kmz``).

Design notes
------------
* XML is parsed with ``defusedxml`` (no DTDs / entity expansion -> no XXE or billion-laughs)
  and streamed with ``iterparse`` so memory stays flat on large files.
* Namespace agnostic: tags are compared by local name, so ``kml:Placemark`` and
  ``Placemark`` both work.
* A malformed coordinate invalidates *that feature* (reported with a reason) instead of
  being skipped - silently dropping one vertex would silently change the area.
* KML coordinates are WGS-84 longitude,latitude by specification.
"""

import io
import math
import re
import zipfile
from pathlib import Path
from typing import IO, Any
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import AppError, FileFormatError
from app.services.crs_service import CRSService, crs_service
from app.services.parsers.base import ParsedDataset, ParsedFeature, ParserLimits, json_safe

_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}


def _local(tag: Any) -> str:
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


class KMLParser:
    file_types = {".kml": "KML", ".kmz": "KMZ"}

    def __init__(self, limits: ParserLimits | None = None, crs: CRSService | None = None) -> None:
        self._limits = limits or ParserLimits()
        self._crs = crs or crs_service

    # ------------------------------------------------------------------ public API
    def parse(self, path: Path) -> ParsedDataset:
        is_kmz = path.suffix.lower() == ".kmz"
        stream = self._open_kmz(path) if is_kmz else path.open("rb")
        try:
            return self._parse_stream(stream, self.file_types[".kmz" if is_kmz else ".kml"])
        finally:
            stream.close()

    # ------------------------------------------------------------------ container handling
    def _open_kmz(self, path: Path) -> IO[bytes]:
        try:
            with zipfile.ZipFile(path) as archive:
                candidates = [
                    e
                    for e in archive.infolist()
                    if not e.is_dir() and e.filename.lower().endswith(".kml") and "__MACOSX" not in e.filename
                ]
                if not candidates:
                    raise FileFormatError("The KMZ archive does not contain a .kml file.")
                # Google Earth writes the main document as doc.kml.
                entry = next((e for e in candidates if e.filename.lower() == "doc.kml"), candidates[0])
                if entry.file_size > self._limits.max_uncompressed_bytes:
                    raise FileFormatError("The KML inside the KMZ is larger than the allowed size.")
                return io.BytesIO(archive.read(entry))
        except zipfile.BadZipFile as exc:
            raise FileFormatError(f"Corrupted KMZ archive: {exc}") from exc

    # ------------------------------------------------------------------ parsing
    def _parse_stream(self, stream: IO[bytes], file_type: str) -> ParsedDataset:
        wgs84 = self._crs.wgs84
        features: list[ParsedFeature] = []
        skipped = 0

        try:
            for _event, elem in SafeET.iterparse(stream, events=("end",), forbid_dtd=True):
                if _local(elem.tag) != "Placemark":
                    continue
                geometry_elem = next((c for c in elem if _local(c.tag) in _GEOMETRY_TAGS), None)
                if geometry_elem is None:
                    skipped += 1
                    elem.clear()
                    continue

                if len(features) >= self._limits.max_features:
                    raise FileFormatError(f"Too many features (limit {self._limits.max_features}).")

                geometry, error = None, None
                try:
                    geometry = self._geometry(geometry_elem)
                except ValueError as exc:
                    error = str(exc)

                index = len(features)
                props = self._properties(elem)
                features.append(
                    ParsedFeature(
                        index=index,
                        feature_id=elem.get("id") or str(index),
                        geometry=geometry,
                        properties=props,
                        crs=wgs84,
                        parse_error=error,
                    )
                )
                elem.clear()  # keep memory flat on big files
        except AppError:
            raise
        except (ParseError, DefusedXmlException, UnicodeDecodeError) as exc:
            raise FileFormatError(f"Corrupted or invalid KML XML structure: {exc}") from exc

        if not features:
            raise FileFormatError("No Placemarks with geometry were found in the KML file.")

        warnings = []
        if skipped:
            warnings.append(
                f"{skipped} placemark(s) skipped: they contain no supported geometry "
                "(supported: Point, LineString, LinearRing, Polygon, MultiGeometry)."
            )
        return ParsedDataset(features=features, file_type=file_type, warnings=warnings)

    # ------------------------------------------------------------------ geometry
    @staticmethod
    def _coordinates(elem: Element) -> list[tuple[float, float]]:
        """Coordinates of the ``<coordinates>`` child of ``elem`` (KML order: lon,lat[,alt])."""
        node = next((c for c in elem if _local(c.tag) == "coordinates"), None)
        if node is None or not (node.text or "").strip():
            return []
        # Be lenient about "lon, lat, alt" (spaces after commas) which many tools emit.
        tokens = re.sub(r"\s*,\s*", ",", node.text.strip()).split()
        coords: list[tuple[float, float]] = []
        for token in tokens:
            parts = token.split(",")
            try:
                if len(parts) < 2:
                    raise ValueError
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                raise ValueError(f"Malformed coordinate '{token[:40]}' (expected lon,lat[,alt])") from None
            if not (math.isfinite(lon) and math.isfinite(lat)):
                raise ValueError(f"Non-finite coordinate '{token[:40]}'")
            coords.append((lon, lat))
        return coords

    def _ring(self, boundary: Element, label: str) -> list[tuple[float, float]]:
        ring_elem = next((c for c in boundary if _local(c.tag) == "LinearRing"), None)
        coords = self._coordinates(ring_elem) if ring_elem is not None else []
        if len(coords) < 3:
            raise ValueError(f"Polygon {label} ring needs at least 3 coordinates, got {len(coords)}")
        return coords

    def _geometry(self, elem: Element) -> BaseGeometry:
        tag = _local(elem.tag)
        if tag == "Point":
            coords = self._coordinates(elem)
            if not coords:
                raise ValueError("Point has no coordinates")
            return Point(coords[0])
        if tag in ("LineString", "LinearRing"):  # a bare LinearRing is a closed line
            coords = self._coordinates(elem)
            if len(coords) < 2:
                raise ValueError(f"{tag} needs at least 2 coordinates, got {len(coords)}")
            return LineString(coords)
        if tag == "Polygon":
            shell, holes = None, []
            for child in elem:
                child_tag = _local(child.tag)
                if child_tag == "outerBoundaryIs":
                    shell = self._ring(child, "outer")
                elif child_tag == "innerBoundaryIs":
                    holes.append(self._ring(child, "inner"))
            if shell is None:
                raise ValueError("Polygon has no outerBoundaryIs")
            return Polygon(shell, holes)
        if tag == "MultiGeometry":
            return self._combine([self._geometry(c) for c in elem if _local(c.tag) in _GEOMETRY_TAGS])
        raise ValueError(f"Unsupported geometry element '{tag}'")

    @staticmethod
    def _flatten(geom: BaseGeometry) -> list[BaseGeometry]:
        if hasattr(geom, "geoms"):
            return [g for part in geom.geoms for g in KMLParser._flatten(part)]
        return [geom]

    def _combine(self, parts: list[BaseGeometry]) -> BaseGeometry:
        flat = [g for part in parts for g in self._flatten(part)]
        if not flat:
            raise ValueError("MultiGeometry contains no usable geometry")
        if len(flat) == 1:
            return flat[0]
        kinds = {g.geom_type for g in flat}
        if kinds == {"Polygon"}:
            return MultiPolygon(flat)
        if kinds == {"LineString"}:
            return MultiLineString(flat)
        if kinds == {"Point"}:
            return MultiPoint(flat)
        return GeometryCollection(flat)  # mixed: reported as unsupported for measurement

    # ------------------------------------------------------------------ attributes
    @staticmethod
    def _properties(placemark: Element) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for child in placemark:
            tag = _local(child.tag)
            if tag in ("name", "description") and child.text and child.text.strip():
                props[tag] = json_safe(child.text.strip())
            elif tag == "ExtendedData":
                for node in child.iter():
                    node_tag = _local(node.tag)
                    key = node.get("name")
                    if not key:
                        continue
                    if node_tag == "Data":  # <Data name="x"><value>..</value></Data>
                        value = next((v.text for v in node if _local(v.tag) == "value"), None)
                    elif node_tag == "SimpleData":  # inside <SchemaData>
                        value = node.text
                    else:
                        continue
                    if value is not None and value.strip():
                        props[key] = json_safe(value.strip())
        return props
