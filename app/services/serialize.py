import json
import math
from typing import Any

import shapely
from shapely.geometry.base import BaseGeometry


def json_safe(value: Any) -> Any:
    """Make a value strictly JSON-serialisable: NaN/inf become null, odd types become strings."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return str(value)


def to_geojson(geom: BaseGeometry) -> dict[str, Any]:
    return json.loads(shapely.to_geojson(geom))
