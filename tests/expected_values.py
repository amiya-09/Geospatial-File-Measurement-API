"""Independent reference values for measurement tests.

Every value here was computed with ``pyproj.Geod(ellps="WGS84")`` only: a
geodesic calculation on the ellipsoid. It deliberately shares no code with the
LAEA/AEQD projection pipeline under test, so agreement between the two is a
real cross-check rather than a tautology.

Units: area in square metres (m^2), length in metres (m).
Coordinates are (lon, lat) in EPSG:4326, rings listed counter-clockwise.

Regenerate with::

    g = Geod(ellps="WGS84")
    g.polygon_area_perimeter(lons, lats)[0]   # area, m^2 (signed; abs() it)
    g.line_length(lons, lats)                 # length, m
"""

# Maximum accepted relative difference between the projected result and the
# reference. Target for the whole suite.
REL_TOLERANCE = 0.001  # 0.1 percent

# 1x1 degree box at the equator.
# Computed: Geod.polygon_area_perimeter([0,1,1,0], [0,0,1,1]) -> 12_308_778_361.47 m^2
# Sanity check: about 12,308 km^2.
BOX_EQUATOR = {
    "coords": [(0, 0), (1, 0), (1, 1), (0, 1)],
    "area_m2": 12_308_778_361.469452,
    "rel_tol": REL_TOLERANCE,
}

# Same 1x1 degree box at 60N. East-west extent shrinks by ~cos(60 deg), so
# roughly half the equatorial area.
# Computed: Geod.polygon_area_perimeter([0,1,1,0], [60,60,61,61]) -> 6_122_943_163.07 m^2
BOX_60N = {
    "coords": [(0, 60), (1, 60), (1, 61), (0, 61)],
    "area_m2": 6_122_943_163.071411,
    "rel_tol": REL_TOLERANCE,
}

# One degree of longitude along the equator.
# Computed: Geod.line_length([0, 1], [0, 0]) -> 111_319.49 m
LINE_EQUATOR_1DEG = {
    "coords": [(0, 0), (1, 0)],
    "length_m": 111_319.49079327357,
    "rel_tol": REL_TOLERANCE,
}

# 10x10 degree square (outer) with a 2x2 degree square hole (inner, 2..4).
# Computed: outer 1_227_877_191_609.63 m^2 (Geod, [0,10,10,0] x [0,0,10,10])
#           hole       49_173_168_695.16 m^2 (Geod, [2,4,4,2] x [2,2,4,4])
#           expected = outer - hole
POLYGON_WITH_HOLE = {
    "exterior": [(0, 0), (10, 0), (10, 10), (0, 10)],
    "hole": [(2, 2), (4, 2), (4, 4), (2, 4)],
    "area_m2": 1_227_877_191_609.6267 - 49_173_168_695.159935,
    "rel_tol": REL_TOLERANCE,
}
