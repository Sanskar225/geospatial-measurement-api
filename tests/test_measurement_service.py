import math

import pytest
from pyproj import CRS
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon

from app.models.schemas import FeatureStatus, MeasurementType
from app.services.crs_service import crs_service
from app.services.measurement_service import MeasurementService
from app.services.measurement_service import measurement_service as measure_svc
from app.services.parsers.base import ParsedFeature
from tests.helpers import square

AUTHALIC_RADIUS = 6_371_007.2  # metres, for an independent spherical cross-check


def feature(geom, crs="EPSG:4326", index=0, parse_error=None):
    return ParsedFeature(
        index=index,
        feature_id=str(index),
        geometry=geom,
        properties={"k": "v"},
        crs=crs_service.resolve(crs),
        parse_error=parse_error,
    )


def sphere_box_area(lon1, lat1, lon2, lat2):
    """Area of a lon/lat box on a sphere: R² Δλ (sin φ2 - sin φ1). Independent of pyproj/UTM."""
    return (
        AUTHALIC_RADIUS**2 * math.radians(lon2 - lon1) * (math.sin(math.radians(lat2)) - math.sin(math.radians(lat1)))
    )


class TestPolygons:
    def test_area_is_metric_not_degrees_and_matches_independent_formula(self):
        result = measure_svc.measure(feature(Polygon(square(77.58, 12.97, 0.01))))
        expected = sphere_box_area(77.58, 12.97, 77.59, 12.98)
        assert result.status == FeatureStatus.SUCCESS
        assert result.measurement.measurement_type == MeasurementType.AREA
        assert result.measurement.value == pytest.approx(expected, rel=0.003)
        assert result.measurement.value > 1_000_000  # ~1.2 km², a degrees-based answer would be 1e-4
        assert result.projected_crs == "EPSG:32643"
        assert result.crs == "EPSG:4326"

    def test_planar_and_geodesic_agree_for_small_polygon(self):
        m = measure_svc.measure(feature(Polygon(square(77.58, 12.97, 0.01)))).measurement
        assert m.advanced_metrics.distortion_percentage < 0.2
        assert m.advanced_metrics.perimeter_meters == pytest.approx(4 * 1085, rel=0.02)

    def test_alternative_units_are_consistent(self):
        m = measure_svc.measure(feature(Polygon(square(77.58, 12.97, 0.01)))).measurement
        assert m.alternative_units["hectares"] == pytest.approx(m.value / 10_000, rel=1e-4)
        assert m.alternative_units["acres"] == pytest.approx(m.value / 4046.8564224, rel=1e-4)

    def test_polygon_with_hole_subtracts_hole_in_both_methods(self):
        outer = [(77.6, 12.96), (77.615, 12.96), (77.615, 12.975), (77.6, 12.975)]
        inner = [(77.604, 12.964), (77.61, 12.964), (77.61, 12.97), (77.604, 12.97)]
        result = measure_svc.measure(feature(Polygon(outer, [inner])))
        planar_ring_only = measure_svc.measure(feature(Polygon(outer))).measurement.value
        hole = measure_svc.measure(feature(Polygon(inner))).measurement.value
        assert result.measurement.value == pytest.approx(planar_ring_only - hole, rel=1e-4)
        # regression: this used to report a ~27% "distortion" because the geodesic hole was *added*
        assert result.measurement.advanced_metrics.distortion_percentage < 0.3

    def test_multipolygon_is_sum_of_parts(self):
        a, b = Polygon(square(77.58, 12.97, 0.01)), Polygon(square(77.70, 12.97, 0.02))
        total = measure_svc.measure(feature(MultiPolygon([a, b]))).measurement.value
        parts = sum(measure_svc.measure(feature(p)).measurement.value for p in (a, b))
        assert total == pytest.approx(parts, rel=1e-6)

    def test_self_intersecting_polygon_is_repaired_and_flagged(self):
        bowtie = Polygon([(77.0, 12.0), (77.02, 12.02), (77.02, 12.0), (77.0, 12.02)])
        result = measure_svc.measure(feature(bowtie))
        assert result.status == FeatureStatus.SUCCESS
        assert result.measurement.value > 0
        assert any("repaired" in n for n in result.notes)

    def test_z_coordinates_are_ignored(self):
        flat = measure_svc.measure(feature(Polygon(square(77.58, 12.97, 0.01)))).measurement.value
        with_z = Polygon([(x, y, 920.0) for x, y in square(77.58, 12.97, 0.01)])
        assert measure_svc.measure(feature(with_z)).measurement.value == pytest.approx(flat, rel=1e-9)

    def test_distortion_above_threshold_adds_a_warning_note(self):
        strict = MeasurementService(distortion_warning_pct=0.1)
        wide = Polygon(square(65.0, 5.0, 20.0))  # ~2200 km wide: UTM is off by ~0.17%
        result = strict.measure(feature(wide))
        assert result.measurement.advanced_metrics.distortion_percentage > 0.1
        assert any("geodesic" in n for n in result.notes)
        assert not any("geodesic" in n for n in measure_svc.measure(feature(wide)).notes)  # default 0.5% threshold

    def test_polar_polygon_uses_ups(self):
        result = measure_svc.measure(feature(Polygon(square(10.0, 85.0, 1.0))))
        assert result.projected_crs == "EPSG:32661"
        assert result.measurement.value > 0


