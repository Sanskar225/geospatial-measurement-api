import csv
import io
from typing import List, Dict, Any
from app.models.schemas import FeatureMeasurement, MeasurementsResponse

_DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

def sanitize_csv_cell(value: Any) -> str:
    """Protects against CSV Formula Injection when opened in Excel / Google Sheets."""
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _DANGEROUS_CSV_PREFIXES:
        return "'" + text
    return text

def format_measurements_as_csv(response: MeasurementsResponse) -> str:
    """Formats features and measurements as a CSV table with formula injection defense."""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Header
    writer.writerow([
        "index",
        "feature_id",
        "name",
        "geometry_type",
        "status",
        "measurement_type",
        "value",
        "unit",
        "formatted",
        "perimeter_m",
        "geodesic_value",
        "difference_pct",
        "source_crs",
        "projected_crs",
        "notes"
    ])

    for idx, f in enumerate(response.features):
        m = f.measurement
        adv = m.advanced_metrics if m else None
        
        feature_name = f.properties.get("name") or f.properties.get("NAME") or ""
        val = m.value if m and m.value is not None else ""
        unit = m.unit.value if m else "none"
        fmt = m.formatted if m else "N/A"
        perim = adv.perimeter_meters if adv and adv.perimeter_meters is not None else ""
        geo_val = adv.geodesic_value if adv and adv.geodesic_value is not None else ""
        diff_pct = adv.distortion_percentage if adv and adv.distortion_percentage is not None else ""
        notes = f.notes or ""

        writer.writerow([
            idx,
            sanitize_csv_cell(f.feature_id),
            sanitize_csv_cell(feature_name),
            f.geometry_type,
            f.status,
            m.measurement_type if m else "none",
            val,
            unit,
            sanitize_csv_cell(fmt),
            perim,
            geo_val,
            diff_pct,
            f.crs,
            f.projected_crs or "",
            sanitize_csv_cell(notes),
        ])

    return output.getvalue()

def format_features_as_geojson(response: MeasurementsResponse) -> Dict[str, Any]:
    """Formats features into RFC 7946 compliant GeoJSON FeatureCollection."""
    features = []
    for f in response.features:
        # Use geometry_wgs84 for RFC 7946 WGS-84 compliance if available, else native geometry
        geom = f.geometry_wgs84 if getattr(f, "geometry_wgs84", None) else f.geometry
        features.append({
            "type": "Feature",
            "id": f.feature_id,
            "geometry": geom,
            "properties": {
                **f.properties,
                "feature_id": f.feature_id,
                "geometry_type": f.geometry_type,
                "source_crs": f.crs,
                "projected_crs": f.projected_crs,
                "measurement_type": f.measurement.measurement_type if f.measurement else "none",
                "measurement_value": f.measurement.value if f.measurement else None,
                "measurement_unit": f.measurement.unit.value if f.measurement else "none",
                "formatted_measurement": f.measurement.formatted if f.measurement else "N/A",
                "status": f.status,
            }
        })

    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features
    }
