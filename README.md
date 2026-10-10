# Geospatial File Measurement API

Upload a zipped Shapefile or a KML file; the API extracts every feature and measures polygon
**area** and line **length** in metres, using projected coordinate systems (never degrees).

FastAPI, SQLAlchemy + SQLite, shapely 2, pyproj, pyshp, defusedxml. No GDAL, GeoPandas, fiona
or pyogrio.

> Sections marked **TODO (author)** are left for the author to write.
> Requirement-by-requirement status (tests, gaps, mismatches): [Requirements traceability](docs/compliance.md).

## Setup

Requires Python 3.11 or newer (the test suite passes on 3.11, 3.12 and 3.13). No system
libraries are needed: every dependency installs from a pip wheel. `requirements.lock` pins the
exact versions the test suite passed with, so install from it:

```bash
git clone <repository-url> geo-measure-api && cd geo-measure-api
python -m venv .venv      # use python3 if `python` is not on your PATH
. .venv/bin/activate
pip install -r requirements.lock
pip install -e . --no-deps
```

`pyproject.toml` keeps loose version bounds. `pip install -e ".[dev]"` instead of the two pip
lines above installs the latest releases. The lock was generated and tested on Linux only.

## Running

```bash
uvicorn app.main:app --port 8000
```

- Interactive docs: <http://127.0.0.1:8000/docs> (OpenAPI JSON at `/openapi.json`).
- Health check: `GET /health`.
- The SQLite file is `geo_api.db` in the directory you start the server from (SQLite also
  creates `geo_api.db-wal` and `geo_api.db-shm` next to it while running); set
  `GEO_API_DB=/path/to/file.db` to change it. Tables are created at startup.
- Limits (upload size 25 MB, uncompressed ZIP size 100 MB, ZIP entries, feature, vertex and part
  counts, the size of a polygon that may be repaired, XML depth, concurrent uploads) are constants
  in `app/config.py`; only the database path is read from the environment.

Tests, lint and the end-to-end smoke check:

```bash
pytest
ruff check app tests scripts
bash scripts/smoke.sh        # starts its own server on a temp DB and port, uploads every file in samples/
python scripts/make_samples.py   # regenerates samples/ (deterministic)
python scripts/verify_independent.py http://127.0.0.1:8000   # needs a running server (see above)
```

Run these with the virtual environment activated: `smoke.sh` uses the `python` on your PATH
(override with `PYTHON=/path/to/python`). `verify_independent.py` imports nothing from `app`;
it recomputes every sample feature's area/length with `pyproj.Geod` and prints the API value,
the Geod value and the percent difference.

`TOTALITY_MUTATIONS=20000 pytest tests/test_parser_totality.py` runs the long fuzz pass over
both parsers.

## API reference

All paths end with a trailing slash and are never redirected. Every error uses one body shape:
`{"error": {"code": "...", "message": "..."}}` (the 422 for an unmeasurable file also carries
`file_id` inside `error`).

| Method and path | Purpose | Success |
|---|---|---|
| `POST /api/files/` | Upload a zipped Shapefile or KML (multipart field `file`) | 201 |
| `GET /api/files/{id}/` | File details | 200 |
| `GET /api/files/{id}/measurements/` | Per-feature measurements | 200 |
| `GET /api/files/` | List uploads, newest first (`limit` 1-200, `offset`) | 200 |

### Upload

```bash
curl -F "file=@samples/sample.kml" http://127.0.0.1:8000/api/files/
```

```json
{"id": "87b4a389-ba1e-468a-91a1-ff7d0157a07c", "filename": "sample.kml", "format": "kml",
 "feature_count": 4, "crs": "EPSG:4326", "status": "COMPLETED", "warnings": [], "error": null}
```

The format is chosen from the **content** (ZIP signature means Shapefile, otherwise KML is
tried); the filename is stored as metadata only. `crs` is the file's source CRS.

### File details

```bash
curl http://127.0.0.1:8000/api/files/87b4a389-ba1e-468a-91a1-ff7d0157a07c/
```

Returns the same object as the upload response.

### Measurements

```bash
curl "http://127.0.0.1:8000/api/files/87b4a389-ba1e-468a-91a1-ff7d0157a07c/measurements/?include_geometry=false&limit=2&offset=1"
```

