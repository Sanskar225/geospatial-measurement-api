import math
from typing import Tuple, Optional
import pyproj
from pyproj import CRS, Transformer, Geod
from shapely.geometry.base import BaseGeometry
from app.core.exceptions import CRSTransformationError

class CRSService:
    """
    Enterprise-grade Coordinate Reference System (CRS) management engine.
    
    Handles:
    - Auto-detection of Geographic vs Projected CRS
    - Dynamic calculation of optimal local UTM zone based on feature centroid
    - Reprojection to WGS-84 (EPSG:4326) for RFC 7946 GeoJSON and web map display
    - Robust coordinate transformation using PROJ
    - Geodesic fallback validation via WGS84 ellipsoid
    """

    def __init__(self):
        self.wgs84_geod = Geod(ellps="WGS84")
        self.wgs84_crs = CRS.from_epsg(4326)

    @staticmethod
    def parse_crs(crs_input: Optional[str]) -> CRS:
        """
        Safely parses WKT, Proj string, or EPSG code into a pyproj.CRS object.
        Defaults to EPSG:4326 if not specified or invalid.
        """
        if not crs_input or not crs_input.strip():
            return CRS.from_epsg(4326)
        
        try:
            return CRS.from_user_input(crs_input)
        except Exception:
            return CRS.from_epsg(4326)

    @staticmethod
    def get_optimal_utm_epsg(lon: float, lat: float) -> int:
        """
        Calculates the optimal UTM Zone EPSG code for a given longitude & latitude.
        UTM divides the Earth into 60 zones, each 6 degrees of longitude wide.
        - Northern Hemisphere: EPSG: 32601 to 32660
        - Southern Hemisphere: EPSG: 32701 to 32760
        """
        lon = max(-180.0, min(180.0, lon))
        zone_number = int(math.floor((lon + 180.0) / 6.0)) + 1
        zone_number = max(1, min(60, zone_number))
        
        if lat >= 0:
            return 32600 + zone_number
        else:
            return 32700 + zone_number

    @staticmethod
    def are_coordinates_geographic(bounds: Tuple[float, float, float, float]) -> bool:
        """Checks if bounding coordinates (minx, miny, maxx, maxy) fit inside [-180, 180] and [-90, 90]."""
        minx, miny, maxx, maxy = bounds
        return -180.0 <= minx <= 180.0 and -180.0 <= maxx <= 180.0 and -90.0 <= miny <= 90.0 and -90.0 <= maxy <= 90.0

    def get_projected_crs_for_geometry(self, geom: BaseGeometry, source_crs: CRS) -> Tuple[CRS, str]:
        """
        Determines the most accurate projected metric CRS for a geometry.
        If the source CRS is already projected, returns it directly.
        If geographic (degrees, e.g. EPSG:4326), calculates the local UTM zone from centroid.
        """
        if source_crs.is_projected:
            return source_crs, source_crs.to_string()
        
        try:
            centroid = geom.centroid
            lon, lat = centroid.x, centroid.y
        except Exception:
            bounds = geom.bounds
            lon = (bounds[0] + bounds[2]) / 2.0
            lat = (bounds[1] + bounds[3]) / 2.0

        utm_epsg = self.get_optimal_utm_epsg(lon, lat)
        projected_crs = CRS.from_epsg(utm_epsg)
        utm_name = f"EPSG:{utm_epsg} (UTM Zone {utm_epsg % 100}{'N' if lat >= 0 else 'S'})"
        return projected_crs, utm_name

    def transform_to_projected(
        self, geom: BaseGeometry, source_crs: CRS
    ) -> Tuple[BaseGeometry, str]:
        """
        Transforms a geometry from its source CRS to an optimal projected metric CRS.
        Returns the transformed geometry in meters and the projection description.
        """
        if source_crs.is_projected:
            return geom, source_crs.to_string()

        projected_crs, crs_name = self.get_projected_crs_for_geometry(geom, source_crs)

        try:
            transformer = Transformer.from_crs(
                source_crs, projected_crs, always_xy=True
            )
            import shapely
            projected_geom = shapely.transform(geom, transformer.transform, interleaved=False)
            return projected_geom, crs_name
        except Exception as e:
            raise CRSTransformationError(f"Failed to project geometry to {crs_name}: {str(e)}")

    def to_wgs84(self, geom: BaseGeometry, source_crs: CRS) -> BaseGeometry:
        """
        Transforms a geometry to WGS-84 (EPSG:4326) coordinates for map display and RFC 7946 GeoJSON.
        If already in EPSG:4326, returns the geometry directly.
        """
        if source_crs.is_geographic and getattr(source_crs, "to_epsg", lambda: None)() == 4326:
            return geom
        try:
            transformer = Transformer.from_crs(source_crs, self.wgs84_crs, always_xy=True)
            import shapely
            return shapely.transform(geom, transformer.transform, interleaved=False)
        except Exception:
            return geom

    def calculate_geodesic_polygon_area(self, geom: BaseGeometry) -> float:
        """
        Calculates exact geodesic area over WGS84 ellipsoid directly from geographic coordinates.
        Used for verification and comparison against planar projected measurements.
        """
        try:
            area, _ = self.wgs84_geod.geometry_area_perimeter(geom)
            return abs(float(area))
        except Exception:
            return 0.0

    def calculate_geodesic_linestring_length(self, geom: BaseGeometry) -> float:
        """
        Calculates exact geodesic length over WGS84 ellipsoid.
        """
        try:
            length = self.wgs84_geod.geometry_length(geom)
            return float(length)
        except Exception:
            return 0.0

crs_service = CRSService()
