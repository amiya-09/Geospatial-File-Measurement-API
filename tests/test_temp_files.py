"""Upload temp files are removed after a success, a failure, a 413 and a client disconnect.

Starlette spools a multipart file over 1 MB to an unnamed temp file, which never shows up in a
directory listing, so the server's open file descriptors are checked as well. Linux only.
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(), reason="needs /proc to see the server's open files"
)

PAD = b"<!--" + b"x" * (3 << 20) + b"-->"  # 3 MB XML comment, so the upload is spooled to disk
GOOD = (
    b'<?xml version="1.0"?>' + PAD + b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
    b"<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>"
    b"13.40,52.50 13.41,52.50 13.41,52.51 13.40,52.50"
    b"</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>"
)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A real server whose temp folder is an empty folder of its own: (port, pid, temp folder)."""
    tmp_path = tmp_path_factory.mktemp("server")
    temp = tmp_path / "temp"
    temp.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {"GEO_API_DB": str(tmp_path / "live.db"), "TMPDIR": str(temp), "PATH": "/usr/bin:/bin"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start")
        yield port, proc.pid, temp
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def leftovers(pid: int, temp: Path) -> list[str]:
    """Files in the temp folder, and the server's open files that point into it."""
    found = [str(p) for p in temp.iterdir()]
    for fd in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(fd)
        except OSError:  # closed while listing
            continue
        if target.startswith(str(temp)):
            found.append(target)
    return found


def settled(pid: int, temp: Path, want_any: bool = False, timeout: float = 5.0) -> list[str]:
    """Leftovers once they are gone (or, with want_any, once there are some), or at the timeout."""
    deadline = time.monotonic() + timeout
    while True:
        found = leftovers(pid, temp)
        if bool(found) == want_any or time.monotonic() > deadline:
            return found
        time.sleep(0.05)


def multipart(data: bytes) -> tuple[bytes, str]:
    boundary = "tempfileboundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="f.kml"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def post(port: int, data: bytes, chunked: bool = False) -> int:
    body, content_type = multipart(data)
    head = f"POST /api/files/ HTTP/1.1\r\nHost: x\r\nContent-Type: {content_type}\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=60) as s:
        if chunked:
            s.sendall((head + "Transfer-Encoding: chunked\r\n\r\n").encode())
            try:
                for i in range(0, len(body), 1 << 20):
                    chunk = body[i : i + (1 << 20)]
                    s.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                s.sendall(b"0\r\n\r\n")
            except OSError:  # the server answered 413 and closed before the body ended
                pass
        else:
            s.sendall((head + f"Content-Length: {len(body)}\r\n\r\n").encode() + body)
        status_line = s.makefile("rb").readline()
    return int(status_line.split()[1])


@pytest.mark.parametrize(
    ("data", "status"),
    [
        (GOOD, 201),
        (GOOD.replace(b"52.51", b"95.00"), 422),  # its only feature is out of range
        (GOOD[:-40], 400),  # truncated XML
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 << 20), 415),
    ],
    ids=["success", "unmeasurable", "unreadable", "unsupported"],
)
def test_no_temp_file_is_left_after_a_response(server, data, status):
    port, pid, temp = server
    assert post(port, data) == status
    assert settled(pid, temp) == []


def test_no_temp_file_is_left_after_a_chunked_upload_over_the_limit(server):
    port, pid, temp = server
    assert post(port, b"<kml>" + b"x" * (27 << 20), chunked=True) == 413
    assert settled(pid, temp) == []


def test_the_spooled_file_is_removed_when_the_client_disconnects_half_way(server):
    port, pid, temp = server
    body, content_type = multipart(GOOD + b"y" * (3 << 20))
    head = (
        f"POST /api/files/ HTTP/1.1\r\nHost: x\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=60) as s:
        s.sendall(head.encode() + body[: len(body) // 2])
        # The check can see a spooled file: here it is, open, half way through the upload.
        assert settled(pid, temp, want_any=True) != []
    assert settled(pid, temp) == []
