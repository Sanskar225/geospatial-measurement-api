import re
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import defusedxml.ElementTree as ET
from xml.etree.ElementTree import Element
from shapely.geometry import Polygon, LineString, Point, MultiPolygon, MultiLineString
from shapely.geometry.base import BaseGeometry

from app.core.exceptions import FileFormatError
from app.services.parsers.base import ParsedDataset, ParsedFeature

class KMLParser:
    """
    Production KML / KMZ parser with namespace agnosticism, XXE protection,
    BOM handling, and support for Polygon (with holes), LineString, MultiGeometry, and Points.
    """

    @staticmethod
    def _clean_tag(tag: str) -> str:
        """Strips XML namespaces like {http://www.opengis.net/kml/2.2}Placemark -> Placemark"""
        return tag.split("}")[-1] if "}" in tag else tag

    @staticmethod
    def _parse_coordinates_str(coords_str: str) -> List[Tuple[float, float]]:
        result = []
        tokens = re.split(r"\s+", coords_str.strip())
        for token in tokens:
            if not token:
                continue
            parts = token.split(",")
            if len(parts) >= 2:
                try:
                    lon = float(parts[0])
                    lat = float(parts[1])
                    result.append((lon, lat))
                except ValueError:
                    continue
        return result

    def _parse_linear_ring(self, ring_elem: Element) -> List[Tuple[float, float]]:
        for child in ring_elem:
            if self._clean_tag(child.tag) == "coordinates" and child.text:
                return self._parse_coordinates_str(child.text)
        return []

    def _parse_polygon(self, poly_elem: Element) -> Optional[Polygon]:
        exterior_ring = []
        interior_rings = []

        for child in poly_elem:
            tag = self._clean_tag(child.tag)
            if tag == "outerBoundaryIs":
                for sub in child:
                    if self._clean_tag(sub.tag) == "LinearRing":
                        exterior_ring = self._parse_linear_ring(sub)
            elif tag == "innerBoundaryIs":
                for sub in child:
                    if self._clean_tag(sub.tag) == "LinearRing":
                        ring = self._parse_linear_ring(sub)
                        if len(ring) >= 3:
                            interior_rings.append(ring)

        if len(exterior_ring) >= 3:
            try:
                return Polygon(shell=exterior_ring, holes=interior_rings)
            except Exception:
                return None
        return None

    def _parse_linestring(self, line_elem: Element) -> Optional[LineString]:
        for child in line_elem:
            if self._clean_tag(child.tag) == "coordinates" and child.text:
                coords = self._parse_coordinates_str(child.text)
                if len(coords) >= 2:
                    try:
                        return LineString(coords)
                    except Exception:
                        return None
        return None

    def _parse_point(self, pt_elem: Element) -> Optional[Point]:
        for child in pt_elem:
            if self._clean_tag(child.tag) == "coordinates" and child.text:
                coords = self._parse_coordinates_str(child.text)
                if coords:
                    try:
                        return Point(coords[0])
                    except Exception:
                        return None
        return None

    def _extract_geometry_from_element(self, elem: Element) -> Optional[BaseGeometry]:
        tag = self._clean_tag(elem.tag)
        if tag == "Polygon":
            return self._parse_polygon(elem)
        elif tag == "LineString":
            return self._parse_linestring(elem)
        elif tag == "Point":
            return self._parse_point(elem)
        elif tag == "LinearRing":
            coords = self._parse_coordinates_str(elem.text or "")
            if len(coords) >= 2:
                try:
                    return LineString(coords)
                except Exception:
                    pass
        elif tag == "MultiGeometry":
            sub_geoms = []
            for sub in elem:
                sub_g = self._extract_geometry_from_element(sub)
                if sub_g:
                    sub_geoms.append(sub_g)
            
            if not sub_geoms:
                return None
            
            if all(isinstance(g, Polygon) for g in sub_geoms):
                return MultiPolygon(sub_geoms)
            elif all(isinstance(g, LineString) for g in sub_geoms):
                return MultiLineString(sub_geoms)
            else:
                from shapely.geometry import GeometryCollection
                return GeometryCollection(sub_geoms)
        return None

    def _extract_properties(self, placemark: Element) -> Dict[str, Any]:
        props = {}
        for elem in placemark:
            tag = self._clean_tag(elem.tag)
            if tag in ("name", "description", "styleUrl") and elem.text:
                props[tag] = elem.text.strip()
            elif tag == "ExtendedData":
                for ext in elem:
                    ext_tag = self._clean_tag(ext.tag)
                    if ext_tag == "Data":
                        attr_name = ext.attrib.get("name")
                        val_node = [c for c in ext if self._clean_tag(c.tag) == "value"]
                        if attr_name and val_node and val_node[0].text:
                            props[attr_name] = val_node[0].text.strip()
                    elif ext_tag == "SimpleData":
                        attr_name = ext.attrib.get("name")
                        if attr_name and ext.text:
                            props[attr_name] = ext.text.strip()
        return props

    def parse(self, filepath: Path) -> ParsedDataset:
        file_type = "KML"
        kml_source_path = filepath
        temp_dir = None

        # Check if KMZ (Zipped KML)
        if filepath.suffix.lower() == ".kmz" or (zipfile.is_zipfile(filepath) and filepath.suffix.lower() != ".zip"):
            file_type = "KMZ"
            temp_dir = tempfile.mkdtemp(prefix="kmz_extract_")
            try:
                with zipfile.ZipFile(filepath, 'r') as kmz_ref:
                    kml_members = [m for m in kmz_ref.namelist() if m.lower().endswith(".kml")]
                    if not kml_members:
                        raise FileFormatError("KMZ archive does not contain any .kml file.")
                    kmz_ref.extract(kml_members[0], temp_dir)
                    kml_source_path = Path(temp_dir) / kml_members[0]
            except Exception as e:
                if temp_dir:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                raise FileFormatError(f"Failed to open KMZ archive: {str(e)}")

        try:
            # Read XML content with BOM strip
            with open(kml_source_path, "rb") as f:
                content = f.read()

            for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
                if content.startswith(bom):
                    content = content[len(bom):]
                    break

            try:
                root = ET.fromstring(content)
            except Exception as e:
                raise FileFormatError(f"Corrupted or invalid KML XML structure: {str(e)}")

            features: List[ParsedFeature] = []
            feature_index = 1

            for elem in root.iter():
                if self._clean_tag(elem.tag) == "Placemark":
                    props = self._extract_properties(elem)
                    feature_id = props.get("name") or str(feature_index)

                    geom = None
                    for child in elem:
                        geom = self._extract_geometry_from_element(child)
                        if geom is not None:
                            break

                    if geom is not None:
                        features.append(
                            ParsedFeature(
                                feature_id=str(feature_id),
                                geometry=geom,
                                properties=props,
                            )
                        )
                        feature_index += 1

            if not features:
                raise FileFormatError("No valid geospatial features (Placemarks) found in the KML file.")

            return ParsedDataset(
                features=features,
                crs="EPSG:4326",
                file_type=file_type,
                warnings=[],
                metadata={"total_features": len(features)}
            )

        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

kml_parser = KMLParser()
