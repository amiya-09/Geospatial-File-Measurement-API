import io
import zipfile

import pytest

from app.parsers.base import ParseError
from app.parsers.kml import parse_kml

KML_NS = "http://www.opengis.net/kml/2.2"


def kml(body: str = "", ns: bool = True) -> bytes:
    root = f'<kml xmlns="{KML_NS}">' if ns else "<kml>"
    return f'<?xml version="1.0" encoding="UTF-8"?>{root}<Document>{body}</Document></kml>'.encode()


def test_minimal_kml_parses_to_wgs84_with_no_features():
    parsed = parse_kml(kml())
    assert parsed.crs.to_epsg() == 4326
    assert parsed.features == []
    assert parsed.warnings == []


def test_kml_without_namespace_is_accepted():
    assert parse_kml(kml(ns=False)).features == []


def test_utf8_bom_is_accepted():
    assert parse_kml(b"\xef\xbb\xbf" + kml()).features == []


def test_empty_file_is_rejected():
    with pytest.raises(ParseError, match="empty"):
        parse_kml(b"")


def test_whitespace_only_file_is_rejected():
    with pytest.raises(ParseError):
        parse_kml(b"  \n\t ")


def test_binary_file_renamed_to_kml_is_rejected():
    with pytest.raises(ParseError):
        parse_kml(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def test_zip_renamed_to_kml_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", kml())
    with pytest.raises(ParseError):
        parse_kml(buf.getvalue())


def test_well_formed_xml_without_a_kml_root_is_rejected():
    with pytest.raises(ParseError, match="kml"):
        parse_kml(b"<html><body>hi</body></html>")


def test_truncated_xml_is_rejected():
    with pytest.raises(ParseError):
        parse_kml(kml()[:-20])


def test_entity_expansion_bomb_is_rejected():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><kml><Document>&b;</Document></kml>'
    )
    with pytest.raises(ParseError):
        parse_kml(bomb)


def test_external_entity_is_rejected_and_not_resolved():
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<kml><Document><name>&x;</name></Document></kml>"
    )
    with pytest.raises(ParseError):
        parse_kml(xxe)


# ---- nesting depth ----

PLACEMARK = "<Placemark><Point><coordinates>1,1</coordinates></Point></Placemark>"


def nested(tag: str, depth: int, inner: str) -> str:
    return f"<{tag}>" * depth + inner + f"</{tag}>" * depth


def test_deeply_nested_folders_are_rejected_cleanly():
    data = kml(nested("Folder", 10_000, PLACEMARK))
    with pytest.raises(ParseError, match="nested"):
        parse_kml(data)


def test_deeply_nested_multigeometry_is_rejected_cleanly_not_with_recursion_error():
    point = "<Point><coordinates>1,1</coordinates></Point>"
    data = kml(f"<Placemark>{nested('MultiGeometry', 10_000, point)}</Placemark>")
    with pytest.raises(ParseError, match="nested"):
        parse_kml(data)


def test_nesting_up_to_the_limit_is_accepted_and_one_more_is_not():
    # <kml><Document> take 2 levels, then the folders, then <Placemark><Point><coordinates>
    folders = 50 - 2 - 3
    assert len(parse_kml(kml(nested("Folder", folders, PLACEMARK))).features) == 1
    with pytest.raises(ParseError, match="nested"):
        parse_kml(kml(nested("Folder", folders + 1, PLACEMARK)))


def test_max_depth_is_configurable():
    data = kml(nested("Folder", 5, PLACEMARK))
    assert len(parse_kml(data, max_depth=20).features) == 1
    with pytest.raises(ParseError, match="nested"):
        parse_kml(data, max_depth=8)
