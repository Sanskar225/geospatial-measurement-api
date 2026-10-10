"""Shared types and helpers for the file parsers."""

import datetime as dt
import math
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from shapely.geometry.base import BaseGeometry

from app.services.crs_service import CRSInfo


@dataclass(frozen=True)
class ParserLimits:
    """Hard caps that protect the service from decompression bombs / huge files."""

    max_features: int = 250_000
    max_uncompressed_bytes: int = 250 * 1024 * 1024
    max_zip_entries: int = 500


@dataclass
class ParsedFeature:
    index: int  # zero-based position in the file
    feature_id: str
    geometry: BaseGeometry | None  # None when the geometry could not be built
    properties: dict[str, Any]
    crs: CRSInfo
    parse_error: str | None = None  # why ``geometry`` is None (kept so nothing is silently dropped)


@dataclass
class ParsedDataset:
    features: list[ParsedFeature]
    file_type: str
    warnings: list[str] = field(default_factory=list)

    @property
    def crs(self) -> str:
        """Single CRS identifier, or ``MIXED`` when layers use different CRSs."""
        identifiers = {f.crs.identifier for f in self.features}
        if len(identifiers) == 1:
            return identifiers.pop()
        return "MIXED" if identifiers else "EPSG:4326"


class GeospatialParser(Protocol):
    #: extension -> human readable file type, e.g. {".kml": "KML", ".kmz": "KMZ"}
    file_types: dict[str, str]

    def parse(self, path: Path) -> ParsedDataset: ...


def json_safe(value: Any) -> Any:
    """Coerce an attribute value into something ``json.dumps`` accepts.

    Shapefile DBF columns can yield ``date`` objects, ``Decimal``, ``bytes`` and ``NaN`` -
    all of which would otherwise crash serialisation (or produce invalid JSON).
    """
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    # NUL bytes are legal in DBF text but rejected by PostgreSQL JSON/text columns.
    return str(value).replace("\x00", "")