```json
{
  "file_id": "87b4a389-ba1e-468a-91a1-ff7d0157a07c",
  "crs": "EPSG:4326",
  "total": 4,
  "limit": 2,
  "offset": 1,
  "features": [
    {
      "index": 1, "source_id": null, "geometry_type": "LineString", "crs": "EPSG:4326",
      "properties": {"name": "Footpath"},
      "measurement": {
        "area_m2": null, "length_m": 1791.5671887934911,
        "area_unit": null, "area_crs": null,
        "length_unit": "m",
        "length_crs": "+proj=aeqd +lat_0=52.522499999999994 +lon_0=13.407499999999999 +datum=WGS84 +units=m +no_defs +type=crs",
        "reason": null
      },
      "is_valid": true, "validity_reason": null, "warnings": [], "geometry": null
    },
    {
      "index": 2, "source_id": null, "geometry_type": "Polygon", "crs": "EPSG:4326",
      "properties": {"name": "Park with pond"},
      "measurement": {
        "area_m2": 1360077.2436689679, "length_m": null,
        "area_unit": "m2",
        "area_crs": "+proj=laea +lat_0=52.50499999999998 +lon_0=13.410277777777765 +datum=WGS84 +units=m +no_defs +type=crs",
        "length_unit": null, "length_crs": null, "reason": null
      },
      "is_valid": true, "validity_reason": null, "warnings": [], "geometry": null
    }
  ]
}
```

- `index` is the feature's position in the source file and is stable across requests.
- `geometry` is GeoJSON in **EPSG:4326** whatever the source CRS (`include_geometry=false`
  returns `null`). `crs` is the **source** CRS.
- Every measured value carries its unit and the local projected CRS it was computed in.
- A feature without usable geometry has `geometry: null` and `measurement.reason`
  `"no geometry"`; points report `"not applicable"`.
- `limit` (1-1000, default: all) and `offset` page through features; `total` is the full count.

Geometry of the polygon above (`include_geometry=true`), outer ring plus the hole:

```json
{"type": "Polygon", "coordinates": [[[13.4, 52.5], [13.42, 52.5], [13.42, 52.51], [13.4, 52.51], [13.4, 52.5]], [[13.405, 52.503], [13.41, 52.503], [13.41, 52.507], [13.405, 52.507], [13.405, 52.503]]]}
```

### List

```bash
curl "http://127.0.0.1:8000/api/files/?limit=2"
```

```json
[{"id": "61b3c931-930b-4a5e-8553-5b57e2edb8c5", "filename": "bad_no_prj.zip",
  "status": "FAILED", "feature_count": 0, "created_at": "2026-10-10T03:35:28.042176Z"}]
```

`created_at` is UTC ISO-8601 with a `Z` suffix.

### Errors

| Status | `error.code` | When |
|---|---|---|
| 400 | `invalid_file` | Empty, corrupt or unsafe file (bad ZIP, zip-slip, broken XML, over parser limits). Nothing is stored. |
| 404 | `not_found` | Unknown file id. |
| 409 | `file_failed` | Measurements requested for a `FAILED` file; the stored error is returned. |
| 413 | `payload_too_large` | Body or file over `MAX_UPLOAD_BYTES` (25 MB). |
| 415 | `unsupported_media_type` | Content is neither a zipped Shapefile nor KML. |
| 422 | `validation_error` | Bad request (missing `file`, malformed UUID, paging out of range). |
| 422 | `unmeasurable_file` | Readable but not measurable (e.g. no `.prj` and coordinates are not lon/lat; a feature spanning more than 180 degrees of longitude; an invalid polygon with more than 350 vertices; more than 20,000 parts after repair). A `FAILED` record is stored; its id is `error.file_id`. |
| 503 | `server_busy` | Four uploads are already being processed and no slot freed up within 2 s. Nothing is stored; retry after the `Retry-After` seconds (5). |
| 503 | `database_busy` | The database write lock or a pooled connection was not free within 5 s. Nothing is stored; retry after the `Retry-After` seconds (5). |
| 500 | `internal_error` | Anything unexpected. Generic body; the traceback is only logged. |

```bash
curl -F "file=@samples/bad_no_prj.zip" http://127.0.0.1:8000/api/files/    # 422
```