class TestProjectedInputs:
    """Regressions: a naive implementation trusts any 'projected' CRS and reports its raw units as metres."""

    def test_us_survey_feet_crs_is_converted_to_metres(self):
        ft_crs = CRS.from_epsg(2229)  # California zone 5, US survey feet
        result = measure_svc.measure(feature(Polygon(square(6_000_000, 2_000_000, 1000)), ft_crs))
        # 1000 ft x 1000 ft = 92,903 m². A naive implementation answers 1,000,000 "square_meters".
        assert result.measurement.value == pytest.approx(92_903, rel=0.005)
        assert result.measurement.advanced_metrics.distortion_percentage < 0.5

    def test_web_mercator_area_distortion_is_removed(self):
        # 1 km x 1 km in EPSG:3857 at ~60°N is really ~0.25 km² (scale = 1/cos φ in each axis)
        result = measure_svc.measure(feature(Polygon(square(1_000_000, 8_400_000, 1000)), CRS.from_epsg(3857)))
        assert result.measurement.value == pytest.approx(250_000, rel=0.03)
        assert result.measurement.value < 300_000

    def test_utm_input_in_matching_zone_is_used_as_is(self):
        result = measure_svc.measure(feature(Polygon(square(500_000, 1_435_000, 1000)), CRS.from_epsg(32643)))
        assert result.projected_crs == "EPSG:32643"
        assert result.measurement.value == pytest.approx(1_000_000, rel=1e-9)

    def test_other_projected_crs_india_lcc(self):
        # EPSG:7755 (WGS 84 / India NSF LCC) - a metric, non-UTM national CRS. Its grid is not
        # equal-area (a 1 km x 1 km grid square here covers ~1.014 km² on the ground), so the
        # correct answer is the ground area, not the grid area.
        result = measure_svc.measure(feature(Polygon(square(4_000_000, 3_000_000, 1000)), CRS.from_epsg(7755)))
        assert result.status == FeatureStatus.SUCCESS
        assert result.projected_crs.startswith("EPSG:326")
        assert result.measurement.value == pytest.approx(result.measurement.advanced_metrics.geodesic_value, rel=0.005)
        assert result.measurement.value != pytest.approx(1_000_000, rel=0.005)

    def test_projected_source_gets_wgs84_geometry_for_maps(self):
        result = measure_svc.measure(feature(Polygon(square(500_000, 1_435_000, 1000)), CRS.from_epsg(32643)))
        lon, lat = result.geometry_wgs84["coordinates"][0][0]
        assert 74 < lon < 76 and 12 < lat < 14
        assert result.geometry["coordinates"][0][0][0] == 500_000  # original kept untouched

    def test_wgs84_source_has_no_duplicate_geometry(self):
        assert measure_svc.measure(feature(Polygon(square(77.58, 12.97, 0.01)))).geometry_wgs84 is None


