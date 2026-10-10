"""Measurement engine.

For every parsed feature this module produces a :class:`FeatureMeasurement`:

========================  =====================================================
Geometry                  Result
========================  =====================================================
Polygon / MultiPolygon    area (m²) + perimeter, planar (UTM/UPS) and geodesic
LineString / MultiLine    length (m), planar (UTM/UPS) and geodesic
Point / MultiPoint        no measurement (status SUCCESS)
anything else             status UNSUPPORTED_GEOMETRY, never an exception
========================  =====================================================

A failure on one feature is recorded on that feature only; it never aborts the file.
"""

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity, make_valid

from app.core.exceptions import AppError, InvalidCoordinatesError
from app.models.schemas import (
    AdvancedGISMetrics,
    FeatureMeasurement,
    FeatureStatus,
    MeasurementDetail,
    MeasurementsSummary,
    MeasurementType,
    MeasurementUnit,
)
from app.services.crs_service import CRSService, crs_service
from app.services.parsers.base import ParsedFeature

logger = logging.getLogger(__name__)

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString"}
POINTAL = {"Point", "MultiPoint"}

ACRES_PER_M2 = 0.0002471053814671653
FEET_PER_M = 3.280839895013123

_FAILED_STATUSES = {
    FeatureStatus.EMPTY_GEOMETRY,
    FeatureStatus.INVALID_GEOMETRY,
    FeatureStatus.INVALID_COORDINATES,
    FeatureStatus.CALCULATION_ERROR,
}


def format_area(square_meters: float) -> str:
    return f"{square_meters:,.2f} m² ({square_meters / 10_000:,.4f} ha | {square_meters / 1_000_000:,.4f} km²)"


def format_length(meters: float) -> str:
    return f"{meters:,.2f} m ({meters / 1000:,.4f} km | {meters * FEET_PER_M:,.2f} ft)"


def _to_geojson(geom: BaseGeometry) -> dict:
    return json.loads(shapely.to_geojson(geom))


def _polygons_of(geom: BaseGeometry) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in _polygons_of(g)]
    return []


@dataclass
class _Ctx:
    """Everything known about one feature while it is being measured."""

    feature: ParsedFeature
    geometry: BaseGeometry  # as parsed (source CRS)
    geometry_type: str
    geojson: dict  # source CRS
    flat: BaseGeometry  # geometry forced to 2D (source CRS)
    geographic: BaseGeometry | None = None  # lon/lat, None if it could not be derived
    geographic_error: AppError | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def wgs84_geojson(self) -> dict | None:
        """Only emitted when it differs from the source geometry (i.e. source is not WGS-84)."""
        if self.geographic is None or self.feature.crs.is_wgs84:
            return None
        return _to_geojson(self.geographic)