```json
{"error": {"code": "unmeasurable_file", "message": "no .prj file and coordinates are outside the lon/lat range, so the coordinate system cannot be determined", "file_id": "61b3c931-930b-4a5e-8553-5b57e2edb8c5"}}
```

```bash
curl -F "file=@samples/bad_not_a_map.kml" http://127.0.0.1:8000/api/files/   # 415
```

```json
{"error": {"code": "unsupported_media_type", "message": "unsupported file type: expected a zipped Shapefile or KML"}}
```

## Project structure

```
app/
  main.py            app factory, router, middleware, startup (creates tables)
  config.py          database path and processing limits
  middleware.py      request body cap (Content-Length, then streamed byte count)
  errors.py          ApiError, one error body shape, exception handlers
  schemas.py         response models and OpenAPI examples
  api/files.py       the HTTP routes (plain `def` handlers)
  services/
    files.py         upload pipeline: read, detect, parse, measure, persist
    serialize.py     JSON-safe values, GeoJSON output
  parsers/
    base.py          ParsedFile, Feature, ParseError, UnmeasurableError, VertexBudget
    kml.py           KML parser (defusedxml)
    shapefile.py     zipped Shapefile parser (zipfile + pyshp)
  geo/
    crs.py           reprojection to WGS84, local LAEA / AEQD projections
    measure.py       area and length of WGS84 geometries
  db/                SQLAlchemy models, engine/session, repository
scripts/             make_samples.py (sample generator), smoke.sh (curl end-to-end check)
samples/             generated good and bad input files
tests/               pytest suite
docs/decisions.md    decision log
plan.md              the agreed plan
```

## File-processing flow

1. **Body cap.** Middleware rejects a declared `Content-Length` over the cap with 413 before
   reading anything, and counts bytes for chunked bodies.
2. **Read.** The route reads the upload in chunks and stops with 413 once `MAX_UPLOAD_BYTES`
   is exceeded.
3. **Wait for a slot.** At most four uploads are parsed and measured at once. Another upload
   waits up to 2 s for a slot, then gets 503 `server_busy`. Reads never wait for a slot.
4. **Detect by content.** `PK\x03\x04` means zipped Shapefile; otherwise the KML parser runs.
   Well-formed XML that is not `<kml>` or unrecognised bytes give 415; broken ZIP/XML gives 400.
5. **Parse.** Parsers return a `ParsedFile` (source CRS, features, warnings) or raise
   `ParseError` (unreadable, 4xx, nothing stored) / `UnmeasurableError` (stored as `FAILED`, 422).
   - Shapefile ZIP: path-safety (zip-slip) checks, entry-count and size caps enforced on bytes
     actually read, encrypted entries rejected, exactly one shapefile required, `.prj` read for
     the CRS, `.cpg` for the text encoding (only known text encodings; anything else is ignored
     with a warning and UTF-8 is assumed), DBF records flagged deleted are skipped.
   - KML: DTDs and entities forbidden, nesting depth, feature and vertex limits; a Placemark
     with bad geometry becomes a feature with `geometry: null` and a warning.
   - Both: at most 20,000 lines and polygon rings per upload, counted before any geometry is
     built (400 above that). A feature with a coordinate outside longitude +/-180 or latitude
     +/-90 (KML, or a shapefile in a lon/lat CRS) is kept with `geometry: null` and a warning.
6. **Measure** each feature (next section).
7. **Persist** the file row and all feature rows in one transaction.
8. **Respond** with the file info (201). Failures after parsing store a `FAILED` record.

## Measurement flow

For each feature with geometry:

1. Reproject from the source CRS to EPSG:4326 (lon/lat, `always_xy`), dropping Z/M.
2. Reject non-finite coordinates and values outside lon/lat range (strictly, no tolerance).
3. Split Multi* geometries and collections into parts, skipping empty parts.
4. Antimeridian: a part spanning more than 180 degrees of longitude is retried with negative
   longitudes shifted by +360; if it still spans more than 180 degrees, or the shift would
   collapse it to zero size (a feature that goes all the way round the globe), the file is
   unmeasurable (422). A warning is recorded when the shift is applied.
5. Check validity on the unwrapped parts. All polygons of one feature, including those inside a
   mixed collection, are checked together, so overlapping members count as invalid. Invalid
   polygons are repaired together (`make_valid` with the "structure" method, which unions
   overlaps) and reported with `is_valid: false` and the reason. A warning gives both areas when
   the repair changed the area. An invalid polygon with more than 350 vertices is not repaired;
   the file is unmeasurable (422).
