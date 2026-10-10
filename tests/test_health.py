import inspect

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_runs_on_the_event_loop_so_busy_upload_threads_cannot_delay_it():
    app = create_app()
    (route,) = [r for r in app.routes if getattr(r, "path", None) == "/health"]
    assert inspect.iscoroutinefunction(route.endpoint)
