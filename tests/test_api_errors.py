import logging
import uuid

from fastapi.testclient import TestClient

from app.errors import ApiError
from app.main import create_app


def error_shape(body):
    assert list(body) == ["error"]
    assert set(body["error"]) >= {"code", "message"}
    assert isinstance(body["error"]["code"], str) and isinstance(body["error"]["message"], str)


def app_with_test_routes():
    app = create_app()

    @app.get("/t/uuid/{value}")
    def needs_uuid(value: uuid.UUID):
        return {"ok": str(value)}

    @app.get("/t/boom")
    def boom():
        raise RuntimeError("secret internal detail: /etc/passwd")

    @app.get("/t/api-error")
    def api_error():
        raise ApiError(418, "teapot", "short and stout", file_id="abc")

    return app


def test_unknown_route_uses_the_error_shape():
    client = TestClient(create_app())
    resp = client.get("/nope")
    assert resp.status_code == 404
    error_shape(resp.json())
    assert resp.json()["error"]["code"] == "not_found"


def test_wrong_method_uses_the_error_shape():
    resp = TestClient(create_app()).post("/health")
    assert resp.status_code == 405
    error_shape(resp.json())
    assert resp.json()["error"]["code"] == "method_not_allowed"


def test_wrong_method_lists_every_allowed_method_in_the_allow_header():
    # RFC 9110: a 405 must carry Allow with all methods the resource supports. Starlette lists
    # only the first route whose path matches, which for /api/files/ (POST and GET routes) gave
    # "Allow: POST" alone; schemathesis flagged the missing GET on OPTIONS.
    client = TestClient(create_app())
    assert client.post("/health").headers["allow"] == "GET"
    assert client.put("/api/files/00000000-0000-0000-0000-000000000000/").headers["allow"] == "GET"
    for method in ("DELETE", "OPTIONS", "PUT", "PATCH"):
        resp = client.request(method, "/api/files/")
        assert resp.status_code == 405
        assert resp.headers["allow"] == "GET, POST"
        assert resp.json()["error"]["code"] == "method_not_allowed"


def test_fastapi_validation_errors_use_the_error_shape_without_echoing_input():
    client = TestClient(app_with_test_routes())
    resp = client.get("/t/uuid/not-a-uuid")
    assert resp.status_code == 422
    error_shape(resp.json())
    body = resp.json()["error"]
    assert body["code"] == "validation_error"
    assert "value" in body["message"]  # says which parameter is wrong
    assert "not-a-uuid" not in body["message"]  # does not echo the input back


def test_api_error_carries_extra_fields():
    resp = TestClient(app_with_test_routes()).get("/t/api-error")
    assert resp.status_code == 418
    assert resp.json() == {
        "error": {"code": "teapot", "message": "short and stout", "file_id": "abc"}
    }


def test_unexpected_exception_is_a_generic_500_and_is_logged_with_a_traceback(caplog):
    client = TestClient(app_with_test_routes(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/t/boom")
    assert resp.status_code == 500
    error_shape(resp.json())
    assert resp.json()["error"]["code"] == "internal_error"
    assert (
        "secret" not in resp.text and "passwd" not in resp.text and "RuntimeError" not in resp.text
    )
    logged = [r for r in caplog.records if r.exc_info]
    assert logged and logged[0].exc_info[0] is RuntimeError


def test_health_still_works():
    assert TestClient(create_app()).get("/health").json() == {"status": "ok"}
