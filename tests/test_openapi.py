from app.main import app

SPEC_OPERATIONS = [
    ("/api/files/", "post"),
    ("/api/files/{file_id}/", "get"),
    ("/api/files/{file_id}/measurements/", "get"),
]


def _example_present(operation: dict, schemas: dict) -> bool:
    """One example response: inline on a response, or on the response model's schema."""
    for response in operation["responses"].values():
        content = response.get("content", {}).get("application/json", {})
        if "example" in content or "examples" in content:
            return True
        ref = content.get("schema", {}).get("$ref")
        if ref and "example" in schemas[ref.rsplit("/", 1)[-1]]:
            return True
    return False


def test_every_spec_endpoint_has_a_summary_and_an_example_response():
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    for path, method in SPEC_OPERATIONS:
        operation = spec["paths"][path][method]
        assert operation.get("summary"), (path, method)
        assert _example_present(operation, schemas), (path, method)


def test_documented_error_responses_use_the_error_body_shape():
    spec = app.openapi()
    post = spec["paths"]["/api/files/"]["post"]["responses"]
    assert {"201", "400", "413", "415", "422", "503"} <= set(post)
    measurements = spec["paths"]["/api/files/{file_id}/measurements/"]["get"]["responses"]
    assert {"200", "404", "409"} <= set(measurements)
    for status in ("400", "413", "415"):
        example = post[status]["content"]["application/json"]["example"]
        assert set(example["error"]) >= {"code", "message"}


def test_the_openapi_document_is_served(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200 and "/api/files/" in resp.json()["paths"]


def test_the_measurements_example_is_what_the_api_really_returns():
    from shapely.geometry import shape

    from app.geo.measure import measure
    from app.schemas import MeasurementsResponse

    (feature,) = MeasurementsResponse.model_config["json_schema_extra"]["example"]["features"]
    real = measure(shape(feature["geometry"]))
    assert feature["measurement"]["area_m2"] == round(real.area_m2, 2)
    assert feature["measurement"]["area_crs"] == real.area_crs
