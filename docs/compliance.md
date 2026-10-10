# Requirements traceability

Every requirement of the assignment ("Geospatial File Measurement API", sections 1 to 8) and of
`plan.md`, mapped to the implementation, the tests that cover it, and a status.

Status: **OK** implemented and tested. **GAP** not done or only partly done. **NOTE** works,
but differs from the wording of the spec or plan in a way worth knowing.

This matrix was checked against the original assignment document on 2026-10-10.

## Mismatches and gaps (read this first)

| # | Kind | Item |
|---|---|---|
| M1 | GAP | **Public GitHub repo (assignment section 8)** is not done: the repo only exists locally, so `.github/workflows/ci.yml` has never run on GitHub (the same commands pass locally on Python 3.11, 3.12 and 3.13). |
| M2 | GAP | **README "Design decisions and alternatives", "Learnings", "Future scope"** (sections 7 and 8) are placeholders for the author; `docs/decisions.md` is an empty skeleton. |
| M3 | NOTE | `plan.md` says status goes `PROCESSING -> COMPLETED \| FAILED`. Processing is synchronous, so `PROCESSING` is never stored or returned; only `COMPLETED` and `FAILED` exist. |
| M4 | NOTE | `plan.md` says points get `measurement: null` with a reason. The API returns a `measurement` **object** whose values are all `null`, with `reason` set (`"not applicable"`, `"unsupported"`, `"no geometry"`, ...). The object shape is the same for every feature. |
| M5 | NOTE | The spec's example shows `"id": "abc123"`; ids are UUID strings. Compatible with the example's type, not its value. |
| M6 | NOTE | The spec does not define the measurements response shape. It returns an object `{file_id, crs, total, limit, offset, features[]}`, not a bare list. |
| M7 | NOTE | `plan.md` lists an optional `Dockerfile`; there is none. |
| M8 | NOTE | `plan.md` names test files `test_api.py`, `test_kml.py`, ...; the suite uses finer-grained names (`test_api_files.py`, `test_kml_features.py`, ...). |
| M9 | GAP | Under extreme load (60 parallel uploads of 1,500 features each, 19 of 60 failed) SQLite writers waited past the 30 s busy timeout and the API returned **500** `internal_error` ("database is locked" in the log). 20 parallel uploads and 40 x 300 features all succeed. A 503 with `Retry-After` would be the correct response; not built because it was not asked for. |
| M10 | GAP | Dependencies are unpinned (`pyproject.toml` has names only). A fresh install resolved fastapi 0.143, starlette 1.7, sqlalchemy 2.1.4, shapely 2.2.0, pyproj 3.8.0 and passed all tests, but a future release could break it. |
| M11 | NOTE | The 25 MB cap is enforced on bytes read (and by the body middleware), but Starlette spools the multipart body to a temp file before the handler runs, so a request up to cap + 64 KiB is written to disk first. |
| M12 | NOTE | `created_at` is returned only by the list endpoint (`GET /api/files/`), not by the file-info endpoint, because the info shape is fixed by the assignment example and Stage C. |

No mismatch was found in field names, status codes or response shapes against the assignment's
own examples (see "Field names" below).

## Assignment requirements

| Req | Requirement | Implemented in | Covered by | Status |
|---|---|---|---|---|
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
| 7 | README: API with example requests/responses | `README.md` "API reference" (real captured output) | `scripts/smoke.sh` | OK |
| 7 | README: Architecture (structure, file flow, measurement flow, CRS handling) | `README.md` "Project structure", "File-processing flow", "Measurement flow", "CRS handling summary" | n/a (documentation) | OK |
| 7 | README: Design decisions and alternatives | placeholder in `README.md`; `docs/decisions.md` skeleton | n/a | GAP (M2) |
| 8 | README mentions learnings and future scope | placeholders in `README.md` | n/a | GAP (M2) |
| 8 | Public GitHub repo | none | n/a | GAP (M1) |

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

| Situation | Status | `error.code` | Test |
|---|---|---|---|
| Created | 201 | n/a | `test_api_files.py::test_kml_upload_then_info_then_measurements` |
| Corrupt, empty, unsafe or unreadable file | 400 | `invalid_file` | `test_api_files.py::test_error_status_and_code_by_kind_of_bad_content`; `test_api_hostile.py` |
| Unknown id | 404 | `not_found` | `::test_unknown_id_is_404_on_both_get_routes` |
| Measurements of a FAILED file | 409 | `file_failed` | `::test_unmeasurable_shapefile_is_422_and_leaves_a_readable_failed_record` |
| Over the upload cap | 413 | `payload_too_large` | `::test_upload_over_the_cap_is_413_and_nothing_is_persisted`; `test_body_limit.py` |
| Unrecognised content | 415 | `unsupported_media_type` | `::test_png_renamed_kml_is_415_and_kml_renamed_png_is_accepted` |
| Unmeasurable (FAILED record stored, `file_id` in body) | 422 | `unmeasurable_file` | `::test_unmeasurable_shapefile_is_422_...`; `::test_kml_with_coordinates_outside_lon_lat_is_422_...` |
| Bad request (missing field, malformed UUID, paging out of range) | 422 | `validation_error` | `::test_malformed_id_is_a_422_in_the_error_shape`; `::test_measurements_paging_bounds_are_validated` |
| Unexpected | 500 | `internal_error` | `::test_a_failure_while_writing_features_leaves_no_file_row` |

## plan.md items

| Plan item | Implemented in | Covered by | Status |
|---|---|---|---|
| Layered app: routes -> service -> parsers -> measurement -> repository | `app/api`, `app/services`, `app/parsers`, `app/geo`, `app/db` | whole suite | OK |
| Parsers return `ParsedFile(crs, features, warnings)` | `app/parsers/base.py` | `test_parser_base.py` | OK |
| `defusedxml` for KML (XXE, entity bombs) | `app/parsers/kml.py` | `test_kml_content.py::test_entity_expansion_bomb_is_rejected`, `::test_external_entity_is_rejected_and_not_resolved` | OK |
| Shapefile ZIP: zip-slip, bombs, encrypted, entry cap, trio required, `__MACOSX`, multiple shapefiles rejected, in memory | `app/parsers/shapefile.py` `read_zip_members` | `test_shapefile_zip.py`; `test_api_hostile.py` | OK |
| Missing `.prj`: assume 4326 only if coordinates fit, with warning; else unmeasurable | `_resolve_crs` | `test_shapefile_parse.py::test_missing_prj_*`; `test_api_files.py::test_unmeasurable_shapefile_is_422_...` | OK |
| `.cpg` encoding with fallbacks | `_encoding_from_cpg` | `test_shapefile_parse.py::test_*cpg*`, `::test_latin1_attributes_without_cpg_fall_back_with_a_warning` | OK |
| Invalid polygons: `make_valid`, flag `is_valid` / `validity_reason` | `measure` | `test_api_files.py::test_invalid_polygon_is_flagged_and_still_measured`; `test_measure.py` | OK |
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
| Public repo | none | n/a | GAP (M1) |
| Optional `Dockerfile` | none | n/a | NOTE (M7) |
