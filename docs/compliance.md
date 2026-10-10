# Requirements traceability

Every requirement of the assignment ("Geospatial File Measurement API", sections 1 to 8) and of
`plan.md`, mapped to the implementation, the tests that cover it, and a status.

Status: **OK** implemented and tested. **GAP** not done or only partly done. **NOTE** works,
but differs from the wording of the spec or plan in a way worth knowing.

This matrix was checked against the original assignment document on 2026-10-10.

## Mismatches and gaps (read this first)

| # | Kind | Item |
|---|---|---|
| M1 | FIXED (2026-10-10) | **Public GitHub repo (assignment section 8):** <https://github.com/amiya-09/Geospatial-File-Measurement-API>. The first GitHub Actions run of `.github/workflows/ci.yml` on that push succeeded on Python 3.11 and 3.12. |
| M2 | FIXED (2026-10-10) | **README "Design decisions and alternatives", "Learnings", "Future scope"** (sections 7 and 8) and `docs/decisions.md` are written. The author filled every place the draft left for them, in their own words, or removed it. |
| M3 | NOTE | `plan.md` says status goes `PROCESSING -> COMPLETED \| FAILED`. Processing is synchronous, so `PROCESSING` is never stored or returned; only `COMPLETED` and `FAILED` exist. |
| M4 | NOTE | `plan.md` says points get `measurement: null` with a reason. The API returns a `measurement` **object** whose values are all `null`, with `reason` set (`"not applicable"`, `"unsupported"`, `"no geometry"`, ...). The object shape is the same for every feature. |
| M5 | NOTE | The spec's example shows `"id": "abc123"`; ids are UUID strings. Compatible with the example's type, not its value. |
| M6 | NOTE | The spec does not define the measurements response shape. It returns an object `{file_id, crs, total, limit, offset, features[]}`, not a bare list. |
| M7 | NOTE | `plan.md` lists an optional `Dockerfile`; there is none. |
| M8 | NOTE | `plan.md` names test files `test_api.py`, `test_kml.py`, ...; the suite uses finer-grained names (`test_api_files.py`, `test_kml_features.py`, ...). |
| M9 | FIXED (2026-10-10) | Under extreme load (60 parallel uploads of 1,500 features each) some uploads could not get the SQLite write lock, or a pooled connection, within 30 s. These used to be **500**. They became **503** `database_busy` (`tests/test_db_busy.py`): 36 of 60 returned 201 and 24 returned 503 after up to 30 s, none returned 500. The feature insert was already one batched statement (78 ms for 1,500 rows), so batching did not apply. Later the same day the database wait went down to 5 s (`Retry-After: 5`) and uploads got four processing slots: an upload that waits more than 2 s for a slot gets 503 `server_busy` (`tests/test_upload_slots.py`). Rerun of the 60 parallel uploads: 4 returned 201, 56 returned 503 `server_busy`, none returned 500, all within 11.2 s; `/health` (now async) answered in 8 ms median and 965 ms at worst, against 27.2 s before. |
| M10 | FIXED (2026-10-10) | `requirements.lock` pins one known good set (generated from a clean Python 3.11 venv). The README and CI install from it, and `pyproject.toml` keeps its loose bounds. Installing from the lock and running the suite passed on fresh Python 3.11 and 3.12 venvs (348 tests at the time). Tested on Linux only. |
| M11 | NOTE | The 25 MB cap is enforced on bytes read (and by the body middleware), but Starlette spools the multipart body to a temp file before the handler runs, so a request up to cap + 64 KiB is written to disk first. |
| M12 | NOTE | `created_at` is returned only by the list endpoint (`GET /api/files/`), not by the file-info endpoint, because the info shape is fixed by the assignment example and Stage C. |

The assignment has one example response (file information). It was compared with the live API
on 2026-10-10: all five fields are present, and the API adds three (`format`, `warnings`,
`error`). The assignment defines no status codes and no measurements response shape; those are
listed under "Not defined by the spec, our choices" below.

## Assignment requirements

