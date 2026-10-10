"""Parser for ESRI Shapefiles delivered as a ``.zip`` archive.

Design notes
------------
* The archive is read **in memory** (``zipfile`` -> ``BytesIO`` -> pyshp). Nothing is
  extracted to disk, so Zip-Slip is impossible by construction, and the total
  uncompressed size / entry count are capped to stop decompression bombs.
* File discovery is case-insensitive (``.SHP`` is common on Windows/ArcGIS exports) and
  ignores macOS ``__MACOSX/._*`` resource-fork junk.
* A zip may hold several shapefiles (layers). All of them are read; each feature keeps
  its own CRS.
* Features are never silently dropped: null shapes and unparseable shapes are kept
  with a status so feature indexes always line up with the source file.
"""

import codecs
import io
import logging
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import shapefile
from shapely.geometry import shape as shapely_shape

from app.core.exceptions import AppError, FileFormatError, MissingCRSError
from app.services.crs_service import CRSInfo, CRSService, crs_service
from app.services.parsers.base import ParsedDataset, ParsedFeature, ParserLimits, json_safe

logger = logging.getLogger(__name__)

_COMPONENT_EXTS = {"shp", "shx", "dbf", "prj", "cpg"}


class _EncodingError(Exception):
    """The DBF attribute table could not be decoded with the chosen text encoding."""


def _is_decode_error(exc: BaseException) -> bool:
    # pyshp 3.x wraps UnicodeDecodeError in its own dbfFileException ("Could not decode ...").
    return (
        isinstance(exc, UnicodeDecodeError)
        or isinstance(exc.__cause__, UnicodeDecodeError)
        or "Could not decode" in str(exc)
    )


@dataclass
class _Layer:
    name: str
    members: dict[str, zipfile.ZipInfo] = field(default_factory=dict)  # extension -> zip entry


