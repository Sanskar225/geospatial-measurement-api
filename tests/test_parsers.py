import datetime
import io
import zipfile
from pathlib import Path

import pytest
import shapefile
from pyproj import CRS

from app.core.exceptions import FileFormatError, MissingCRSError, UnsupportedFileTypeError
from app.services.parsers import ParserLimits, build_registry
from app.services.parsers.base import json_safe
from app.services.parsers.kml_parser import KMLParser
from app.services.parsers.shapefile_parser import ShapefileParser
from tests.helpers import (
    WGS84_ESRI_WKT,
    kml,
    kml_line,
    kml_point,
    kml_polygon,
    make_shapefile_zip,
    square,
    zip_of,
)

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


@pytest.fixture
def write(tmp_path):
    def _write(name: str, data: bytes) -> Path:
        path = tmp_path / name
        path.write_bytes(data)
        return path

    return _write


# ============================================================ KML
class TestKML:
    def test_sample_file(self):
        dataset = KMLParser().parse(SAMPLE_DIR / "survey_sample.kml")
        assert [f.geometry.geom_type for f in dataset.features] == ["Polygon", "LineString", "Point", "Polygon"]
        assert dataset.crs == "EPSG:4326"
        polygon_with_hole = dataset.features[3].geometry
        assert len(polygon_with_hole.interiors) == 1

    def test_extended_data_and_schema_data_are_both_read(self, write):
        extra = (
            '<ExtendedData><Data name="Project"><value>Alpha</value></Data>'
            '<SchemaData schemaUrl="#s"><SimpleData name="Owner">Aereo</SimpleData></SchemaData></ExtendedData>'
        )
        path = write("a.kml", kml(kml_polygon("P1", extra=extra)))
        props = KMLParser().parse(path).features[0].properties
        assert props == {"name": "P1", "Project": "Alpha", "Owner": "Aereo"}

    def test_ids_and_indexes(self, write):
        body = kml(
            kml_polygon("same name").replace("<Placemark>", '<Placemark id="plot-7">'),
            kml_polygon("same name", x=77.7),
        )
        features = KMLParser().parse(write("a.kml", body)).features
        assert [f.feature_id for f in features] == ["plot-7", "1"]  # unique even when names repeat
        assert [f.index for f in features] == [0, 1]

    def test_nested_folders_and_namespace_prefix(self, write):
        xml = (
            b'<?xml version="1.0"?><k:kml xmlns:k="http://www.opengis.net/kml/2.2"><k:Document><k:Folder><k:Folder>'
            b"<k:Placemark><k:name>deep</k:name><k:Point><k:coordinates>77,13</k:coordinates></k:Point></k:Placemark>"
            b"</k:Folder></k:Folder></k:Document></k:kml>"
        )
        features = KMLParser().parse(write("a.kml", xml)).features
        assert len(features) == 1 and features[0].geometry.geom_type == "Point"

    def test_whitespace_after_commas_is_tolerated(self, write):
        path = write("a.kml", kml(kml_line(coords="77.58, 12.97, 0   77.59, 12.98, 0")))
        assert len(KMLParser().parse(path).features[0].geometry.coords) == 2

    def test_standalone_linear_ring_becomes_linestring(self, write):
        ring = (
            "<Placemark><LinearRing><coordinates>77,13 77.1,13 77.1,13.1 77,13</coordinates></LinearRing></Placemark>"
        )
        assert KMLParser().parse(write("a.kml", kml(ring))).features[0].geometry.geom_type == "LineString"

    @pytest.mark.parametrize(
        ("members", "expected"),
        [
            (["Polygon", "Polygon"], "MultiPolygon"),
            (["Point", "Point"], "MultiPoint"),
            (["LineString", "LineString"], "MultiLineString"),
            (["Polygon", "Point"], "GeometryCollection"),
            (["Polygon"], "Polygon"),
        ],
    )
    def test_multigeometry_is_combined(self, write, members, expected):
        parts = {
            "Polygon": "<Polygon><outerBoundaryIs><LinearRing><coordinates>77,13 77.1,13 77.1,13.1 77,13</coordinates></LinearRing></outerBoundaryIs></Polygon>",
            "Point": "<Point><coordinates>77,13</coordinates></Point>",
            "LineString": "<LineString><coordinates>77,13 77.1,13.1</coordinates></LineString>",
        }
        xml = "<Placemark><MultiGeometry>" + "".join(parts[m] for m in members) + "</MultiGeometry></Placemark>"
        assert KMLParser().parse(write("a.kml", kml(xml))).features[0].geometry.geom_type == expected

    def test_bad_coordinate_invalidates_that_feature_only(self, write):
        bad = kml_line("bad", coords="77.58,12.97 abc,12.98")
        dataset = KMLParser().parse(write("a.kml", kml(bad, kml_point("ok"))))
        assert dataset.features[0].geometry is None
        assert "Malformed coordinate" in dataset.features[0].parse_error
        assert dataset.features[1].geometry is not None

    def test_degenerate_polygon_is_reported(self, write):
        poly = "<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>77,13 77.1,13</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        feature = KMLParser().parse(write("a.kml", kml(poly, kml_point()))).features[0]
        assert feature.geometry is None and "at least 3" in feature.parse_error

    def test_placemarks_without_geometry_are_counted_in_warnings(self, write):
        label = "<Placemark><name>just a label</name></Placemark>"
        dataset = KMLParser().parse(write("a.kml", kml(label, kml_point())))
        assert len(dataset.features) == 1
        assert "1 placemark(s) skipped" in dataset.warnings[0]

    def test_no_features_is_an_error(self, write):
        with pytest.raises(FileFormatError, match="No Placemarks"):
            KMLParser().parse(write("a.kml", kml("<Folder/>")))

    def test_malformed_xml(self, write):
        with pytest.raises(FileFormatError, match="invalid KML"):
            KMLParser().parse(write("a.kml", b"<kml><Placemark><name>x</kml>"))

    def test_billion_laughs_and_xxe_are_rejected(self, write):
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
            b"<kml><Placemark><name>&b;</name><Point><coordinates>1,1</coordinates></Point></Placemark></kml>"
        )
        with pytest.raises(FileFormatError):
            KMLParser().parse(write("a.kml", bomb))
        xxe = b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]><kml>&x;</kml>'
        with pytest.raises(FileFormatError):
            KMLParser().parse(write("b.kml", xxe))

    def test_feature_limit(self, write):
        parser = KMLParser(ParserLimits(max_features=2))
        with pytest.raises(FileFormatError, match="Too many features"):
            parser.parse(write("a.kml", kml(kml_point(), kml_point(), kml_point())))

    def test_kmz(self, write):
        data = zip_of({"doc.kml": kml(kml_polygon("in kmz")), "files/icon.png": b"\x89PNG"})
        dataset = KMLParser().parse(write("a.kmz", data))
        assert dataset.file_type == "KMZ" and dataset.features[0].properties["name"] == "in kmz"

    def test_kmz_without_kml(self, write):
        with pytest.raises(FileFormatError, match="does not contain a .kml"):
            KMLParser().parse(write("a.kmz", zip_of({"readme.txt": b"hi"})))

    def test_kmz_that_is_not_a_zip(self, write):
        with pytest.raises(FileFormatError, match="Corrupted KMZ"):
            KMLParser().parse(write("a.kmz", b"not a zip at all"))


