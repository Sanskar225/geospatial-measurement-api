import pytest
from shapely.geometry import Polygon, LineString, Point, GeometryCollection
from app.services.measurement_service import measurement_service
from app.models.schemas import MeasurementUnit

def test_polygon_area_calculation():
    # Square polygon in degrees
    poly = Polygon([(77.58, 12.97), (77.59, 12.97), (77.59, 12.98), (77.58, 12.98), (77.58, 12.97)])
    res = measurement_service.process_feature(
        feature_id="poly-1",
        geom=poly,
        properties={"landuse": "solar"},
        source_crs_str="EPSG:4326"
    )

    assert res.status == "SUCCESS"
    assert res.geometry_type == "Polygon"
    assert res.measurement.measurement_type == "area"
    assert res.measurement.unit == MeasurementUnit.SQUARE_METERS
    assert res.measurement.value > 0
    assert "hectares" in res.measurement.alternative_units
    assert "EPSG:32643" in res.projected_crs

def test_linestring_length_calculation():
    line = LineString([(77.58, 12.97), (77.59, 12.98)])
    res = measurement_service.process_feature(
        feature_id="line-1",
        geom=line,
        properties={"corridor": "pipe"},
        source_crs_str="EPSG:4326"
    )

    assert res.status == "SUCCESS"
    assert res.geometry_type == "LineString"
    assert res.measurement.measurement_type == "length"
    assert res.measurement.unit == MeasurementUnit.METERS
    assert res.measurement.value > 0
    assert "kilometers" in res.measurement.alternative_units
    assert "EPSG:32643" in res.projected_crs

def test_point_handling_graceful():
    pt = Point(77.58, 12.97)
    res = measurement_service.process_feature(
        feature_id="pt-1",
        geom=pt,
        properties={"type": "gcp"},
        source_crs_str="EPSG:4326"
    )

    # Per requirement: Point - No measurement is required. Handled gracefully.
    assert res.status == "SUCCESS"
    assert res.geometry_type == "Point"
    assert res.measurement.measurement_type == "none"
    assert res.measurement.value is None
    assert "zero dimension" in res.measurement.formatted.lower()

def test_unsupported_geometry_graceful():
    # GeometryCollection or custom unsupported
    gc = GeometryCollection()
    res = measurement_service.process_feature(
        feature_id="gc-1",
        geom=gc,
        properties={},
        source_crs_str="EPSG:4326"
    )

    assert res.status == "UNSUPPORTED_GEOMETRY"
    assert res.measurement.value is None
