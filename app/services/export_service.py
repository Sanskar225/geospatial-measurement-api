"""Pure formatting helpers for the export endpoint (no I/O, easy to unit test)."""

import csv
import io
import json
from collections.abc import AsyncIterator, Iterable

from app.models.schemas import FeatureMeasurement

CSV_COLUMNS = [
    "index",
    "feature_id",
    "name",
    "geometry_type",
    "status",
    "measurement_type",
    "value",
    "unit",
    "perimeter_m",
    "geodesic_value",
    "difference_pct",
    "source_crs",
    "projected_crs",
    "notes",
]

# Characters that make Excel/LibreOffice treat a cell as a formula (CSV injection).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> object:
    """Neutralise spreadsheet formulas in *text* cells; numbers pass through untouched."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def feature_to_csv_row(feature: FeatureMeasurement) -> list[object]:
    m = feature.measurement
    metrics = m.advanced_metrics if m else None
    return [
        feature.index,
        csv_safe(feature.feature_id),
        csv_safe(str(feature.properties.get("name", "") or "")),
        feature.geometry_type,
        feature.status.value,
        m.measurement_type.value if m else "",
        m.value if m and m.value is not None else "",
        m.unit.value if m else "",
        metrics.perimeter_meters if metrics and metrics.perimeter_meters is not None else "",
        metrics.geodesic_value if metrics and metrics.geodesic_value is not None else "",
        metrics.distortion_percentage if metrics and metrics.distortion_percentage is not None else "",
        feature.crs,
        feature.projected_crs or "",
        csv_safe(" | ".join(feature.notes)),
    ]


def feature_to_geojson(feature: FeatureMeasurement) -> dict:
    """RFC 7946 Feature: geometry is always lon/lat WGS-84; measurements ride along as properties."""
    m = feature.measurement
    metrics = m.advanced_metrics if m else None
    properties = {
        **feature.properties,
        "feature_id": feature.feature_id,
        "feature_status": feature.status.value,
        "source_crs": feature.crs,
        "projected_crs": feature.projected_crs,
        "measure_type": m.measurement_type.value if m else None,
        "measure_value": m.value if m else None,
        "measure_unit": m.unit.value if m else None,
        "measure_geodesic": metrics.geodesic_value if metrics else None,
        "measure_difference_pct": metrics.distortion_percentage if metrics else None,
    }
    return {
        "type": "Feature",
        "id": feature.index,
        "geometry": feature.geometry_wgs84 or feature.geometry,
        "properties": properties,
    }


async def stream_geojson(batches: AsyncIterator[list[FeatureMeasurement]]) -> AsyncIterator[str]:
    yield '{"type":"FeatureCollection","features":['
    first = True
    async for batch in batches:
        chunk = ",".join(json.dumps(feature_to_geojson(f)) for f in batch)
        yield chunk if first else "," + chunk
        first = False
    yield "]}"


def _csv_chunk(rows: Iterable[list[object]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


async def stream_csv(batches: AsyncIterator[list[FeatureMeasurement]]) -> AsyncIterator[str]:
    yield _csv_chunk([CSV_COLUMNS])
    async for batch in batches:
        yield _csv_chunk(feature_to_csv_row(f) for f in batch)
