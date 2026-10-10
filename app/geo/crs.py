"""CRS helpers.

All transformers use ``always_xy=True``: coordinates are always (lon, lat) / (x, y),
never the (lat, lon) order EPSG:4326 formally defines.
"""

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)


def crs_label(crs: CRS) -> str:
    """ "EPSG:<code>" when pyproj resolves one, otherwise the CRS name."""
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.name


def _reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, apply)


def wgs84_transformer(source_crs: CRS) -> Transformer:
    """Transformer from ``source_crs`` to EPSG:4326 (lon, lat).

    Building one can take about 30 ms (a .prj without an authority code), so callers build it
    once per file and pass it to ``to_wgs84`` for every feature.
    """
    return Transformer.from_crs(source_crs, WGS84, always_xy=True)


def to_wgs84(
    geom: BaseGeometry, source_crs: CRS, transformer: Transformer | None = None
) -> BaseGeometry:
    """Reproject to EPSG:4326 (lon, lat), dropping Z/M. Always done, even for projected sources.

    ``transformer`` is ``wgs84_transformer(source_crs)``, built once by the caller; without it
    one is built for this call.
    """
    geom = shapely.force_2d(geom)
    if transformer is None:
        transformer = wgs84_transformer(source_crs)
    return _reproject(geom, transformer)


def _normalise_lon(lon: float) -> float:
    """Wrap a longitude into [-180, 180) (unwrapped features can have centroids above 180)."""
    return (lon + 180.0) % 360.0 - 180.0


def local_laea(lon: float, lat: float) -> CRS:
    """Lambert Azimuthal Equal-Area on WGS84, centred on (lon, lat). Used for area."""
    lon = _normalise_lon(lon)
    return CRS.from_proj4(f"+proj=laea +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def local_aeqd(lon: float, lat: float) -> CRS:
    """Azimuthal Equidistant on WGS84, centred on (lon, lat). Used for length."""
    lon = _normalise_lon(lon)
    return CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs")


def project_local(geom_wgs84: BaseGeometry, kind: str) -> tuple[BaseGeometry, CRS]:
    """Project a WGS84 geometry into a local CRS centred on its own centroid.

    ``kind`` is "laea" (area) or "aeqd" (length). Returns the projected geometry and the
    CRS used.
    """
    builders = {"laea": local_laea, "aeqd": local_aeqd}
    if kind not in builders:
        raise ValueError(f"unknown projection kind: {kind!r}")
    centre = geom_wgs84.centroid
    crs = builders[kind](lon=centre.x, lat=centre.y)
    transformer = Transformer.from_crs(WGS84, crs, always_xy=True)
    return _reproject(geom_wgs84, transformer), crs
