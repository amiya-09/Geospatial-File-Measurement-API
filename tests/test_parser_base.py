from pyproj import CRS
from shapely.geometry import Point

from app.config import MAX_UNCOMPRESSED_BYTES, MAX_UPLOAD_BYTES, MAX_ZIP_ENTRIES
from app.parsers.base import Feature, ParsedFile, ParseError, UnmeasurableError


def test_parsed_file_bundles_crs_features_and_warnings():
    feature = Feature(index=0, source_id=None, geometry=Point(1, 2), properties={"a": 1})
    parsed = ParsedFile(crs=CRS.from_epsg(4326), features=[feature], warnings=["w"])
    assert parsed.features[0].geometry.x == 1
    assert parsed.warnings == ["w"]
    assert parsed.crs.to_epsg() == 4326


def test_warnings_default_to_an_empty_list_per_instance():
    a = ParsedFile(crs=CRS.from_epsg(4326), features=[])
    b = ParsedFile(crs=CRS.from_epsg(4326), features=[])
    a.warnings.append("x")
    assert b.warnings == []


def test_failure_classes_are_distinct():
    # unreadable files persist nothing; readable-but-unmeasurable files persist a FAILED record
    assert not issubclass(UnmeasurableError, ParseError)
    assert not issubclass(ParseError, UnmeasurableError)


def test_limits_are_sane_positive_numbers():
    assert 0 < MAX_ZIP_ENTRIES
    assert 0 < MAX_UPLOAD_BYTES < MAX_UNCOMPRESSED_BYTES
