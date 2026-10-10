"""20 parallel uploads against a real server process (own temp DB) must all succeed."""

import json
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.api_helpers import SURVEY_KML, utm_polygon_zip

ROOT = Path(__file__).resolve().parent.parent
WORKERS = 20


@pytest.fixture
def live_server(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {"GEO_API_DB": str(tmp_path / "live.db"), "PATH": "/usr/bin:/bin"}
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{base}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def post(base: str, name: str, data: bytes) -> tuple[int, dict]:
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    )
    body = head.encode() + data + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base}/api/files/",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_twenty_parallel_uploads_all_succeed_and_are_all_readable(live_server):
    payloads = [("a.kml", SURVEY_KML), ("b.zip", utm_polygon_zip())]
    jobs = [payloads[i % 2] for i in range(WORKERS)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda job: post(live_server, *job), jobs))
    assert [status for status, _ in results] == [201] * WORKERS, results
    ids = {body["id"] for _, body in results}
    assert len(ids) == WORKERS

    def read(file_id):
        url = f"{live_server}/api/files/{file_id}/measurements/"
        with urllib.request.urlopen(url, timeout=240) as response:
            return response.status, json.load(response)["total"]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        reads = list(pool.map(read, ids))
    assert all(status == 200 for status, _ in reads)
    assert sorted(total for _, total in reads) == sorted(
        [4] * (WORKERS // 2) + [2] * (WORKERS // 2)
    )
    with urllib.request.urlopen(f"{live_server}/api/files/?limit=200") as response:
        assert len(json.load(response)) == WORKERS
