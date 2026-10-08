import pytest
from pyproj import CRS
from shapely.geometry import Polygon, LineString, Point
from app.services.crs_service import crs_service

def test_utm_zone_calculation():
    # Bangalore / India (Lon: 77.59, Lat: 12.97) -> Zone 43N
    assert crs_service.get_optimal_utm_epsg(77.59, 12.97) == 32643

    # London / UK (Lon: -0.12, Lat: 51.5) -> Zone 30N
    assert crs_service.get_optimal_utm_epsg(-0.12, 51.5) == 32630

    # Sydney / Australia (Lon: 151.2, Lat: -33.86) -> Zone 56S
    assert crs_service.get_optimal_utm_epsg(151.2, -33.86) == 32756

def test_parse_crs():
    crs_4326 = crs_service.parse_crs("EPSG:4326")
    assert crs_4326.to_epsg() == 4326

    # Fallback on invalid
    crs_fallback = crs_service.parse_crs("INVALID_CRS_STRING_123")
    assert crs_fallback.to_epsg() == 4326

def test_geometry_projection_to_meters():
    # Polygon ~ 1km x 1km near Bangalore in EPSG:4326
    # 0.01 deg is roughly 1.1 km
    poly_degrees = Polygon([
        (77.58, 12.97),
        (77.59, 12.97),
        (77.59, 12.98),
        (77.58, 12.98),
        (77.58, 12.97)
    ])
    
    # In geographic degrees, geom.area is ~0.0001 (NOT METERS!)
    assert poly_degrees.area < 1.0

    # Transform to projected UTM
    source_crs = CRS.from_epsg(4326)
    proj_geom, proj_name = crs_service.transform_to_projected(poly_degrees, source_crs)

    assert "32643" in proj_name
    # Projected area should be in square meters (approx 1.1 km * 1.1 km = ~1.2 million m²)
    assert proj_geom.area > 100000.0
    assert proj_geom.area < 2000000.0

def test_geodesic_polygon_area_verification():
    poly_degrees = Polygon([
        (77.58, 12.97),
        (77.59, 12.97),
        (77.59, 12.98),
        (77.58, 12.98),
        (77.58, 12.97)
    ])
    geodesic_area = crs_service.calculate_geodesic_polygon_area(poly_degrees)
    assert geodesic_area > 100000.0
