"""Parser registry: maps a file extension to the parser that can read it.

Adding a new format (GeoJSON, GeoPackage, DXF ...) means writing one class that
implements ``GeospatialParser`` and registering it in ``build_registry``.
"""

from app.core.exceptions import UnsupportedFileTypeError
from app.services.parsers.base import GeospatialParser, ParsedDataset, ParsedFeature, ParserLimits
from app.services.parsers.kml_parser import KMLParser
from app.services.parsers.shapefile_parser import ShapefileParser

__all__ = [
    "ParserRegistry",
    "ParsedDataset",
    "ParsedFeature",
    "ParserLimits",
    "build_registry",
]


class ParserRegistry:
    def __init__(self, parsers: list[GeospatialParser]) -> None:
        self._by_extension = {ext: parser for parser in parsers for ext in parser.file_types}

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset(self._by_extension)

    def file_type_for(self, extension: str) -> str:
        return self.for_extension(extension).file_types[extension.lower()]

    def for_extension(self, extension: str) -> GeospatialParser:
        try:
            return self._by_extension[extension.lower()]
        except KeyError:
            supported = ", ".join(sorted(self._by_extension))
            raise UnsupportedFileTypeError(
                f"Unsupported file extension '{extension}'. Supported: {supported} (.zip must contain a Shapefile)."
            ) from None


def build_registry(limits: ParserLimits | None = None) -> ParserRegistry:
    limits = limits or ParserLimits()
    return ParserRegistry([ShapefileParser(limits), KMLParser(limits)])
