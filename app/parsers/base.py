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


@dataclass
class ParsedFile:
    crs: CRS
    features: list[Feature]
    warnings: list[str] = field(default_factory=list)