| Req | Requirement | Implemented in | Covered by | Status |
|---|---|---|---|---|
| Intro | Production quality Python code, a clean backend API | `pyproject.toml` (ruff settings), `.github/workflows/ci.yml`, one error body shape in `app/errors.py`, upload limits in `app/config.py` | `ruff check`, `ruff format --check` and `pytest` ran clean on 2026-10-10 | NOTE: the spec gives no measurable criterion, so this row records evidence, not a pass mark |
| 1 | Use FastAPI | `app/main.py` `create_app` | `tests/test_health.py` | OK |
| 2 | `POST /api/files/` accepts a `.zip` containing a Shapefile | `app/api/files.py` `upload_file`; `app/services/files.py` `parse_upload`; `app/parsers/shapefile.py` `parse_shapefile_zip` | `test_api_files.py::test_zipped_shapefile_in_utm_is_reprojected_to_wgs84_in_the_response`; `test_shapefile_zip.py`; `test_shapefile_parse.py` | OK |
| 2 | ... accepts a `.kml` | `parse_upload` -> `app/parsers/kml.py` `parse_kml` | `test_api_files.py::test_kml_upload_then_info_then_measurements`; `test_kml_features.py`; `test_kml_content.py` | OK |
| 2 | Format decided by content, not extension | `parse_upload` (PK signature, else KML) | `test_api_files.py::test_zip_uploaded_with_a_kml_name_and_kml_with_a_zip_name_are_accepted_by_content`; `::test_png_renamed_kml_is_415_and_kml_renamed_png_is_accepted` | OK |
| 3 | Read the file and extract features | `parse_kml`, `parse_shapefile_zip` return `ParsedFile`/`Feature` (`app/parsers/base.py`) | `test_kml_features.py`, `test_shapefile_parse.py` | OK |
| 3 | Per feature: Feature ID/index | `Feature.index`, `source_id`; `FeatureRecord.index/source_id`; response `index`, `source_id` | `test_api_files.py::test_kml_upload_then_info_then_measurements` (index stability); `test_shapefile_parse.py::test_null_shape_keeps_its_index_with_no_geometry_and_a_warning`; `::test_deleted_dbf_records_are_skipped_and_the_rest_stay_aligned` | OK |
| 3 | Per feature: Geometry type | `FeatureRecord.geometry_type`; response `geometry_type` | `test_api_files.py::test_kml_upload_then_info_then_measurements` | OK |
| 3 | Per feature: Geometry | `to_geojson` (`app/services/serialize.py`), stored in EPSG:4326; response `geometry` (`include_geometry=false` omits it) | `test_api_files.py::test_zipped_shapefile_in_utm_...`; `::test_include_geometry_false_omits_only_the_geometry` | OK |
| 3 | Per feature: CRS | `crs_label` (`app/geo/crs.py`); response `crs` (source CRS) | `test_crs.py::test_crs_label_*`; `test_api_files.py::test_zipped_shapefile_in_utm_...` | OK |
| 3 | Per feature: Properties/attributes | `Feature.properties`, `json_safe`; response `properties` | `test_kml_features.py::test_extended_data_and_schema_data_become_properties`; `test_shapefile_parse.py::test_attribute_types_become_json_friendly_values`; `test_api_files.py::test_non_finite_property_values_are_stored_as_null` | OK |
| 3 | Unsupported geometry for measurement is handled gracefully | `measure()` reasons `"unsupported"`, `"not applicable"`; parsers keep the feature with `geometry=None` + warning | `test_measure_edge_cases.py::test_unsupported_geometry_type_is_reported_not_raised`; `test_api_files.py::test_unsupported_kml_geometry_is_kept_without_geometry_and_never_a_500`; `test_api_hostile.py` (never 500) | OK |
| 4 | Polygon -> area | `app/geo/measure.py` `measure` (LAEA, `area_m2`, `area_unit`, `area_crs`) | `test_measure.py::test_box_at_equator_matches_reference`, `::test_box_at_60n_...`, `::test_polygon_with_hole_...`; `test_api_files.py` | OK |
| 4 | LineString -> length | `measure` (AEQD, `length_m`, `length_unit`, `length_crs`) | `test_measure.py::test_one_degree_of_longitude_line_...`, `::test_regional_line_...`; `scripts/verify_independent.py` (independent check against the live API) | OK |
| 4 | Point -> no measurement required | `measure` returns reason `"not applicable"` | `test_measure.py::test_point_has_no_measurement_and_a_reason`; `test_api_files.py::test_kml_upload_then_info_then_measurements` | OK (see M4) |
| 4 | Multi* and collections | `measure` measures parts separately and sums | `test_measure.py::test_multipolygon_sums_parts`, `::test_geometry_collection_measures_each_part_by_type`; `test_api_files.py::test_geometry_collection_of_mixed_types_is_measured_by_type` | OK |
| 5 | Geographic coordinates (EPSG:4326) handled | `to_wgs84` (`always_xy=True`) | `test_crs.py::test_to_wgs84_*` | OK |
| 5 | Never compute area/distance in degrees; transform to a projected CRS first | `project_local`, `local_laea`, `local_aeqd` (`app/geo/crs.py`) | `test_crs.py::test_local_laea_is_equal_area_projected_in_metres_centred_on_input`, `::test_local_aeqd_...`; `test_measure.py::test_area_matches_geodesic_in_other_hemispheres` | OK |
| 5 | Own strategy for choosing the projected CRS | Local LAEA (area) / AEQD (length) centred on each part's centroid | `test_measure.py::test_distant_polygon_parts_equal_the_sum_of_independently_measured_parts` | OK |
| 5 | Projected sources are not trusted (e.g. Web Mercator) | Always source -> WGS84 -> local projection | `test_measure.py::test_polygon_from_web_mercator_source_matches_geodesic_not_mercator_area`; `test_parsers_to_measure.py::test_utm_shapefile_measures_like_the_geodesic_area_of_the_same_lon_lat_box` | OK |
| 5 | Antimeridian features | `_unwrap_antimeridian` in `measure.py` | `test_measure_antimeridian.py`; `test_api_antimeridian.py` | OK |
| 6 | `POST /api/files/` uploads and processes | `upload_file` -> `create_file` (201) | `test_api_files.py` success tests; `::test_exact_spec_paths_never_redirect` | OK |
| 6 | `GET /api/files/{id}/` returns file info | `get_file`, `FileInfo` | `test_api_files.py::test_kml_upload_then_info_then_measurements` (POST and GET share one shape) | OK |
| 6 | `GET /api/files/{id}/measurements/` | `get_measurements`, `MeasurementsResponse` | `test_api_files.py` success tests, `include_geometry`, paging tests | OK |
| 6 | Paths exactly as in the spec, no 307 redirects | `FastAPI(redirect_slashes=False)` | `test_api_files.py::test_exact_spec_paths_never_redirect`, `::test_paths_without_the_trailing_slash_are_404_not_a_redirect` | OK |
| 6 | Unknown id / bad input produce clear errors | `app/errors.py` (one body shape) | `test_api_errors.py`; `test_api_files.py` 404/409/415/413/422 tests | OK |
| 7 | README: Setup | `README.md` "Setup", "Running" | fresh-clone check (below) | OK |
| 7 | README: API with example requests/responses | `README.md` "API reference" (real captured output) | `scripts/smoke.sh` | NOTE: upload, measurements, list and error responses are shown. For file details the README gives the curl command and says it returns the same object as the upload, with no separate response block (checked by reading the README on 2026-10-10) |
| 7 | README: Architecture (structure, file flow, measurement flow, CRS handling) | `README.md` "Project structure", "File-processing flow", "Measurement flow", "CRS handling summary" | n/a (documentation) | OK |
| 7 | README: Design decisions and alternatives | `README.md` "Design decisions and alternatives"; `docs/decisions.md` | n/a | OK (M2) |
| 8 | README mentions learnings and future scope | `README.md` "Learnings", "Future scope" | n/a | OK (M2) |
| 8 | Public GitHub repo | <https://github.com/amiya-09/Geospatial-File-Measurement-API> | GitHub Actions (`.github/workflows/ci.yml`) | OK (M1) |
| 8 | Share the repo link | n/a | n/a | Yours to do (see below) |

