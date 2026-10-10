"""Coordinate reference system handling.

Strategy (see README -> "CRS handling" for the reasoning):

1. Every source CRS is resolved to a :class:`CRSInfo` (never silently defaulted).
2. Planar measurements are *always* taken in a local metric projection chosen from the
   feature's location (UTM zone, or UPS near the poles) - even when the source CRS is
   already projected. ``is_projected`` does not mean "safe for area": a projected CRS may
   use feet (EPSG:2229) or heavily distort area (Web Mercator inflates it by 1/cos²(lat)).
3. The same measurement is repeated on the WGS-84 ellipsoid (``pyproj.Geod``) as a
   ground-truth check, and the difference is reported.
"""

import math
import threading
from dataclasses import dataclass, field

import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from pyproj.exceptions import CRSError as PyprojCRSError
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from app.core.exceptions import (
    CRSTransformationError,
    InvalidCoordinatesError,
    InvalidCRSError,
)

# Polar limits: UTM is defined between 80°S and 84°N. Beyond that use UPS.
UTM_MAX_LAT = 84.0
UTM_MIN_LAT = -80.0
UPS_NORTH_EPSG = 32661
UPS_SOUTH_EPSG = 32761


@dataclass(frozen=True)
class CRSInfo:
    """A resolved CRS plus the bits of metadata the rest of the app needs."""

    identifier: str  # "EPSG:4326", "ESRI:102100", or "CUSTOM:<name>"
    name: str
    wkt: str = field(repr=False)
    is_geographic: bool = False
    is_projected: bool = False
    is_wgs84: bool = False
    crs: CRS = field(repr=False, compare=False, default=None)  # type: ignore[assignment]

    @property
    def epsg(self) -> int | None:
        scheme, _, code = self.identifier.partition(":")
        return int(code) if scheme == "EPSG" and code.isdigit() else None


_WGS84_CRS = CRS.from_epsg(4326)
_local = threading.local()  # pyproj Transformers are cheap to reuse but not shared across threads