class ShapefileParser:
    file_types = {".zip": "Shapefile (.zip)"}

    def __init__(self, limits: ParserLimits | None = None, crs: CRSService | None = None) -> None:
        self._limits = limits or ParserLimits()
        self._crs = crs or crs_service

    # ------------------------------------------------------------------ public API
    def parse(self, path: Path) -> ParsedDataset:
        if not zipfile.is_zipfile(path):
            raise FileFormatError("Uploaded file is not a valid zip archive.")

        dataset = ParsedDataset(features=[], file_type=self.file_types[".zip"])
        try:
            with zipfile.ZipFile(path) as archive:
                layers = self._discover_layers(archive)
                for layer in layers:
                    self._read_layer(archive, layer, dataset, multi_layer=len(layers) > 1)
        except zipfile.BadZipFile as exc:
            raise FileFormatError(f"Corrupted zip archive: {exc}") from exc
        except (RuntimeError, NotImplementedError) as exc:  # encrypted / exotic compression
            raise FileFormatError(f"Zip archive cannot be read (encrypted or unsupported): {exc}") from exc

        if not dataset.features:
            raise FileFormatError("The shapefile contains no features.")
        return dataset

    # ------------------------------------------------------------------ zip inspection
    def _discover_layers(self, archive: zipfile.ZipFile) -> list[_Layer]:
        entries = archive.infolist()
        if len(entries) > self._limits.max_zip_entries:
            raise FileFormatError(f"Archive has too many entries (limit {self._limits.max_zip_entries}).")
        if sum(e.file_size for e in entries) > self._limits.max_uncompressed_bytes:
            raise FileFormatError("Archive expands beyond the allowed uncompressed size (possible zip bomb).")

        grouped: dict[tuple[str, str], _Layer] = defaultdict(lambda: _Layer(name=""))
        for entry in entries:
            if entry.is_dir():
                continue
            parts = entry.filename.replace("\\", "/").split("/")
            basename = parts[-1]
            if "__MACOSX" in parts or basename.startswith("."):
                continue  # macOS resource forks / hidden files are not real shapefile parts
            stem, dot, ext = basename.rpartition(".")
            ext = ext.lower()
            if not dot or ext not in _COMPONENT_EXTS:
                continue
            layer = grouped[("/".join(parts[:-1]).lower(), stem.lower())]
            layer.name = layer.name or stem
            layer.members[ext] = entry

        layers = [layer for layer in grouped.values() if "shp" in layer.members]
        if not layers:
            raise FileFormatError("Shapefile zip archive does not contain any .shp file.")
        return sorted(layers, key=lambda layer: layer.name.lower())

    # ------------------------------------------------------------------ layer reading
    def _read_layer(self, archive: zipfile.ZipFile, layer: _Layer, dataset: ParsedDataset, multi_layer: bool) -> None:
        encoding, lenient = self._declared_encoding(archive, layer)
        start = len(dataset.features)
        try:
            features, warnings = self._load_features(archive, layer, encoding, lenient, start, multi_layer)
        except _EncodingError:
            # No usable .cpg and the attribute table is not UTF-8: retry as latin-1 (never fails).
            features, warnings = self._load_features(archive, layer, "latin-1", True, start, multi_layer)
            warnings.append(f"Layer '{layer.name}': attribute table is not valid UTF-8; decoded as latin-1.")
        dataset.features.extend(features)
        dataset.warnings.extend(warnings)

    @staticmethod
    def _open(archive: zipfile.ZipFile, layer: _Layer, ext: str) -> io.BytesIO | None:
        entry = layer.members.get(ext)
        return io.BytesIO(archive.read(entry)) if entry else None

    def _load_features(
        self,
        archive: zipfile.ZipFile,
        layer: _Layer,
        encoding: str,
        lenient: bool,
        start_index: int,
        multi_layer: bool,
    ) -> tuple[list[ParsedFeature], list[str]]:
        warnings: list[str] = []
        dbf = self._open(archive, layer, "dbf")
        if dbf is None:
            warnings.append(f"Layer '{layer.name}': no .dbf file, attributes are empty.")

        try:
            reader = shapefile.Reader(
                shp=self._open(archive, layer, "shp"),
                shx=self._open(archive, layer, "shx"),
                dbf=dbf,
                encoding=encoding,
                encodingErrors="replace" if lenient else "strict",
            )
        except Exception as exc:
            if _is_decode_error(exc):
                raise _EncodingError from exc
            raise FileFormatError(f"Layer '{layer.name}' is not a readable shapefile: {exc}") from exc

        features: list[ParsedFeature] = []
        with reader:
            crs = self._layer_crs(archive, layer, reader, warnings)
            try:
                records = (
                    ((sr.shape, sr.record.as_dict()) for sr in reader.iterShapeRecords())
                    if dbf is not None
                    else ((s, {}) for s in reader.iterShapes())
                )
                for local_index, (shp_shape, attributes) in enumerate(records):
                    if start_index + len(features) >= self._limits.max_features:
                        raise FileFormatError(f"Too many features (limit {self._limits.max_features}).")
                    geometry, error = self._build_geometry(shp_shape)
                    features.append(
                        ParsedFeature(
                            index=start_index + len(features),
                            feature_id=f"{layer.name}/{local_index}" if multi_layer else str(local_index),
                            geometry=geometry,
                            properties={str(k): json_safe(v) for k, v in attributes.items()},
                            crs=crs,
                            parse_error=error,
                        )
                    )
            except AppError:
                raise
            except Exception as exc:  # pyshp raises KeyError/struct.error/... on corrupt input
                if _is_decode_error(exc):
                    raise _EncodingError from exc
                raise FileFormatError(f"Layer '{layer.name}' is corrupted ({type(exc).__name__}: {exc}).") from exc
        return features, warnings

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _declared_encoding(archive: zipfile.ZipFile, layer: _Layer) -> tuple[str, bool]:
        """(encoding, lenient). A .cpg file names the DBF encoding; otherwise try UTF-8 strictly."""
        entry = layer.members.get("cpg")
        if entry is None:
            return "utf-8", False
        text = archive.read(entry).decode("ascii", errors="ignore").strip()
        for candidate in (text, f"cp{text}"):  # e.g. "UTF-8", or a bare code page "1252"
            try:
                return codecs.lookup(candidate).name, True
            except LookupError:
                continue
        return "utf-8", False

    def _layer_crs(
        self, archive: zipfile.ZipFile, layer: _Layer, reader: shapefile.Reader, warnings: list[str]
    ) -> CRSInfo:
        prj_entry = layer.members.get("prj")
        if prj_entry is not None:
            text = archive.read(prj_entry).decode("utf-8", errors="ignore").strip()
            try:
                return self._crs.resolve(text)
            except AppError as exc:
                warnings.append(f"Layer '{layer.name}': unreadable .prj ({exc.message})")

        # No (usable) CRS declaration. Assume WGS-84 only when the coordinates can really be lon/lat;
        # guessing for projected-looking coordinates would silently produce wrong areas.
        bbox = reader.bbox
        bounds = (bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax) if bbox is not None else (0, 0, 0, 0)
        if self._crs.looks_geographic(bounds):
            warnings.append(
                f"Layer '{layer.name}': no usable .prj; coordinates look like lon/lat, so EPSG:4326 was assumed."
            )
            return self._crs.wgs84
        raise MissingCRSError(
            f"Layer '{layer.name}' has no usable .prj and its coordinates "
            f"{tuple(round(v, 2) for v in bounds)} are not valid longitude/latitude values, so the "
            "CRS cannot be assumed. Add the .prj file to the archive."
        )

    @staticmethod
    def _build_geometry(shp_shape: shapefile.Shape):
        """Return (geometry, error). ``(None, None)`` means a legitimately null shape."""
        if shp_shape.shapeType == shapefile.NULL:
            return None, None
        try:
            return shapely_shape(shp_shape.__geo_interface__), None
        except Exception as exc:
            return None, f"Shape could not be converted to a geometry: {exc}"
