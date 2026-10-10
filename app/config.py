"""Settings and processing limits. Parsers take limits as keyword defaults so tests can pass
small ones; the upload route reads MAX_UPLOAD_BYTES at request time."""

import os
from pathlib import Path

DATABASE_PATH = Path(os.environ.get("GEO_API_DB", "geo_api.db"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ZIP_ENTRIES = 100
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
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