class MeasurementService:
    def __init__(self, crs: CRSService | None = None, distortion_warning_pct: float = 0.5) -> None:
        self._crs = crs or crs_service
        self._distortion_warning_pct = distortion_warning_pct

    # ------------------------------------------------------------------ public API
    def measure(self, feature: ParsedFeature) -> FeatureMeasurement:
        geom = feature.geometry
        if geom is None:  # nothing to measure; explain why instead of dropping the feature
            reason = feature.parse_error or "Feature has no geometry (null shape)."
            status = FeatureStatus.INVALID_GEOMETRY if feature.parse_error else FeatureStatus.EMPTY_GEOMETRY
            return self._empty_result(feature, status, reason)
        if geom.is_empty:
            return self._empty_result(
                feature, FeatureStatus.EMPTY_GEOMETRY, "Geometry has no coordinates.", geom.geom_type
            )

        ctx = _Ctx(
            feature=feature,
            geometry=geom,
            geometry_type=geom.geom_type,
            geojson=_to_geojson(geom),
            flat=shapely.force_2d(geom),
        )
        try:
            ctx.geographic = self._crs.to_wgs84(ctx.flat, feature.crs)
        except AppError as exc:  # reported below, but only for geometry we actually measure
            ctx.geographic_error = exc

        try:
            if ctx.geometry_type in POINTAL:
                return self._point(ctx)
            if ctx.geometry_type in POLYGONAL:
                return self._polygonal(ctx)
            if ctx.geometry_type in LINEAR:
                return self._linear(ctx)
            return self._unsupported(ctx)
        except InvalidCoordinatesError as exc:
            return self._result(ctx, FeatureStatus.INVALID_COORDINATES, notes=[exc.message])
        except AppError as exc:
            return self._result(ctx, FeatureStatus.CALCULATION_ERROR, notes=[exc.message])
        except Exception:  # last line of defence: one bad feature must not fail the whole file
            logger.exception("Unexpected error measuring feature %s", feature.feature_id)
            return self._result(
                ctx, FeatureStatus.CALCULATION_ERROR, notes=["Unexpected error while measuring this feature."]
            )

    def summarize(self, results: Iterable[FeatureMeasurement]) -> MeasurementsSummary:
        summary = MeasurementsSummary()
        area = length = 0.0
        measured_polygons = measured_lines = 0
        for result in results:
            summary.total_features += 1
            if result.geometry_type in POLYGONAL:
                summary.polygon_count += 1
            elif result.geometry_type in LINEAR:
                summary.linestring_count += 1
            elif result.geometry_type in POINTAL:
                summary.point_count += 1
            if result.status == FeatureStatus.UNSUPPORTED_GEOMETRY:
                summary.unsupported_count += 1
            elif result.status in _FAILED_STATUSES:
                summary.failed_count += 1

            measurement = result.measurement
            if result.status == FeatureStatus.SUCCESS and measurement and measurement.value is not None:
                if measurement.measurement_type == MeasurementType.AREA:
                    area += measurement.value
                    measured_polygons += 1
                elif measurement.measurement_type == MeasurementType.LENGTH:
                    length += measurement.value
                    measured_lines += 1

        summary.total_area_sq_meters = round(area, 2)
        summary.total_area_hectares = round(area / 10_000, 4)
        summary.total_length_meters = round(length, 2)
        summary.total_length_kilometers = round(length / 1000, 4)
        if measured_polygons:
            summary.average_polygon_area_sq_m = round(area / measured_polygons, 2)
        if measured_lines:
            summary.average_linestring_length_m = round(length / measured_lines, 2)
        return summary

    # ------------------------------------------------------------------ per geometry family
    def _require_geographic(self, ctx: _Ctx) -> BaseGeometry:
        if ctx.geographic_error is not None:
            raise ctx.geographic_error
        assert ctx.geographic is not None
        return ctx.geographic

    def _point(self, ctx: _Ctx) -> FeatureMeasurement:
        self._require_geographic(ctx)  # still validated, so a mis-declared CRS is caught early
        return self._result(
            ctx,
            FeatureStatus.SUCCESS,
            measurement=MeasurementDetail(
                measurement_type=MeasurementType.NONE,
                formatted="N/A (points have no length or area)",
                advanced_metrics=AdvancedGISMetrics(vertex_count=shapely.get_num_coordinates(ctx.geometry)),
            ),
        )

    def _polygonal(self, ctx: _Ctx) -> FeatureMeasurement:
        geographic = self._require_geographic(ctx)
        working = ctx.flat
        if not working.is_valid:
            reason = explain_validity(working)
            repaired = _polygons_of(make_valid(working))
            if not repaired:
                raise InvalidCoordinatesError(f"Invalid polygon ({reason}) collapses to nothing after repair.")
            working = repaired[0] if len(repaired) == 1 else MultiPolygon(repaired)
            geographic = self._crs.to_wgs84(working, ctx.feature.crs)
            ctx.notes.append(f"Invalid polygon ({reason}) was repaired with make_valid() before measuring.")

        epsg = self._projection_for(geographic)
        projected = self._crs.to_projected(working, ctx.feature.crs, epsg)
        planar = float(projected.area)
        perimeter = float(projected.length)
        geodesic = self._crs.geodesic_area(geographic)
        difference = self._difference_pct(planar, geodesic)
        self._maybe_warn(ctx, difference, epsg)

        measurement = MeasurementDetail(
            measurement_type=MeasurementType.AREA,
            value=round(planar, 2),
            unit=MeasurementUnit.SQUARE_METERS,
            formatted=format_area(planar),
            alternative_units={
                "hectares": round(planar / 10_000, 4),
                "square_kilometers": round(planar / 1_000_000, 6),
                "acres": round(planar * ACRES_PER_M2, 4),
            },
            advanced_metrics=AdvancedGISMetrics(
                geodesic_value=round(geodesic, 2),
                distortion_percentage=difference,
                perimeter_meters=round(perimeter, 2),
                vertex_count=shapely.get_num_coordinates(ctx.geometry),
            ),
        )
        return self._result(ctx, FeatureStatus.SUCCESS, projected_crs=f"EPSG:{epsg}", measurement=measurement)

    def _linear(self, ctx: _Ctx) -> FeatureMeasurement:
        geographic = self._require_geographic(ctx)
        epsg = self._projection_for(geographic)
        projected = self._crs.to_projected(ctx.flat, ctx.feature.crs, epsg)
        planar = float(projected.length)
        geodesic = self._crs.geodesic_length(geographic)
        difference = self._difference_pct(planar, geodesic)
        self._maybe_warn(ctx, difference, epsg)

        measurement = MeasurementDetail(
            measurement_type=MeasurementType.LENGTH,
            value=round(planar, 2),
            unit=MeasurementUnit.METERS,
            formatted=format_length(planar),
            alternative_units={
                "kilometers": round(planar / 1000, 4),
                "feet": round(planar * FEET_PER_M, 2),
            },
            advanced_metrics=AdvancedGISMetrics(
                geodesic_value=round(geodesic, 2),
                distortion_percentage=difference,
                vertex_count=shapely.get_num_coordinates(ctx.geometry),
            ),
        )
        return self._result(ctx, FeatureStatus.SUCCESS, projected_crs=f"EPSG:{epsg}", measurement=measurement)

    def _unsupported(self, ctx: _Ctx) -> FeatureMeasurement:
        parts = shapely.get_parts(ctx.geometry) if hasattr(ctx.geometry, "geoms") else []
        members = sorted({g.geom_type for g in parts})
        detail = f" (contains {', '.join(members)})" if members else ""
        return self._result(
            ctx,
            FeatureStatus.UNSUPPORTED_GEOMETRY,
            measurement=MeasurementDetail(measurement_type=MeasurementType.NONE, formatted="Unsupported geometry type"),
            notes=[
                f"Geometry type '{ctx.geometry_type}'{detail} is not measured. "
                "Supported: Polygon/MultiPolygon (area), LineString/MultiLineString (length)."
            ],
        )

    # ------------------------------------------------------------------ helpers
    def _projection_for(self, geographic: BaseGeometry) -> int:
        """EPSG of the metric CRS for the centre of the geometry's bounding box."""
        minx, miny, maxx, maxy = geographic.bounds
        return self._crs.select_projected_epsg((minx + maxx) / 2, (miny + maxy) / 2)

    @staticmethod
    def _difference_pct(planar: float, geodesic: float) -> float | None:
        if geodesic <= 0:
            return None
        return round(abs(planar - geodesic) / geodesic * 100, 4)

    def _maybe_warn(self, ctx: _Ctx, difference: float | None, epsg: int) -> None:
        if difference is not None and difference > self._distortion_warning_pct:
            ctx.notes.append(
                f"Planar value ({self._crs.describe_projection(epsg)}) differs from the geodesic value "
                f"by {difference}%. The feature is large, spans several UTM zones or lies near a pole; "
                "prefer the geodesic value."
            )

    @staticmethod
    def _result(
        ctx: _Ctx,
        status: FeatureStatus,
        *,
        projected_crs: str | None = None,
        measurement: MeasurementDetail | None = None,
        notes: list[str] | None = None,
    ) -> FeatureMeasurement:
        feature = ctx.feature
        return FeatureMeasurement(
            feature_id=feature.feature_id,
            index=feature.index,
            geometry_type=ctx.geometry_type,
            geometry=ctx.geojson,
            geometry_wgs84=ctx.wgs84_geojson,
            crs=feature.crs.identifier,
            projected_crs=projected_crs,
            properties=feature.properties,
            measurement=measurement,
            status=status,
            notes=ctx.notes + (notes or []),
        )

    @staticmethod
    def _empty_result(
        feature: ParsedFeature, status: FeatureStatus, reason: str, geometry_type: str = "Unknown"
    ) -> FeatureMeasurement:
        return FeatureMeasurement(
            feature_id=feature.feature_id,
            index=feature.index,
            geometry_type=geometry_type,
            crs=feature.crs.identifier,
            properties=feature.properties,
            status=status,
            notes=[reason],
        )


measurement_service = MeasurementService()
