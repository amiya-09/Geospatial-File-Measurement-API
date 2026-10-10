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
MAX_XML_DEPTH = 50
