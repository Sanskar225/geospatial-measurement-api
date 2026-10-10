import math

import pytest
from pyproj import CRS
from shapely.geometry import Polygon

from app.core.exceptions import InvalidCoordinatesError, InvalidCRSError
from app.services.crs_service import CRSService, crs_service
from tests.helpers import WGS84_ESRI_WKT, square


class TestProjectionSelection:
    @pytest.mark.parametrize(
        ("lon", "lat", "epsg"),
        [
            (77.59, 12.97, 32643),  # Bengaluru -> UTM 43N
            (-74.0, 40.7, 32618),  # New York -> 18N
            (151.2, -33.9, 32756),  # Sydney -> 56S
            (0.0, 0.0, 32631),  # on the equator -> northern hemisphere codes
            (179.9, 10, 32660),
            (-179.9, 10, 32601),
            (180.0, 0, 32601),  # 180 wraps to -180
        ],
    )
    def test_standard_utm_zones(self, lon, lat, epsg):
        assert CRSService.select_projected_epsg(lon, lat) == epsg

    def test_norway_exception(self):
        assert CRSService.select_projected_epsg(10.0, 60.0) == 32632  # 32V, not 32N

    @pytest.mark.parametrize(("lon", "epsg"), [(5, 32631), (15, 32633), (25, 32635), (38, 32637)])
    def test_svalbard_exceptions(self, lon, epsg):
        assert CRSService.select_projected_epsg(lon, 78.0) == epsg

    def test_poles_use_ups(self):
        assert CRSService.select_projected_epsg(10, 85) == 32661
        assert CRSService.select_projected_epsg(10, -81) == 32761

    def test_describe(self):
        assert CRSService.describe_projection(32643) == "UTM zone 43N"
        assert CRSService.describe_projection(32756) == "UTM zone 56S"
        assert CRSService.describe_projection(32661) == "UPS North"


class TestResolve:
    def test_esri_wkt_resolves_to_epsg_4326(self):
        info = crs_service.resolve(WGS84_ESRI_WKT)
        assert info.identifier == "EPSG:4326"
        assert info.is_wgs84 and info.is_geographic

    def test_epsg_code(self):
        info = crs_service.resolve(32643)
        assert info.identifier == "EPSG:32643"
        assert info.is_projected and not info.is_wgs84
        assert info.epsg == 32643

    def test_unknown_projection_gets_custom_identifier(self):
        crs = CRS.from_proj4("+proj=tmerc +lat_0=0 +lon_0=77 +k=0.9996 +x_0=500000 +ellps=WGS84 +units=m")
        info = crs_service.resolve(crs)
        assert info.identifier.startswith("CUSTOM:")
        assert info.epsg is None

    def test_garbage_raises_instead_of_defaulting_to_wgs84(self):
        with pytest.raises(InvalidCRSError):
            crs_service.resolve("this is not a crs")

    def test_engineering_crs_rejected(self):
        wkt = 'LOCAL_CS["site",UNIT["metre",1]]'
        with pytest.raises(InvalidCRSError):
            crs_service.resolve(wkt)


class TestTransforms:
    def test_looks_geographic(self):
        assert CRSService.looks_geographic((77, 12, 78, 13))
        assert not CRSService.looks_geographic((500000, 1400000, 501000, 1401000))
        assert not CRSService.looks_geographic((float("nan"), 0, 1, 1))

    def test_to_wgs84_rejects_out_of_range(self):
        wgs = crs_service.wgs84
        with pytest.raises(InvalidCoordinatesError):
            crs_service.to_wgs84(Polygon(square(77, 95, 1)), wgs)

    def test_projected_source_is_converted_to_lonlat(self):
        utm = crs_service.resolve(32643)
        geom = Polygon(square(500000, 1435000, 1000))
        out = crs_service.to_wgs84(geom, utm)
        minx, miny, maxx, maxy = out.bounds
        assert 74.9 < minx < 75.1 and 12.9 < miny < 13.1  # 500000 E is the central meridian, 75°E

    def test_same_epsg_projection_is_identity(self):
        utm = crs_service.resolve(32643)
        geom = Polygon(square(500000, 1435000, 1000))
        assert crs_service.to_projected(geom, utm, 32643) is geom

    def test_transformers_are_cached_per_thread(self):
        wgs = crs_service.wgs84
        a = crs_service._transformer(wgs, "EPSG:32643", CRS.from_epsg(32643))
        b = crs_service._transformer(wgs, "EPSG:32643", CRS.from_epsg(32643))
        assert a is b


class TestGeodesic:
    OUTER = [(77.6, 12.96), (77.615, 12.96), (77.615, 12.975), (77.6, 12.975)]
    INNER = [(77.604, 12.964), (77.61, 12.964), (77.61, 12.97), (77.604, 12.97)]

    def test_hole_is_subtracted_regardless_of_ring_orientation(self):
        outer = crs_service.geodesic_area(Polygon(self.OUTER))
        inner = crs_service.geodesic_area(Polygon(self.INNER))
        with_hole = Polygon(self.OUTER, [self.INNER])
        reversed_rings = Polygon(self.OUTER[::-1], [self.INNER[::-1]])
        expected = outer - inner
        assert math.isclose(crs_service.geodesic_area(with_hole), expected, rel_tol=1e-9)
        assert math.isclose(crs_service.geodesic_area(reversed_rings), expected, rel_tol=1e-9)

    def test_geodesic_length_of_one_degree_of_latitude(self):
        from shapely.geometry import LineString

        length = crs_service.geodesic_length(LineString([(77.0, 12.0), (77.0, 13.0)]))
        assert 110_500 < length < 111_000  # ~110.6 km at this latitude
