"""Parser output feeds the Stage A pipeline (source CRS -> WGS84 -> measure) correctly."""

import pytest
import shapefile
from pyproj import CRS, Geod, Transformer

from app.geo.crs import to_wgs84
from app.geo.measure import measure
from app.parsers.kml import parse_kml
from app.parsers.shapefile import parse_shapefile_zip
from tests import expected_values as ev
from tests.fixtures import shapefile_parts, shapefile_zip
from tests.test_kml_content import kml


def test_kml_polygon_with_hole_measures_to_the_reference_area():
    ring = " ".join(
        f"{x},{y}"
        for x, y in [*ev.POLYGON_WITH_HOLE["exterior"], ev.POLYGON_WITH_HOLE["exterior"][0]]
    )
    hole = " ".join(
        f"{x},{y}" for x, y in [*ev.POLYGON_WITH_HOLE["hole"], ev.POLYGON_WITH_HOLE["hole"][0]]
    )
    body = (
        "<Placemark><Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{ring}</coordinates></LinearRing></outerBoundaryIs>"
        f"<innerBoundaryIs><LinearRing><coordinates>{hole}</coordinates></LinearRing>"
        "</innerBoundaryIs></Polygon></Placemark>"
    )
    parsed = parse_kml(kml(body))
    feature = parsed.features[0]
    area = measure(to_wgs84(feature.geometry, parsed.crs)).area_m2
    assert area == pytest.approx(
        ev.POLYGON_WITH_HOLE["area_m2"], rel=ev.POLYGON_WITH_HOLE["rel_tol"]
    )


def test_utm_shapefile_measures_like_the_geodesic_area_of_the_same_lon_lat_box():
    utm = CRS.from_epsg(32632)
    fwd = Transformer.from_crs(4326, utm, always_xy=True)
    lonlat = [(9.0, 45.0), (9.1, 45.0), (9.1, 45.1), (9.0, 45.1)]
    projected = [fwd.transform(x, y) for x, y in lonlat]
    clockwise = [projected[0], projected[3], projected[2], projected[1], projected[0]]

    parts = shapefile_parts(lambda w: (w.poly([clockwise]), w.record("utm")), shapefile.POLYGON)
    parsed = parse_shapefile_zip(shapefile_zip(parts, prj=utm.to_wkt()))

    area = measure(to_wgs84(parsed.features[0].geometry, parsed.crs)).area_m2
    lons, lats = zip(*lonlat, strict=True)
    expected = abs(Geod(ellps="WGS84").polygon_area_perimeter(lons, lats)[0])
    assert area == pytest.approx(expected, rel=0.001)