## Not defined by the spec, our choices

The assignment does not define the items below. Each is our own choice. The last column says what
was actually checked, on 2026-10-10 unless stated.

| Item | What the spec says | What we do | What was checked |
|---|---|---|---|
| POST response body | "Uploads and processes a file." No response example. | 201 with the file information object | For `samples/sample.kml` the POST body equals the GET file details body |
| Measurements response shape | "Returns measurements for the features in the file." No list or wrapper, no field names. `feature_count` appears only in the file information example. | An object `{file_id, crs, total, limit, offset, features}` | Live keys listed; the wrapper has no `feature_count` and the file details endpoint keeps it |
| Multipart field name | Not named. | The upload must be the form field `file` | A field named `upload` returns 422. Since commit `9e82a17` the message names the expected field (`tests/test_api_files.py::test_missing_file_field_message_names_the_expected_field`) |
| Status codes | None defined. | See "Status codes" below | n/a |
| Processing limits | None defined. | `app/config.py`: upload 25 MB, uncompressed ZIP 100 MB, 100 ZIP entries, 10,000 features, 1,000,000 vertices, 20,000 lines and polygon rings, repair of invalid polygons up to 350 vertices, XML depth 50, four uploads processed at once | `tests/test_parser_limits.py::test_default_limits`; `tests/test_upload_slots.py::test_default_slot_settings`; the limit tests in `test_parser_limits.py`, `test_measure_repair.py` and `test_api_hostile.py` |
| Coordinates outside lon/lat range | Not covered. | Strict check, no tolerance. KML, or a shapefile in a lon/lat CRS: that feature is kept with no geometry and a warning, and the rest of the file is stored (201). If no feature is left with a usable geometry, the file is unmeasurable (422, `FAILED` record). A shapefile without `.prj` whose coordinates do not fit lon/lat stays unmeasurable (422) | `tests/test_coordinate_range.py`; `test_api_files.py::test_kml_whose_only_feature_is_outside_lon_lat_is_422_with_a_failed_record`; `samples/bad_coordinates.kml` (422 in `scripts/smoke.sh`) |

