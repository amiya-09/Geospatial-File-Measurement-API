"""Processing limits. Parsers take these as keyword defaults so tests can pass small ones."""

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 100
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_FEATURES = 10_000
MAX_VERTICES = 1_000_000
MAX_XML_DEPTH = 50
