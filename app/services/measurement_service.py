from typing import Optional, Tuple, Dict, Any
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid
from shapely.geometry import mapping
from app.models.schemas import (
    FeatureMeasurement,
    MeasurementDetail,
    MeasurementUnit,
    AdvancedGISMetrics,
)
from app.services.crs_service import crs_service

class MeasurementService:
    """
    Enterprise Measurement Engine.
    Calculates projected planar measurements (Area, Length, Perimeter)
    alongside ellipsoidal geodesic measurements and projection distortion metrics.
    Also produces WGS-84 geometry for RFC 7946 compliance and web map rendering.
    """

    @staticmethod
    def _count_vertices(geom: BaseGeometry) -> int:
        try:
            if hasattr(geom, "geoms"):
                return sum(MeasurementService._count_vertices(g) for g in geom.geoms)
            if hasattr(geom, "exterior"):
                count = len(geom.exterior.coords)
                for hole in geom.interiors:
                    count += len(hole.coords)
                return count
            if hasattr(geom, "coords"):
                return len(geom.coords)
        except Exception:
            pass
        return 0

    def process_feature(
        self,
        feature_id: str,
        geom: BaseGeometry,
        properties: dict,
        source_crs_str: str,
    ) -> FeatureMeasurement:
        geom_type = geom.geom_type
        source_crs = crs_service.parse_crs(source_crs_str)
        
        # Native GeoJSON mapping
        try:
            geo_json_dict = mapping(geom)
        except Exception:
            geo_json_dict = {"type": geom_type, "coordinates": []}

        # WGS-84 GeoJSON mapping (for web maps if source is projected)
        try:
            wgs84_geom = crs_service.to_wgs84(geom, source_crs)
            geo_json_wgs84 = mapping(wgs84_geom) if source_crs.is_projected else None
        except Exception:
            geo_json_wgs84 = None

        # Point geometries (Zero-dimensional)
        if geom_type in ("Point", "MultiPoint"):
            return FeatureMeasurement(
                feature_id=feature_id,
                geometry_type=geom_type,
                geometry=geo_json_dict,
                geometry_wgs84=geo_json_wgs84,
                crs=source_crs_str,
                projected_crs=None,
                properties=properties,
                measurement=MeasurementDetail(
                    measurement_type="none",
                    value=None,
                    unit=MeasurementUnit.NONE,
                    formatted="N/A (Points have zero dimension)",
                    alternative_units={},
                    advanced_metrics=AdvancedGISMetrics(
                        geodesic_value=None,
                        distortion_percentage=None,
                        perimeter_meters=None,
                        vertex_count=self._count_vertices(geom),
                    ),
                ),
                status="SUCCESS",
                notes="Point geometry does not possess area or length. Handled gracefully.",
            )

        # Polygon geometries (Area calculation)
        if geom_type in ("Polygon", "MultiPolygon"):
            return self._calculate_polygon_measurement(
                feature_id=feature_id,
                geom=geom,
                geom_type=geom_type,
                geo_json_dict=geo_json_dict,
                geo_json_wgs84=geo_json_wgs84,
                properties=properties,
                source_crs_str=source_crs_str,
            )

        # LineString geometries (Length calculation)
        if geom_type in ("LineString", "MultiLineString"):
            return self._calculate_linestring_measurement(
                feature_id=feature_id,
                geom=geom,
                geom_type=geom_type,
                geo_json_dict=geo_json_dict,
                geo_json_wgs84=geo_json_wgs84,
                properties=properties,
                source_crs_str=source_crs_str,
            )

        # Graceful handling for other/unsupported geometries
        return FeatureMeasurement(
            feature_id=feature_id,
            geometry_type=geom_type,
            geometry=geo_json_dict,
            geometry_wgs84=geo_json_wgs84,
            crs=source_crs_str,
            projected_crs=None,
            properties=properties,
            measurement=MeasurementDetail(
                measurement_type="none",
                value=None,
                unit=MeasurementUnit.NONE,
                formatted="Unsupported geometry type",
                alternative_units={},
            ),
            status="UNSUPPORTED_GEOMETRY",
            notes=f"Geometry type '{geom_type}' is not supported for measurement calculation. Handled gracefully without failure.",
        )

    def _calculate_polygon_measurement(
        self,
        feature_id: str,
        geom: BaseGeometry,
        geom_type: str,
        geo_json_dict: dict,
        geo_json_wgs84: Optional[dict],
        properties: dict,
        source_crs_str: str,
    ) -> FeatureMeasurement:
        try:
            if not geom.is_valid:
                geom = make_valid(geom)

            source_crs = crs_service.parse_crs(source_crs_str)
            projected_geom, proj_crs_name = crs_service.transform_to_projected(
                geom, source_crs
            )

            # Planar area & perimeter in projected metric CRS
            area_sq_m = round(float(projected_geom.area), 2)
            perimeter_m = round(float(projected_geom.length), 2)
            hectares = round(area_sq_m / 10000.0, 4)
            sq_km = round(area_sq_m / 1000000.0, 6)
            acres = round(area_sq_m * 0.000247105, 4)

            # Ellipsoidal Geodesic Area
            # Calculate geodesic over WGS-84 geometry if source was projected, else over geom
            wgs84_for_geod = crs_service.to_wgs84(geom, source_crs) if source_crs.is_projected else geom
            geodesic_area = crs_service.calculate_geodesic_polygon_area(wgs84_for_geod)
            distortion_pct = 0.0
            if geodesic_area > 0:
                distortion_pct = round(abs(area_sq_m - geodesic_area) / geodesic_area * 100.0, 4)

            formatted = f"{area_sq_m:,.2f} m² ({hectares:,.4f} ha | {sq_km:,.4f} km²)"

            return FeatureMeasurement(
                feature_id=feature_id,
                geometry_type=geom_type,
                geometry=geo_json_dict,
                geometry_wgs84=geo_json_wgs84,
                crs=source_crs_str,
                projected_crs=proj_crs_name,
                properties=properties,
                measurement=MeasurementDetail(
                    measurement_type="area",
                    value=area_sq_m,
                    unit=MeasurementUnit.SQUARE_METERS,
                    formatted=formatted,
                    alternative_units={
                        "hectares": hectares,
                        "square_kilometers": sq_km,
                        "acres": acres,
                        "perimeter_meters": perimeter_m,
                    },
                    advanced_metrics=AdvancedGISMetrics(
                        geodesic_value=round(geodesic_area, 2),
                        distortion_percentage=distortion_pct,
                        perimeter_meters=perimeter_m,
                        vertex_count=self._count_vertices(geom),
                    ),
                ),
                status="SUCCESS",
                notes="Area calculated via metric projected CRS transformation with WGS-84 geodesic verification.",
            )
        except Exception as ex:
            return FeatureMeasurement(
                feature_id=feature_id,
                geometry_type=geom_type,
                geometry=geo_json_dict,
                geometry_wgs84=geo_json_wgs84,
                crs=source_crs_str,
                projected_crs=None,
                properties=properties,
                measurement=None,
                status="CALCULATION_ERROR",
                notes=f"Error projecting or calculating polygon area: {str(ex)}",
            )

    def _calculate_linestring_measurement(
        self,
        feature_id: str,
        geom: BaseGeometry,
        geom_type: str,
        geo_json_dict: dict,
        geo_json_wgs84: Optional[dict],
        properties: dict,
        source_crs_str: str,
    ) -> FeatureMeasurement:
        try:
            source_crs = crs_service.parse_crs(source_crs_str)
            projected_geom, proj_crs_name = crs_service.transform_to_projected(
                geom, source_crs
            )

            # Planar length in meters
            length_m = round(float(projected_geom.length), 2)
            km = round(length_m / 1000.0, 4)
            feet = round(length_m * 3.28084, 2)

            # Ellipsoidal Geodesic Length
            wgs84_for_geod = crs_service.to_wgs84(geom, source_crs) if source_crs.is_projected else geom
            geodesic_length = crs_service.calculate_geodesic_linestring_length(wgs84_for_geod)
            distortion_pct = 0.0
            if geodesic_length > 0:
                distortion_pct = round(abs(length_m - geodesic_length) / geodesic_length * 100.0, 4)

            formatted = f"{length_m:,.2f} m ({km:,.4f} km | {feet:,.2f} ft)"

            return FeatureMeasurement(
                feature_id=feature_id,
                geometry_type=geom_type,
                geometry=geo_json_dict,
                geometry_wgs84=geo_json_wgs84,
                crs=source_crs_str,
                projected_crs=proj_crs_name,
                properties=properties,
                measurement=MeasurementDetail(
                    measurement_type="length",
                    value=length_m,
                    unit=MeasurementUnit.METERS,
                    formatted=formatted,
                    alternative_units={
                        "kilometers": km,
                        "feet": feet,
                    },
                    advanced_metrics=AdvancedGISMetrics(
                        geodesic_value=round(geodesic_length, 2),
                        distortion_percentage=distortion_pct,
                        perimeter_meters=None,
                        vertex_count=self._count_vertices(geom),
                    ),
                ),
                status="SUCCESS",
                notes="Length calculated via metric projected CRS transformation with WGS-84 geodesic verification.",
            )
        except Exception as ex:
            return FeatureMeasurement(
                feature_id=feature_id,
                geometry_type=geom_type,
                geometry=geo_json_dict,
                geometry_wgs84=geo_json_wgs84,
                crs=source_crs_str,
                projected_crs=None,
                properties=properties,
                measurement=None,
                status="CALCULATION_ERROR",
                notes=f"Error projecting or calculating line length: {str(ex)}",
            )

measurement_service = MeasurementService()
