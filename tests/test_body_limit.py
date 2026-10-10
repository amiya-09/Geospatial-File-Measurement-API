"""Request body cap enforced by middleware, before the multipart parser or any geo parsing."""

import pytest

from app import config
from tests.api_helpers import SURVEY_KML, upload
from tests.test_api_files import row_counts


@pytest.fixture
def parser_calls(monkeypatch):
    """Record whether any parser was reached."""
    from app.services import files as files_service

    calls = []
    for name in ("parse_kml", "parse_shapefile_zip"):
        original = getattr(files_service, name)

        def spy(*args, _original=original, **kwargs):
            calls.append(1)
            return _original(*args, **kwargs)

        monkeypatch.setattr(files_service, name, spy)
    return calls


def test_declared_content_length_over_the_cap_is_413_before_any_parsing(
    client, session_factory, monkeypatch, parser_calls
):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1_000)
    resp = client.post(
        "/api/files/",
        content=b"x" * 200_000,
        headers={"content-type": "multipart/form-data; boundary=zzz"},
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
    assert parser_calls == [] and row_counts(session_factory) == (0, 0)


def test_content_length_check_does_not_read_the_body(monkeypatch):
    """Drive the ASGI app directly: a body that would blow up if read is never requested."""
    import asyncio

    from app.main import app

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1_000)
    sent, received = [], []

    async def receive():
        received.append(1)
        return {"type": "http.request", "body": b"x", "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/files/",
        "raw_path": b"/api/files/",
        "query_string": b"",
        "headers": [(b"content-length", b"999999"), (b"content-type", b"multipart/form-data")],
        "server": ("test", 80),
        "client": ("c", 1),
        "scheme": "http",
        "http_version": "1.1",
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert received == []


def test_streamed_body_without_content_length_is_cut_off_with_413(
    client, session_factory, monkeypatch, parser_calls
):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1_000)

    def body():  # a well-formed multipart start, then far more file data than allowed
        yield (
            b'--zzz\r\nContent-Disposition: form-data; name="file"; filename="a.kml"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
        )
        for _ in range(2_000):
            yield b"x" * 1_024

    request = client.build_request(
        "POST",
        "/api/files/",
        content=body(),
        headers={"content-type": "multipart/form-data; boundary=zzz"},
    )
    assert "content-length" not in request.headers  # genuinely chunked
    resp = client.send(request)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
    assert parser_calls == [] and row_counts(session_factory) == (0, 0)


def test_normal_uploads_and_small_bodies_are_unaffected(client):
    assert upload(client, SURVEY_KML).status_code == 201
    assert client.get("/health").status_code == 200


def test_the_cap_applies_to_the_file_bytes_not_the_multipart_envelope(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", len(SURVEY_KML))
    assert (
        upload(client, SURVEY_KML, "a-rather-long-file-name-" + "x" * 200 + ".kml").status_code
        == 201
    )


def test_streaming_stops_pulling_chunks_soon_after_the_cap(monkeypatch):
    """Drive the ASGI app with an endless chunked body: it must stop asking for more."""
    import asyncio

    from app.main import app

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1_000)
    pulled, sent = [], []

    async def receive():
        pulled.append(1)
        if len(pulled) == 1:
            return {
                "type": "http.request",
                "body": b'--zzz\r\nContent-Disposition: form-data; name="file"; filename="a"'
                b"\r\n\r\n",
                "more_body": True,
            }
        return {"type": "http.request", "body": b"x" * 1_024, "more_body": True}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/files/",
        "raw_path": b"/api/files/",
        "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=zzz")],
        "server": ("test", 80),
        "client": ("c", 1),
        "scheme": "http",
        "http_version": "1.1",
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 413
    assert len(pulled) < 200  # 64 KiB of slack at 1 KiB a chunk, then cut off