6. For each part, build a local projection centred on the part's centroid and project into it:
   **Lambert azimuthal equal-area** for area, **azimuthal equidistant** for length.
7. Measure in metres, sum the parts, and attach the unit and local CRS to each value.

## CRS handling summary

- Source CRS comes from the `.prj` (Shapefile) or is EPSG:4326 (KML). With no `.prj`, EPSG:4326
  is assumed only if every coordinate fits lon/lat; otherwise the file is unmeasurable (422).
- `crs` in responses is `EPSG:<code>` when pyproj can identify it, otherwise the CRS name.
- Geometry is always returned in EPSG:4326.
- Areas and lengths are never computed in degrees, nor in the source CRS: always in a local
  equal-area (area) or equidistant (length) projection.
- Accuracy limits: straight edges are projected, not geodesic, so very large shapes differ
  slightly from a geodesic calculation (a 10-degree box: about -0.4% area).

## Known limitations and deviations

**TODO (author).**

- An invalid polygon with more than 350 vertices (counted as stored, closing vertices included)
  is not repaired, and the upload gets 422. Repair time grows sharply with the number of
  self-crossings: a 350 vertex star polygon took about 1.5 s. The cap counts vertices, not
  crossings, so a 10,000 vertex bow-tie that would repair in about 0.01 s is rejected too.
- An upload may hold at most 20,000 lines and polygon rings (points do not count). They are
  counted before geometry is built (400 above that) and again after repair (422).
- A coordinate outside longitude +/-180 or latitude +/-90 drops that feature's geometry with a
  warning (KML, or a shapefile in a lon/lat CRS). The upload is still 201, even when every
  feature is dropped this way, for example a KML written in projected coordinates.
- Polygons that share an edge or overlap inside one feature (a MultiPolygon or a mixed collection) are
  reported invalid and measured as their union, with a warning when that changed the area.
- A `.cpg` naming anything other than a known text encoding is ignored with a warning and UTF-8
  is assumed. ESRI numeric ISO 8859 codes such as `88595` are not recognised.
- At most four uploads are parsed and measured at once, in one process. The work is CPU bound
  and shares one interpreter lock, so four slots bound memory and waiting time rather than add
  speed. With 60 uploads of 1,500 polygons sent at once, 4 returned 201 and 56 returned 503
  `server_busy` within 11.2 s, and `/health` answered in 8 ms (median) and 965 ms at worst.
  Clients have to retry after `Retry-After`.
- Peak memory is still roughly four times (25 MB upload, up to 100 MB of decompressed members,
  and the geometries) while four uploads are processed.
- A shapefile polygon with a one point ring fails the whole upload with 400 ("list index out of
  range"), where other bad features only lose their own geometry.
- `.prj` edge cases: an empty or whitespace only `.prj`, or a byte order mark before valid WKT,
  gives 422, although a missing `.prj` falls back to EPSG:4326. A geocentric CRS such as
  EPSG:4978 is accepted and gives meaningless results (a line measured 0 m at the north pole).
- A few status codes are inconsistent: a ZIP with no `.shp` (a KMZ, say) is 400 rather than 415;
  HTML is 400 with a DOCTYPE and 415 without one; a truncated KML is 400 in UTF-8 and 415 in
  UTF-16; a multipart body without a boundary gives the code `http_error`, which the table above
  does not list.
- The measurements endpoint has no default page size, so one request returns every feature with
  its geometry (up to 10,000 features).
- Lines shorter than roughly half a millimetre measure exactly 0 m, because the azimuthal
  equidistant projection snaps points that close to its centre.
- A 405 response's `Allow` header lists the methods of the first matching route only:
  `DELETE /api/files/` returns `Allow: POST`, although GET works on that path too.
- mypy is not run in CI, and running it on `app/` reports 7 findings that are left as they are (shapely's type stubs are stricter than how the code calls shapely, pyshp ships no type stubs, and the shapefile reader types file contents as `bytes | None`).

## Design decisions and alternatives

**TODO (author).** See `docs/decisions.md` for the skeleton.

## Learnings

**TODO (author).**

## Future scope

**TODO (author).**
