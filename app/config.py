"""Settings and processing limits. Parsers take limits as keyword defaults so tests can pass
small ones; create_file reads MAX_UPLOAD_BYTES at request time."""

import os
from pathlib import Path

DATABASE_PATH = Path(os.environ.get("GEO_API_DB", "geo_api.db"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ZIP_ENTRIES = 100
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
# The .dbf (attribute table) alone: an upload stores no more attribute text than a KML upload
# could carry. Without it a 199 KB ZIP holding a 100 MB .dbf stored 118 MB in the database.
MAX_DBF_BYTES = MAX_UPLOAD_BYTES
MAX_FEATURES = 10_000
MAX_VERTICES = 1_000_000
# Lines and polygon rings per upload (points do not count). Each part is projected on its own,
# about 0.7 ms of CPU, so this bounds the measuring time of one upload.
MAX_PARTS = 20_000
# make_valid slows down sharply with the number of self-crossings. A star polygon at this
# limit (about 30,000 crossings, the worst shape found) takes about 1.5 s; larger invalid
# polygons are rejected instead of repaired. Counted as stored, closing vertices included.
# Read at call time so tests can lower it.
MAX_REPAIR_VERTICES = 350
MAX_XML_DEPTH = 50
# Elements in one KML. defusedxml builds the tree with Python's own (slow) XML parser: 25 MB of
# empty elements took 23 s and 554 MB without a limit, and is rejected after 1.4 s and 45 MB with
# this one. A KML with 10,000 Placemarks of about 30 elements each needs 300,000.
MAX_XML_ELEMENTS = 500_000
# Parsing and measuring are CPU bound and hold the decompressed upload in memory, so only this
# many uploads are processed at once. Another upload waits up to UPLOAD_SLOT_WAIT_SECONDS for
# a slot, then gets 503 server_busy. The wait is read at call time so tests can lower it.
MAX_CONCURRENT_UPLOADS = 4
UPLOAD_SLOT_WAIT_SECONDS = 2