## Yours to do

- Share the public repo link (assignment section 8: "Create a public github repo and share the
  link"). This cannot be done from here.

## Field names

| Where | Assignment / plan name | Actual field | Match |
|---|---|---|---|
| File info | `id` | `id` | yes |
| File info | `filename` | `filename` | yes |
| File info | `feature_count` | `feature_count` | yes |
| File info | `crs` (example `"EPSG:4326"`) | `crs` (`EPSG:<code>` or CRS name) | yes |
| File info | `status` (example `"COMPLETED"`) | `status` (`COMPLETED` / `FAILED`) | yes (M3) |
| File info | `warnings`, `error` (plan.md) | `warnings`, `error` | yes |
| Feature | Feature ID/index | `index`, `source_id` | yes |
| Feature | Geometry type | `geometry_type` | yes |
| Feature | Geometry | `geometry` | yes |
| Feature | CRS | `crs` | yes |
| Feature | Properties/attributes | `properties` | yes |
| Feature | `is_valid`, `validity_reason` (plan.md) | `is_valid`, `validity_reason` | yes |
| Measurement | `area_m2`, `length_m`, unit and CRS used (plan.md) | `area_m2`, `length_m`, `area_unit`, `length_unit`, `area_crs`, `length_crs`, `reason` | yes (M4) |

## Status codes

The assignment defines no status codes. This table is our own design.

| Situation | Status | `error.code` | Test |
|---|---|---|---|
| Created | 201 | n/a | `test_api_files.py::test_kml_upload_then_info_then_measurements` |
| Corrupt, empty, unsafe or unreadable file | 400 | `invalid_file` | `test_api_files.py::test_error_status_and_code_by_kind_of_bad_content`; `test_api_hostile.py` |
| Unknown id | 404 | `not_found` | `::test_unknown_id_is_404_on_both_get_routes` |
| Measurements of a FAILED file | 409 | `file_failed` | `::test_unmeasurable_shapefile_is_422_and_leaves_a_readable_failed_record` |
| Over the upload cap | 413 | `payload_too_large` | `::test_upload_over_the_cap_is_413_and_nothing_is_persisted`; `test_body_limit.py` |
| Unrecognised content | 415 | `unsupported_media_type` | `::test_png_renamed_kml_is_415_and_kml_renamed_png_is_accepted` |
| Unmeasurable (FAILED record stored, `file_id` in body) | 422 | `unmeasurable_file` | `::test_unmeasurable_shapefile_is_422_...`; `test_api_antimeridian.py::test_feature_spanning_the_whole_circle_is_422_with_a_failed_record`; `::test_parts_created_by_repair_count_towards_the_upload_part_limit`; `::test_kml_whose_only_feature_is_outside_lon_lat_is_422_with_a_failed_record` |
| Bad request (missing field, malformed UUID, paging out of range) | 422 | `validation_error` | `::test_malformed_id_is_a_422_in_the_error_shape`; `::test_measurements_paging_bounds_are_validated` |
| Too many uploads in progress (nothing stored, `Retry-After: 5`) | 503 | `server_busy` | `test_upload_slots.py::test_upload_when_every_slot_is_taken_is_503_server_busy_and_stores_nothing` |
| Database write lock or pooled connection not free within 5 s (nothing stored, `Retry-After: 5`) | 503 | `database_busy` | `test_db_busy.py` |
| Unexpected | 500 | `internal_error` | `::test_a_failure_while_writing_features_leaves_no_file_row` |

## plan.md items

| Plan item | Implemented in | Covered by | Status |
|---|---|---|---|
| Layered app: routes -> service -> parsers -> measurement -> repository | `app/api`, `app/services`, `app/parsers`, `app/geo`, `app/db` | whole suite | OK |
| Parsers return `ParsedFile(crs, features, warnings)` | `app/parsers/base.py` | `test_parser_base.py` | OK |
| `defusedxml` for KML (XXE, entity bombs) | `app/parsers/kml.py` | `test_kml_content.py::test_entity_expansion_bomb_is_rejected`, `::test_external_entity_is_rejected_and_not_resolved` | OK |
| Shapefile ZIP: zip-slip, bombs, encrypted, entry cap, trio required, `__MACOSX`, multiple shapefiles rejected, in memory | `app/parsers/shapefile.py` `read_zip_members` | `test_shapefile_zip.py`; `test_api_hostile.py` | OK |
| Missing `.prj`: assume 4326 only if coordinates fit, with warning; else unmeasurable | `_resolve_crs` | `test_shapefile_parse.py::test_missing_prj_*`; `test_api_files.py::test_unmeasurable_shapefile_is_422_...` | OK |
| `.cpg` encoding with fallbacks | `_encoding_from_cpg` (only names on an allowlist of text encodings; base64, rot13, zlib, UTF-16 and the like are ignored with a warning) | `test_shapefile_parse.py::test_*cpg*`, `::test_latin1_attributes_without_cpg_fall_back_with_a_warning`; `test_api_hostile.py::test_cpg_naming_base64_is_not_a_500` | OK |
| Invalid polygons: `make_valid`, flag `is_valid` / `validity_reason` | `measure`: parts are unwrapped across the antimeridian first, then all polygons of a feature are checked and repaired together (`make_valid`, "structure" method), with a warning when the repair changed the area; an invalid polygon over 350 vertices is rejected (422) | `test_api_files.py::test_invalid_polygon_is_flagged_and_still_measured`; `test_measure_repair.py`; `test_measure.py` | OK |
| Z/M dropped | `force_2d` in `to_wgs84` / `measure` | `test_crs.py::test_to_wgs84_drops_z_before_transforming` | OK |
| Null/empty geometries kept so indices stay stable | parsers, `_measure_features` | `test_shapefile_parse.py::test_null_shape_keeps_its_index_...` | OK |
| Failure policy: unreadable -> 4xx, nothing persisted; unmeasurable -> FAILED record + 422 with id | `app/services/files.py` `create_file`, `_fail` | `test_api_files.py::test_nothing_is_persisted_for_4xx_responses`, `::test_unmeasurable_shapefile_is_422_...` | OK |
| `warnings` on file info; `is_valid`/`validity_reason` per feature | `FileInfo`, `FeatureOut` | `test_api_files.py` | OK |
| SQLite via SQLAlchemy; file + features in one transaction | `app/db/` | `test_db.py::test_file_and_features_are_written_in_one_transaction`; `test_api_files.py::test_a_failure_while_writing_features_leaves_no_file_row` | OK |
| Status `PROCESSING -> COMPLETED \| FAILED` | only `COMPLETED` / `FAILED` | n/a | NOTE (M3) |
| Upload size limit | `read_limited`, `app/middleware.py` | `test_api_files.py::test_read_limited_*`; `test_body_limit.py` | OK |
| `GET /api/files/` list | `list_files` | `test_api_files.py::test_list_files_*` | OK |
| OpenAPI docs: summary and example per endpoint | `app/api/files.py`, `app/schemas.py` | `test_openapi.py` | OK |
| Tests vs `pyproj.Geod` | `tests/expected_values.py` | `test_measure*.py`; `scripts/verify_independent.py` (independent of `app`) | OK |
| Sample files, final run from a clean clone | `samples/`, `scripts/make_samples.py`, `scripts/smoke.sh` | `test_samples.py`; fresh-clone check | OK |
| Public repo | <https://github.com/amiya-09/Geospatial-File-Measurement-API> | GitHub Actions | OK (M1) |
| Optional `Dockerfile` | none | n/a | NOTE (M7) |
