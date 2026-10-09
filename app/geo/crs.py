"""CRS helpers.

All transformers use ``always_xy=True``: coordinates are always (lon, lat) / (x, y),
never the (lat, lon) order EPSG:4326 formally defines.
"""

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)


def _reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, apply)


def to_wgs84(geom: BaseGeometry, source_crs: CRS) -> BaseGeometry:
    """Reproject to EPSG:4326 (lon, lat), dropping Z/M. Always done, even for projected sources."""
    geom = shapely.force_2d(geom)
    transformer = Transformer.from_crs(source_crs, WGS84, always_xy=True)
    return _reproject(geom, transformer)


def local_laea(lon: float, lat: float) -> CRS:
    """Lambert Azimuthal Equal-Area on WGS84, centred on (lon, lat). Used for area."""
    return CRS.from_proj4(f"+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def local_aeqd(lon: float, lat: float) -> CRS:
    """Azimuthal Equidistant on WGS84, centred on (lon, lat). Used for length."""
    return CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def project_local(geom_wgs84: BaseGeometry, kind: str) -> BaseGeometry:
    """Project a WGS84 geometry into a local CRS centred on its own centroid.

    ``kind`` is "laea" (area) or "aeqd" (length).
    """
    builders = {"laea": local_laea, "aeqd": local_aeqd}
    if kind not in builders:
        raise ValueError(f"unknown projection kind: {kind!r}")
    centre = geom_wgs84.centroid
    crs = builders[kind](lon=centre.x, lat=centre.y)
    transformer = Transformer.from_crs(WGS84, crs, always_xy=True)
    return _reproject(geom_wgs84, transformer)
