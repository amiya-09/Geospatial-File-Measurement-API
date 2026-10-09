"""Totality: whatever bytes a parser is given, it returns a ParsedFile or raises one of the two
documented errors. Valid fixtures are truncated and corrupted with a seeded RNG, so any failure
reproduces from its seed and index.

Set TOTALITY_MUTATIONS=20000 for a much longer local run.
"""

import contextlib
import datetime
import os
import random
import signal

import pytest
import shapefile

from app.parsers.base import ParsedFile, ParseError, UnmeasurableError
from app.parsers.kml import parse_kml
from app.parsers.shapefile import parse_shapefile_zip
from tests.fixtures import make_zip, shapefile_parts, shapefile_zip

MUTATIONS = int(os.environ.get("TOTALITY_MUTATIONS", "300"))
SEED = 20261009
PER_PARSE_SECONDS = 5

WGS84_PRJ = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document><name>fixture</name>
 <Folder><name>outer</name>
  <Folder><name>inner</name>
   <Placemark id="p1"><name>pt</name><description>a point</description>
    <ExtendedData><Data name="owner"><value>Ada</value></Data>
     <SchemaData schemaUrl="#s"><SimpleData name="zone">R2</SimpleData></SchemaData></ExtendedData>
    <Point><coordinates>10.5,50.25,120</coordinates></Point></Placemark>
   <Placemark><name>line</name><LineString>
    <coordinates>0,0 1,0 1,1 2,2</coordinates></LineString></Placemark>
  </Folder>
  <Placemark><name>poly</name><Polygon><outerBoundaryIs><LinearRing>
    <coordinates>0,0 1,0 1,1 0,1 0,0</coordinates></LinearRing></outerBoundaryIs>
    <innerBoundaryIs><LinearRing><coordinates>0.4,0.4 0.6,0.4 0.6,0.6 0.4,0.6 0.4,0.4</coordinates>
    </LinearRing></innerBoundaryIs></Polygon></Placemark>
 </Folder>
 <Placemark><MultiGeometry><Point><coordinates>1,1</coordinates></Point>
  <LineString><coordinates>3,3 4,4</coordinates></LineString></MultiGeometry></Placemark>
 <Placemark><name>ring</name><LinearRing>
  <coordinates>0,0 1,0 1,1 0,0</coordinates></LinearRing></Placemark>
 <Placemark><name>nothing</name></Placemark>
 <Placemark><gx:Track/></Placemark>
