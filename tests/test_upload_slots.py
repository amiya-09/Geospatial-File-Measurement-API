"""Only a few uploads are parsed and measured at once. One that cannot get a slot within a short
wait is a retryable 503 server_busy, and nothing is stored for it (M4)."""

import threading
import time

import pytest

from app import config
from app.errors import RETRY_AFTER_SECONDS
from app.services import files as service
from tests.api_helpers import SURVEY_KML, upload, utm_polygon_zip


def test_default_slot_settings():
    assert config.MAX_CONCURRENT_UPLOADS == 4
    assert config.UPLOAD_SLOT_WAIT_SECONDS == 2


@pytest.fixture
def one_slot(monkeypatch):
    """A single processing slot and a 0.1 s wait, so a held slot turns uploads away quickly."""
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(service, "processing_slots", slots)
    monkeypatch.setattr(config, "UPLOAD_SLOT_WAIT_SECONDS", 0.1)
    return slots


def test_upload_when_every_slot_is_taken_is_503_server_busy_and_stores_nothing(client, one_slot):
    one_slot.acquire()
    try:
        resp = upload(client, SURVEY_KML)
    finally:
        one_slot.release()
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == RETRY_AFTER_SECONDS
    assert resp.json()["error"]["code"] == "server_busy"
    assert set(resp.json()) == {"error"}
    assert client.get("/api/files/").json() == []


def test_an_upload_turned_away_is_never_read_into_memory(client, one_slot, monkeypatch):
    # When reading came first, 24 uploads waiting for a slot took the server from 121 MB to
    # 1.4 GB, although every one of them got a 503.
    reads, real = [], service.read_limited

    def spy(*args, **kwargs):
        reads.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "read_limited", spy)
    one_slot.acquire()
    try:
        assert upload(client, SURVEY_KML).status_code == 503
    finally:
        one_slot.release()
    assert reads == []
    assert upload(client, SURVEY_KML).status_code == 201
    assert reads == [1]


def test_reads_do_not_need_a_slot(client, one_slot):
    fid = upload(client, SURVEY_KML).json()["id"]
    one_slot.acquire()
    try:
        assert client.get("/api/files/").status_code == 200
        assert client.get(f"/api/files/{fid}/measurements/").status_code == 200
    finally:
        one_slot.release()


def test_an_upload_waits_briefly_for_a_slot_to_free_up(client, one_slot, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_SLOT_WAIT_SECONDS", 5)
    one_slot.acquire()
    threading.Timer(0.3, one_slot.release).start()
    started = time.monotonic()
    resp = upload(client, SURVEY_KML)
    assert resp.status_code == 201
    assert time.monotonic() - started >= 0.3


@pytest.mark.parametrize(
    ("data", "status"),
    [
        (SURVEY_KML, 201),
        (b"PK\x03\x04 not really a zip", 400),
        (b"plain text, neither zip nor kml", 415),
        (utm_polygon_zip(prj=False), 422),
    ],
    ids=["kml", "bad-zip", "text", "no-prj"],
)
def test_the_slot_is_released_whatever_the_outcome(client, one_slot, data, status):
    assert upload(client, data).status_code == status
    assert one_slot.acquire(blocking=False)
    one_slot.release()


def test_the_slot_is_released_after_an_unexpected_error(client, one_slot, monkeypatch):
    def boom(_data):
        raise RuntimeError("bug")

    monkeypatch.setattr(service, "parse_upload", boom)
    assert upload(client, SURVEY_KML).status_code == 500
    assert one_slot.acquire(blocking=False)
    one_slot.release()
