import io
import zipfile

import pytest
import shapefile

from app.parsers.base import ParseError
from app.parsers.shapefile import read_zip_members
from tests.fixtures import (
    forge_uncompressed_size,
    make_zip,
    mark_entries_encrypted,
    shapefile_parts,
    shapefile_zip,
)


@pytest.fixture(scope="module")
def parts():
    return shapefile_parts(lambda w: (w.point(1, 2), w.record("a")), shapefile.POINT)


def test_valid_zip_returns_the_three_required_members(parts):
    members = read_zip_members(shapefile_zip(parts))
    assert members["shp"] == parts["shp"]
    assert members["shx"] == parts["shx"]
    assert members["dbf"] == parts["dbf"]
    assert members["prj"] is None and members["cpg"] is None


def test_prj_and_cpg_are_returned_when_present(parts):
    members = read_zip_members(shapefile_zip(parts, prj="GEOGCS[...]", cpg="UTF-8"))
    assert members["prj"] == b"GEOGCS[...]"
    assert members["cpg"] == b"UTF-8"


def test_names_are_matched_case_insensitively(parts):
    zipped = make_zip(
        {"DATA.SHP": parts["shp"], "Data.Shx": parts["shx"], "data.DBF": parts["dbf"]}
    )
    assert read_zip_members(zipped)["shp"] == parts["shp"]


def test_shapefile_inside_a_subfolder_is_found(parts):
    zipped = make_zip({f"survey/data.{ext}": data for ext, data in parts.items()})
    assert read_zip_members(zipped)["dbf"] == parts["dbf"]


def test_macosx_junk_is_ignored_even_if_it_looks_like_a_second_shapefile(parts):
    junk = {f"__MACOSX/._data.{ext}": b"junk" for ext in ("shp", "shx", "dbf")}
    junk["__MACOSX/data.shp"] = b"junk"
    zipped = shapefile_zip(parts, extra=junk)
    assert read_zip_members(zipped)["shp"] == parts["shp"]


def test_unrelated_files_and_directory_entries_are_ignored(parts):
    zipped = shapefile_zip(parts, extra={"README.txt": b"hello", "docs/": b""})
    assert read_zip_members(zipped)["shp"] == parts["shp"]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
        b"<kml><Document/></kml>",
        b"PK\x03\x04" + b"garbage" * 20,  # right signature, not a zip
        b"PK\x05\x06" + b"\x00" * 18,  # a valid but empty zip: wrong leading signature
    ],
)
def test_content_that_is_not_a_zip_is_rejected(data):
    with pytest.raises(ParseError):
        read_zip_members(data)


def test_truncated_zip_is_rejected(parts):
    with pytest.raises(ParseError):
        read_zip_members(shapefile_zip(parts)[:-30])


@pytest.mark.parametrize(
    "name",
    [
        "../evil.shp",
        "a/../../evil.dbf",
        "/etc/evil.shp",
        "..\\evil.shx",
        "C:\\evil.shp",
        "x/..\\..\\y",
    ],
)
def test_zip_slip_entry_names_are_rejected(parts, name):
    with pytest.raises(ParseError, match="unsafe"):
        read_zip_members(shapefile_zip(parts, extra={name: b"x"}))


def test_zip_slip_in_a_member_name_is_rejected(parts):
    zipped = make_zip(
        {"../data.shp": parts["shp"], "data.shx": parts["shx"], "data.dbf": parts["dbf"]}
    )
    with pytest.raises(ParseError, match="unsafe"):
        read_zip_members(zipped)


def test_too_many_entries_is_rejected(parts):
    zipped = shapefile_zip(parts, extra={f"f{i}.txt": b"x" for i in range(10)})
    with pytest.raises(ParseError, match="entries"):
        read_zip_members(zipped, max_entries=5)


def test_uncompressed_size_over_the_cap_is_rejected_before_decompressing(parts):
    bomb = shapefile_zip(parts, extra={"zeros.bin": b"\x00" * 5_000_000})
    assert len(bomb) < 100_000  # compresses to almost nothing
    with pytest.raises(ParseError, match="uncompressed"):
        read_zip_members(bomb, max_uncompressed=1_000_000)


def test_compressed_size_over_the_cap_is_rejected(parts):
    with pytest.raises(ParseError, match="too large"):
        read_zip_members(shapefile_zip(parts), max_compressed=100)


@pytest.mark.parametrize("missing", ["shx", "dbf", "shp"])
def test_missing_required_member_is_rejected(parts, missing):
    present = {ext: data for ext, data in parts.items() if ext != missing}
    with pytest.raises(ParseError, match=missing):
        read_zip_members(shapefile_zip(present))


def test_members_split_across_folders_do_not_count_as_one_shapefile(parts):
    zipped = make_zip(
        {"a/data.shp": parts["shp"], "b/data.shx": parts["shx"], "b/data.dbf": parts["dbf"]}
    )
    with pytest.raises(ParseError, match="shx"):
        read_zip_members(zipped)


def test_multiple_shapefiles_are_rejected(parts):
    zipped = make_zip(
        {f"{stem}.{ext}": data for stem in ("roads", "parcels") for ext, data in parts.items()}
    )
    with pytest.raises(ParseError, match="multiple"):
        read_zip_members(zipped)


def test_duplicate_member_names_differing_only_by_case_are_rejected(parts):
    zipped = make_zip({**{f"data.{e}": d for e, d in parts.items()}, "DATA.SHP": parts["shp"]})
    with pytest.raises(ParseError, match="duplicate"):
        read_zip_members(zipped)


def test_encrypted_entries_are_rejected(parts):
    with pytest.raises(ParseError, match="encrypted"):
        read_zip_members(mark_entries_encrypted(shapefile_zip(parts)))


def test_zip_with_no_shapefile_is_rejected():
    with pytest.raises(ParseError, match="shp"):
        read_zip_members(make_zip({"doc.kml": b"<kml/>", "notes.txt": b"hi"}))


def test_forged_small_declared_size_cannot_smuggle_a_large_entry(parts):
    # Header claims 100 bytes; the deflate stream really holds 5 MB. The declared-size
    # pre-check passes (100 < cap), so only reading can catch it.
    honest = shapefile_zip(parts, extra={"zeros.bin": b"\x00" * 5_000_000})
    forged = forge_uncompressed_size(honest, 100)
    declared = sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(forged)).infolist())
    assert declared < 1_000_000  # the pre-check really is fooled
    with pytest.raises(ParseError):
        read_zip_members(forged, max_uncompressed=1_000_000)


def test_zip_bound_is_enforced_on_bytes_actually_read_not_on_the_header(parts, monkeypatch):
    # Simulate a decompressor that ignores the header and keeps producing data, so the
    # result cannot depend on zipfile's own size handling.
    zipped = shapefile_zip(parts)  # built before patching: writestr also goes through open()
    requested = []

    class Endless(io.BytesIO):
        def read(self, size=-1):
            requested.append(size)
            return super().read(size)

    real_open = zipfile.ZipFile.open

    def fake_open(self, info, *args, **kwargs):
        if info.filename.endswith(".shp"):
            return Endless(b"\x00" * 5_000_000)
        return real_open(self, info, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fake_open)
    cap = 1_000_000
    with pytest.raises(ParseError, match="uncompressed"):
        read_zip_members(zipped, max_uncompressed=cap)
    # every read was explicitly bounded by the remaining budget (never size=-1 / unbounded)
    assert requested and all(0 < n <= cap + 1 for n in requested)