# ============================================================ Shapefile
class TestShapefile:
    def test_sample_files(self):
        parser = ShapefileParser()
        dataset = parser.parse(SAMPLE_DIR / "sample_shapefile.zip")
        assert len(dataset.features) == 2 and dataset.crs == "EPSG:4326" and not dataset.warnings
        utm = parser.parse(SAMPLE_DIR / "sample_utm_shapefile.zip")
        assert utm.crs == "EPSG:32643"

    def test_polygon_attributes_and_crs(self, write):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], [("Plot A",)], crs=WGS84_ESRI_WKT)
        dataset = ShapefileParser().parse(write("a.zip", data))
        feature = dataset.features[0]
        assert feature.properties == {"NAME": "Plot A"} and feature.crs.identifier == "EPSG:4326"
        assert feature.geometry.geom_type == "Polygon" and feature.feature_id == "0"

    def test_date_decimal_and_nan_attributes_are_json_safe(self, write):
        """Regression: DBF date fields used to crash JSON serialisation with a 500."""
        data = make_shapefile_zip(
            [square(77.58, 12.97, 0.01)],
            [("A", datetime.date(2026, 1, 2), 3.5, float("nan"))],
            fields=(("NAME", "C", 20), ("SURVEYED", "D"), ("AREA_HA", "N", 10, 2), ("RATIO", "F", 10, 3)),
            crs=WGS84_ESRI_WKT,
        )
        props = ShapefileParser().parse(write("a.zip", data)).features[0].properties
        assert props["SURVEYED"] == "2026-01-02" and props["AREA_HA"] == 3.5
        assert props["RATIO"] is None

    def test_uppercase_extensions(self, write):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT, upper_case=True)
        assert len(ShapefileParser().parse(write("a.zip", data)).features) == 1

    def test_macosx_resource_forks_are_ignored(self, write):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT, mac_junk=True)
        dataset = ShapefileParser().parse(write("a.zip", data))
        assert len(dataset.features) == 1 and not dataset.warnings

    def test_shapefile_inside_a_subfolder(self, write):
        inner = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT)
        with zipfile.ZipFile(io.BytesIO(inner)) as src:
            data = zip_of({f"export/data/{n}": src.read(n) for n in src.namelist()})
        assert len(ShapefileParser().parse(write("a.zip", data)).features) == 1

    def test_missing_prj_with_lonlat_coordinates_assumes_wgs84_with_warning(self, write):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=None)
        dataset = ShapefileParser().parse(write("a.zip", data))
        assert dataset.crs == "EPSG:4326"
        assert "EPSG:4326 was assumed" in dataset.warnings[0]

    def test_missing_prj_with_projected_coordinates_is_rejected(self, write):
        """Regression: this used to be silently treated as degrees and produced garbage."""
        data = make_shapefile_zip([square(500_000, 1_435_000, 1000)], crs=None)
        with pytest.raises(MissingCRSError, match="Add the .prj"):
            ShapefileParser().parse(write("a.zip", data))

    def test_unparseable_prj_falls_back_like_missing_prj(self, write):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs="garbage wkt")
        dataset = ShapefileParser().parse(write("a.zip", data))
        assert dataset.crs == "EPSG:4326" and "unreadable .prj" in dataset.warnings[0]

    def test_projected_crs_is_reported(self, write):
        data = make_shapefile_zip([square(500_000, 1_435_000, 1000)], crs=CRS.from_epsg(32643))
        assert ShapefileParser().parse(write("a.zip", data)).crs == "EPSG:32643"

    def test_null_shapes_are_kept_with_correct_indexes(self, write):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01), None, square(77.7, 12.97, 0.01)], crs=WGS84_ESRI_WKT)
        features = ShapefileParser().parse(write("a.zip", data)).features
        assert [f.geometry is None for f in features] == [False, True, False]
        assert features[1].parse_error is None and features[2].index == 2

    def test_polyline_and_point_layers(self, write):
        line = make_shapefile_zip([[(77.58, 12.97), (77.59, 12.98)]], shape_type=shapefile.POLYLINE, crs=WGS84_ESRI_WKT)
        assert ShapefileParser().parse(write("l.zip", line)).features[0].geometry.geom_type == "LineString"
        point = make_shapefile_zip([(77.58, 12.97)], shape_type=shapefile.POINT, crs=WGS84_ESRI_WKT)
        assert ShapefileParser().parse(write("p.zip", point)).features[0].geometry.geom_type == "Point"

    def test_missing_shx_and_dbf_still_parse(self, write):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT, omit=("shx",))
        assert len(ShapefileParser().parse(write("a.zip", data)).features) == 1
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT, omit=("dbf",))
        dataset = ShapefileParser().parse(write("b.zip", data))
        assert dataset.features[0].properties == {} and "no .dbf" in dataset.warnings[0]

    def test_multiple_layers_are_all_read(self, write):
        other = make_shapefile_zip([square(77.9, 12.97, 0.01)], crs=WGS84_ESRI_WKT, name="roads")
        with zipfile.ZipFile(io.BytesIO(other)) as src:
            extra = {n: src.read(n) for n in src.namelist()}
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT, name="parcels", extra_layers=extra)
        features = ShapefileParser().parse(write("a.zip", data)).features
        assert [f.feature_id for f in features] == ["parcels/0", "roads/0"]
        assert [f.index for f in features] == [0, 1]

    def test_mixed_crs_across_layers(self, write):
        utm = make_shapefile_zip([square(500_000, 1_435_000, 1000)], crs=CRS.from_epsg(32643), name="utm")
        with zipfile.ZipFile(io.BytesIO(utm)) as src:
            extra = {n: src.read(n) for n in src.namelist()}
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT, name="geo", extra_layers=extra)
        assert ShapefileParser().parse(write("a.zip", data)).crs == "MIXED"

    def test_latin1_attributes_without_cpg_fall_back(self, write):
        data = make_shapefile_zip(
            [square(77.58, 12.97, 0.01)], [("Café Zürich",)], crs=WGS84_ESRI_WKT, encoding="latin-1"
        )
        dataset = ShapefileParser().parse(write("a.zip", data))
        assert dataset.features[0].properties["NAME"] == "Café Zürich"
        assert "latin-1" in dataset.warnings[-1]

    def test_declared_cpg_encoding_is_honoured(self, write):
        data = make_shapefile_zip(
            [square(77.58, 12.97, 0.01)], [("Café",)], crs=WGS84_ESRI_WKT, encoding="cp1252", cpg="1252"
        )
        dataset = ShapefileParser().parse(write("a.zip", data))
        assert dataset.features[0].properties["NAME"] == "Café" and not dataset.warnings

    def test_not_a_zip(self, write):
        with pytest.raises(FileFormatError, match="not a valid zip"):
            ShapefileParser().parse(write("a.zip", b"definitely not a zip"))

    def test_zip_without_shp(self, write):
        with pytest.raises(FileFormatError, match="does not contain any .shp"):
            ShapefileParser().parse(write("a.zip", zip_of({"readme.txt": b"hello"})))

    @pytest.mark.filterwarnings("ignore::shapefile.PossiblyCorruptFileHeader")
    def test_corrupt_shp_content(self, write):
        data = zip_of({"a.shp": b"garbage" * 50, "a.dbf": b"x" * 100})
        with pytest.raises(FileFormatError):
            ShapefileParser().parse(write("a.zip", data))

    def test_empty_shapefile(self, write):
        data = make_shapefile_zip([], crs=WGS84_ESRI_WKT)
        with pytest.raises(FileFormatError, match="no features"):
            ShapefileParser().parse(write("a.zip", data))

    def test_zip_bomb_limits(self, write):
        bomb = zip_of({"a.shp": b"\x00" * 5_000_000, "a.dbf": b""})
        with pytest.raises(FileFormatError, match="zip bomb"):
            ShapefileParser(ParserLimits(max_uncompressed_bytes=1_000_000)).parse(write("a.zip", bomb))
        many = zip_of({f"f{i}.txt": b"x" for i in range(20)})
        with pytest.raises(FileFormatError, match="too many entries"):
            ShapefileParser(ParserLimits(max_zip_entries=10)).parse(write("b.zip", many))

    def test_feature_limit(self, write):
        data = make_shapefile_zip([square(77 + i * 0.1, 12, 0.01) for i in range(5)], crs=WGS84_ESRI_WKT)
        with pytest.raises(FileFormatError, match="Too many features"):
            ShapefileParser(ParserLimits(max_features=3)).parse(write("a.zip", data))

    def test_zip_slip_names_are_harmless(self, write):
        """Nothing is extracted to disk, so path traversal names cannot write anywhere."""
        inner = make_shapefile_zip([square(77.58, 12.97, 0.01)], crs=WGS84_ESRI_WKT)
        with zipfile.ZipFile(io.BytesIO(inner)) as src:
            data = zip_of({f"../../evil/{n}": src.read(n) for n in src.namelist()})
        assert len(ShapefileParser().parse(write("a.zip", data)).features) == 1


# ============================================================ registry & helpers
class TestRegistryAndHelpers:
    def test_registry_dispatch(self):
        registry = build_registry()
        assert registry.file_type_for(".KML") == "KML"
        assert registry.file_type_for(".kmz") == "KMZ"
        assert registry.file_type_for(".zip").startswith("Shapefile")
        assert registry.extensions == {".kml", ".kmz", ".zip"}

    def test_registry_rejects_unknown_extension(self):
        with pytest.raises(UnsupportedFileTypeError, match=r"\.geojson"):
            build_registry().for_extension(".geojson")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            (True, True),
            (3, 3),
            (1.5, 1.5),
            (float("nan"), None),
            (float("inf"), None),
            (datetime.date(2026, 1, 2), "2026-01-02"),
            (b"caf\xc3\xa9", "café"),
            ("a\x00b", "ab"),
        ],
    )
    def test_json_safe(self, value, expected):
        assert json_safe(value) == expected