class CRSService:
    """Stateless helpers around pyproj/shapely. Safe to share between threads."""

    def __init__(self) -> None:
        self._geod = Geod(ellps="WGS84")
        self.wgs84 = self.resolve(_WGS84_CRS)

    # ------------------------------------------------------------------ resolution
    def resolve(self, definition: "str | int | CRS") -> CRSInfo:
        """Turn WKT / PROJ string / EPSG code / CRS into a :class:`CRSInfo`.

        Raises :class:`InvalidCRSError` instead of silently falling back to WGS-84 -
        a wrong guess here would produce wrong areas without any error.
        """
        try:
            crs = definition if isinstance(definition, CRS) else CRS.from_user_input(definition)
        except (PyprojCRSError, TypeError, ValueError) as exc:
            raise InvalidCRSError(f"Unrecognised CRS definition: {exc}") from exc

        if not (crs.is_geographic or crs.is_projected):
            raise InvalidCRSError(
                f"CRS '{crs.name}' is neither geographic nor projected, so it cannot be georeferenced for measurement."
            )

        authority = crs.to_authority(min_confidence=70)
        identifier = f"{authority[0]}:{authority[1]}" if authority else f"CUSTOM:{crs.name}"[:255]
        return CRSInfo(
            identifier=identifier,
            name=crs.name or identifier,
            wkt=crs.to_wkt(),
            is_geographic=crs.is_geographic,
            is_projected=crs.is_projected,
            is_wgs84=crs.is_geographic and crs.equals(_WGS84_CRS, ignore_axis_order=True),
            crs=crs,
        )

    @staticmethod
    def looks_geographic(bounds: tuple[float, float, float, float]) -> bool:
        """True when (minx, miny, maxx, maxy) fits inside a lon/lat range."""
        minx, miny, maxx, maxy = bounds
        return all(math.isfinite(v) for v in bounds) and (
            -180.0 <= minx <= maxx <= 180.0 and -90.0 <= miny <= maxy <= 90.0
        )

    # ------------------------------------------------------------------ projection choice
    @staticmethod
    def select_projected_epsg(lon: float, lat: float) -> int:
        """EPSG code of the metric CRS best suited to measure around (lon, lat).

        UTM zone for -80° <= lat <= 84°, UPS beyond (UTM is undefined at the poles).
        Includes the Norway (32V) and Svalbard (31X-37X) zone exceptions.
        """
        if lat > UTM_MAX_LAT:
            return UPS_NORTH_EPSG
        if lat < UTM_MIN_LAT:
            return UPS_SOUTH_EPSG

        lon = ((lon + 180.0) % 360.0) - 180.0  # wrap into [-180, 180)
        zone = int((lon + 180.0) // 6) + 1
        if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
            zone = 32  # southwest Norway
        elif 72.0 <= lat <= UTM_MAX_LAT:  # Svalbard
            for lo, hi, z in ((0, 9, 31), (9, 21, 33), (21, 33, 35), (33, 42, 37)):
                if lo <= lon < hi:
                    zone = z
                    break
        return (32600 if lat >= 0 else 32700) + zone

    @staticmethod
    def describe_projection(epsg: int) -> str:
        if epsg == UPS_NORTH_EPSG:
            return "UPS North"
        if epsg == UPS_SOUTH_EPSG:
            return "UPS South"
        return f"UTM zone {epsg % 100}{'N' if epsg < 32700 else 'S'}"

    # ------------------------------------------------------------------ transforms
    @staticmethod
    def _transformer(src: CRSInfo, dst_key: str, dst: CRS) -> Transformer:
        cache = getattr(_local, "cache", None)
        if cache is None:
            cache = _local.cache = {}
        key = (src.wkt, dst_key)
        if key not in cache:
            cache[key] = Transformer.from_crs(src.crs, dst, always_xy=True)
        return cache[key]

    def _apply(self, geom: BaseGeometry, transformer: Transformer, target: str) -> BaseGeometry:
        try:
            out = shapely.transform(geom, transformer.transform, interleaved=False)
        except Exception as exc:  # pyproj raises ProjError for out-of-domain input
            raise CRSTransformationError(f"Could not transform geometry to {target}: {exc}") from exc
        if not np.isfinite(out.bounds).all():
            raise InvalidCoordinatesError(
                f"Coordinates could not be transformed to {target}. The declared CRS probably "
                "does not match the actual coordinates."
            )
        return out

    def to_wgs84(self, geom: BaseGeometry, src: CRSInfo) -> BaseGeometry:
        """Geometry in lon/lat degrees (WGS-84), validated to be inside the valid range."""
        out = geom if src.is_wgs84 else self._apply(geom, self._transformer(src, "EPSG:4326", _WGS84_CRS), "EPSG:4326")
        if not self.looks_geographic(out.bounds):
            raise InvalidCoordinatesError(
                "Coordinates fall outside valid longitude/latitude ranges "
                f"(bounds={tuple(round(v, 6) for v in out.bounds)}). "
                f"Check that the file really uses {src.identifier}."
            )
        return out

    def to_projected(self, geom: BaseGeometry, src: CRSInfo, epsg: int) -> BaseGeometry:
        """Geometry in the metric CRS ``epsg`` (coordinates in metres)."""
        key = f"EPSG:{epsg}"
        if src.epsg == epsg:
            return geom
        return self._apply(geom, self._transformer(src, key, CRS.from_epsg(epsg)), key)

    # ------------------------------------------------------------------ geodesic ground truth
    @staticmethod
    def _polygons(geom: BaseGeometry) -> list[Polygon]:
        if isinstance(geom, Polygon):
            return [geom]
        if isinstance(geom, MultiPolygon) or hasattr(geom, "geoms"):
            return [p for g in geom.geoms for p in CRSService._polygons(g)]
        return []

    def geodesic_area(self, geom_wgs84: BaseGeometry) -> float:
        """Area (m²) on the WGS-84 ellipsoid.

        Rings are re-oriented first: pyproj returns a *signed* area and only subtracts holes
        when exterior rings are counter-clockwise and holes clockwise. Without ``orient`` a
        polygon with a hole reports exterior + hole instead of exterior - hole.
        """
        total = 0.0
        for polygon in self._polygons(geom_wgs84):
            area, _ = self._geod.geometry_area_perimeter(orient(polygon, sign=1.0))
            total += abs(area)
        return total

    def geodesic_length(self, geom_wgs84: BaseGeometry) -> float:
        """Length (m) on the WGS-84 ellipsoid."""
        return float(self._geod.geometry_length(geom_wgs84))


crs_service = CRSService()
