from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


class ParseError(ValueError):
    """The file cannot be read at all (corrupt or unsafe zip, wrong type, bad XML).

    Maps to a 4xx response with nothing persisted.
    """


class UnmeasurableError(ValueError):
    """The file was read but cannot be measured (e.g. no .prj and coordinates are not lon/lat).

    Maps to a persisted FAILED record and a 422 response that includes the file id.
    """


@dataclass(frozen=True)
class Feature:
    index: int
    source_id: str | None
    geometry: BaseGeometry | None  # in the file's own CRS; None when absent or unreadable
    properties: dict[str, Any]
    warnings: tuple[str, ...] = ()  # why the geometry was dropped; also in the file's warnings


@dataclass
class ParsedFile:
    crs: CRS
    features: list[Feature]
    warnings: list[str] = field(default_factory=list)


class Budget:
    """Running count of vertices or parts for one file; ``take`` raises ParseError once the
    limit is passed.

    Parsers call it with a count taken from the raw input, before building shapely geometry.
    """

    def __init__(self, limit: int, unit: str) -> None:
        self.limit = limit
        self.unit = unit
        self.used = 0

    def take(self, count: int) -> None:
        self.used += count
        if self.used > self.limit:
            raise ParseError(f"file has more than {self.limit} {self.unit}")
