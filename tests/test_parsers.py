import pytest
from pathlib import Path
from app.services.parsers.kml_parser import kml_parser
from app.services.parsers.shapefile_parser import shapefile_parser
from app.core.exceptions import FileFormatError

SAMPLE_DIR = Path(__file__).parent.parent / "sample_data"

def test_parse_sample_kml():
    kml_path = SAMPLE_DIR / "survey_sample.kml"
    assert kml_path.exists()

    dataset = kml_parser.parse(kml_path)
    assert dataset.file_type == "KML"
    assert dataset.crs == "EPSG:4326"
    assert len(dataset.features) >= 4

    types = [f.geometry.geom_type for f in dataset.features]
    assert "Polygon" in types
    assert "LineString" in types
    assert "Point" in types

def test_parse_sample_shapefile_zip():
    shp_zip = SAMPLE_DIR / "sample_shapefile.zip"
    assert shp_zip.exists()

    dataset = shapefile_parser.parse(shp_zip)
    assert dataset.file_type == "Shapefile (.zip)"
    assert len(dataset.features) == 2
    assert dataset.features[0].geometry.geom_type == "Polygon"
    assert "NAME" in dataset.features[0].properties

def test_shapefile_invalid_zip(tmp_path):
    bad_zip = tmp_path / "corrupt.zip"
    bad_zip.write_text("This is not a zip file")

    with pytest.raises(FileFormatError):
        shapefile_parser.parse(bad_zip)

def test_kml_invalid_xml(tmp_path):
    bad_kml = tmp_path / "corrupt.kml"
    bad_kml.write_text("<unclosed_tag>")

    with pytest.raises(FileFormatError):
        kml_parser.parse(bad_kml)