class TestLines:
    def test_length_matches_haversine(self):
        a, b = (77.58, 12.97), (77.59, 12.98)
        result = measure_svc.measure(feature(LineString([a, b])))
        lat1, lat2 = math.radians(a[1]), math.radians(b[1])
        h = (
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
        )
        haversine = 2 * AUTHALIC_RADIUS * math.asin(math.sqrt(h))
        assert result.measurement.measurement_type == MeasurementType.LENGTH
        assert result.measurement.value == pytest.approx(haversine, rel=0.004)
        assert result.measurement.alternative_units["kilometers"] == pytest.approx(
            result.measurement.value / 1000, rel=1e-4
        )

    def test_multilinestring_length_sums(self):
        from shapely.geometry import MultiLineString

        l1, l2 = LineString([(77.58, 12.97), (77.59, 12.97)]), LineString([(77.60, 12.97), (77.62, 12.97)])
        total = measure_svc.measure(feature(MultiLineString([l1, l2]))).measurement.value
        parts = sum(measure_svc.measure(feature(g)).measurement.value for g in (l1, l2))
        assert total == pytest.approx(parts, rel=1e-6)


class TestGracefulHandling:
    def test_point_has_no_measurement(self):
        result = measure_svc.measure(feature(Point(77.59, 12.975)))
        assert result.status == FeatureStatus.SUCCESS
        assert result.measurement.measurement_type == MeasurementType.NONE
        assert result.measurement.value is None

    def test_geometry_collection_is_unsupported_not_an_error(self):
        gc = GeometryCollection([Point(77.58, 12.97), Polygon(square(77.58, 12.97, 0.01))])
        result = measure_svc.measure(feature(gc))
        assert result.status == FeatureStatus.UNSUPPORTED_GEOMETRY
        assert "Polygon" in result.notes[0] and "Point" in result.notes[0]

    def test_empty_polygon(self):
        result = measure_svc.measure(feature(Polygon()))
        assert result.status == FeatureStatus.EMPTY_GEOMETRY

    def test_missing_geometry_from_null_shape(self):
        assert measure_svc.measure(feature(None)).status == FeatureStatus.EMPTY_GEOMETRY

    def test_missing_geometry_with_parse_error(self):
        result = measure_svc.measure(feature(None, parse_error="Malformed coordinate"))
        assert result.status == FeatureStatus.INVALID_GEOMETRY
        assert result.notes == ["Malformed coordinate"]

    @pytest.mark.parametrize(
        "geom", [Polygon(square(77, 200, 1)), Point(500_000, 1_400_000), LineString([(0, 0), (1, 95)])]
    )
    def test_coordinates_outside_lonlat_range_flagged_not_computed(self, geom):
        result = measure_svc.measure(feature(geom))
        assert result.status == FeatureStatus.INVALID_COORDINATES
        assert "EPSG:4326" in result.notes[0]
        assert result.measurement is None

    def test_unexpected_exception_is_contained(self, monkeypatch):
        monkeypatch.setattr(crs_service, "geodesic_area", lambda g: 1 / 0)
        result = measure_svc.measure(feature(Polygon(square(77.58, 12.97, 0.01))))
        assert result.status == FeatureStatus.CALCULATION_ERROR


class TestSummary:
    def test_counts_and_totals(self):
        results = [
            measure_svc.measure(feature(Polygon(square(77.58, 12.97, 0.01)), index=0)),
            measure_svc.measure(feature(Polygon(square(77.70, 12.97, 0.01)), index=1)),
            measure_svc.measure(feature(LineString([(77.58, 12.97), (77.59, 12.97)]), index=2)),
            measure_svc.measure(feature(Point(77.58, 12.97), index=3)),
            measure_svc.measure(feature(GeometryCollection([Point(1, 1), Point(2, 2)]), index=4)),
            measure_svc.measure(feature(Polygon(square(77, 200, 1)), index=5)),
            measure_svc.measure(feature(None, index=6)),
        ]
        s = measure_svc.summarize(results)
        assert (s.total_features, s.polygon_count, s.linestring_count, s.point_count) == (7, 3, 1, 1)
        assert s.unsupported_count == 1
        assert s.failed_count == 2  # invalid coordinates + null shape
        areas = [r.measurement.value for r in results[:2]]
        assert s.total_area_sq_meters == pytest.approx(sum(areas), abs=0.01)
        assert s.average_polygon_area_sq_m == pytest.approx(sum(areas) / 2, abs=0.01)  # only measured polygons
        assert s.total_length_meters == results[2].measurement.value

    def test_empty_summary_has_no_averages(self):
        s = measure_svc.summarize([])
        assert s.total_features == 0 and s.average_polygon_area_sq_m is None