</Document></kml>""".encode()

FIELDS = (
    ("name", "C", 40, 0),
    ("n", "N", 10, 0),
    ("f", "N", 12, 3),
    ("ok", "L", 1, 0),
    ("day", "D", 8, 0),
)


def _attrs(i):
    return [f"feature {i} café", i, i / 4, i % 2 == 0, datetime.date(2024, 1, 1 + i)]


def _polygons(w):
    w.poly(
        [
            [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)],
            [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6), (0.4, 0.4)],
        ]
    )
    w.record(*_attrs(0))
    w.poly([[(5, 5), (5, 6), (6, 6), (6, 5), (5, 5)], [(8, 8), (8, 9), (9, 9), (9, 8), (8, 8)]])
    w.record(*_attrs(1))
    w.null()
    w.record(*_attrs(2))


def _lines(w):
    w.line([[(0, 0), (1, 0), (1, 1)], [(5, 5), (6, 5)]])
    w.record(*_attrs(0))
    w.line([[(2, 2), (3, 3)]])
    w.record(*_attrs(1))


def _points(w):
    for i in range(4):
        w.point(i, i)
        w.record(*_attrs(i))


def _pointz(w):
    w.pointz(1, 2, 3)
    w.record(*_attrs(0))
    w.pointz(4, 5, float("nan"))
    w.record(*_attrs(1))


SHAPEFILES = {
    "polygons": shapefile_parts(_polygons, shapefile.POLYGON, fields=FIELDS),
    "lines": shapefile_parts(_lines, shapefile.POLYLINE, fields=FIELDS),
    "points": shapefile_parts(_points, shapefile.POINT, fields=FIELDS),
    "pointz": shapefile_parts(_pointz, shapefile.POINTZ, fields=FIELDS),
}


def mutate(data: bytes, rng: random.Random) -> bytes:
    buf = bytearray(data)
    kind = rng.choice(["truncate", "flip", "flip", "insert", "delete", "overwrite"])
    if kind == "truncate":
        return bytes(buf[: rng.randrange(0, len(buf))])
    if kind == "flip":
        for _ in range(rng.randint(1, 8)):
            buf[rng.randrange(len(buf))] = rng.randrange(256)
    elif kind == "insert":
        at = rng.randrange(len(buf))
        buf[at:at] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
    elif kind == "delete":
        at = rng.randrange(len(buf))
        del buf[at : at + rng.randint(1, 16)]
    else:
        at = rng.randrange(len(buf))
        span = rng.randint(1, 32)
        buf[at : at + span] = bytes(rng.randrange(256) for _ in range(min(span, len(buf) - at)))
    return bytes(buf)


@contextlib.contextmanager
def time_limit(seconds: int):
    def on_alarm(signum, frame):
        raise TimeoutError(f"parser ran longer than {seconds}s")

    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def assert_total(parse, data: bytes, label: str) -> str:
    """Run one parse; return the outcome name. Anything unexpected fails with a repro label."""
    try:
        with time_limit(PER_PARSE_SECONDS):
            result = parse(data)
    except (ParseError, UnmeasurableError) as exc:
        return type(exc).__name__
    except BaseException as exc:  # noqa: BLE001 - the point of this test
        pytest.fail(f"{label}: unexpected {type(exc).__name__}: {exc}")
    assert isinstance(result, ParsedFile), label
    return "ParsedFile"


def _zip_of(parts: dict[str, bytes]) -> bytes:
    return shapefile_zip(parts, prj=WGS84_PRJ, cpg="UTF-8")


def test_the_unmutated_fixtures_parse():
    assert len(parse_kml(KML).features) == 7
    for name, parts in SHAPEFILES.items():
        assert parse_shapefile_zip(_zip_of(parts)).features, name


@pytest.mark.filterwarnings("ignore::shapefile.PossiblyCorruptFileHeader")
@pytest.mark.filterwarnings("ignore::shapefile.PossibleDataLoss")
def test_mutated_kml_is_always_a_parsed_file_or_a_documented_error():
    rng = random.Random(SEED)
    outcomes = {}
    for i in range(MUTATIONS):
        data = mutate(KML, rng)
        outcome = assert_total(parse_kml, data, f"kml seed={SEED} i={i}")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    assert set(outcomes) <= {"ParsedFile", "ParseError", "UnmeasurableError"}


@pytest.mark.filterwarnings("ignore::shapefile.PossiblyCorruptFileHeader")
@pytest.mark.filterwarnings("ignore::shapefile.PossibleDataLoss")
def test_mutated_shapefile_zips_are_always_a_parsed_file_or_a_documented_error():
    rng = random.Random(SEED + 1)
    base = _zip_of(SHAPEFILES["polygons"])
    for i in range(MUTATIONS):
        assert_total(parse_shapefile_zip, mutate(base, rng), f"zip seed={SEED + 1} i={i}")


@pytest.mark.filterwarnings("ignore::shapefile.PossiblyCorruptFileHeader")
@pytest.mark.filterwarnings("ignore::shapefile.PossibleDataLoss")
def test_mutated_shapefile_members_inside_a_valid_zip_are_always_total():
    # Corrupting the zip mostly fails the zip layer; corrupt the members and re-zip them so
    # the pyshp / attribute / .prj paths are exercised too.
    rng = random.Random(SEED + 2)
    names = list(SHAPEFILES)
    for i in range(MUTATIONS):
        parts = dict(SHAPEFILES[names[i % len(names)]])
        members = {**parts, "prj": WGS84_PRJ.encode(), "cpg": b"UTF-8"}
        victim = rng.choice(sorted(members))
        members[victim] = mutate(members[victim], rng)
        zipped = make_zip({f"data.{ext}": data for ext, data in members.items()})
        assert_total(parse_shapefile_zip, zipped, f"members seed={SEED + 2} i={i} victim={victim}")


# Regression cases first found by the mutation runs above.


def test_xml_declaration_naming_an_unknown_encoding_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_kml(b'<?xml version="1.0" encoding="UTF-R"?><kml/>')


def test_cpg_containing_a_nul_byte_is_ignored_with_a_warning():
    parts = shapefile_parts(
        lambda w: (w.point(1, 1), w.record(*_attrs(0))), shapefile.POINT, fields=FIELDS
    )
    parsed = parse_shapefile_zip(
        shapefile_zip(parts, prj=WGS84_PRJ, extra={"data.cpg": b"UTF\x008"})
    )
    assert any("unrecognised encoding" in w for w in parsed.warnings)


def _patch_central_header(zip_bytes: bytes, offset: int, value: bytes) -> bytes:
    data = bytearray(zip_bytes)
    pos = data.find(b"PK\x01\x02")
    data[pos + offset : pos + offset + len(value)] = value
    return bytes(data)


def test_zip_with_an_unsupported_version_is_a_parse_error():
    base = _zip_of(SHAPEFILES["points"])
    with pytest.raises(ParseError):
        parse_shapefile_zip(_patch_central_header(base, 6, b"\x99\x00"))  # version needed


def test_zip_whose_directory_offset_points_past_its_real_position_is_a_parse_error():
    # Makes zipfile compute a negative header offset ("negative seek value -3") on open().
    data = bytearray(_zip_of(SHAPEFILES["points"]))
    eocd = data.rfind(b"PK\x05\x06")
    offset = int.from_bytes(data[eocd + 16 : eocd + 20], "little")
    data[eocd + 16 : eocd + 20] = (offset + 3).to_bytes(4, "little")
    with pytest.raises(ParseError):
        parse_shapefile_zip(bytes(data))
