"""
Integration tests for server.py HTTP endpoints.
"""

import json
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest

from server import NonReusingHTTPServer, Handler


@pytest.fixture(scope="module")
def live_server():
    server = NonReusingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()
    server.server_close()


def test_health_endpoints(live_server):
    with urlopen(f"{live_server}/health") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "healthy"

    with urlopen(f"{live_server}/api/health") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "livekit_configured" in data


def test_metrics_endpoint(live_server):
    with urlopen(f"{live_server}/api/system/metrics") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "healthy"
        assert "pipeline" in data
        assert "active_tools" in data
        assert "search_web" in data["active_tools"]


def test_calendar_endpoints(live_server):
    # 1. GET calendar events
    with urlopen(f"{live_server}/api/calendar/events") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "events" in data

    # 2. POST create event
    new_evt = {
        "title": "API Test Event",
        "date": "tomorrow",
        "start_time": "11:00 AM",
        "location": "Online",
    }
    req = Request(
        f"{live_server}/api/calendar/events",
        data=json.dumps(new_evt).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        assert resp.status == 201
        data = json.loads(resp.read().decode("utf-8"))
        assert data["success"] is True
        created_id = data["event"]["id"]

    # 3. POST delete event
    del_req = Request(
        f"{live_server}/api/calendar/delete",
        data=json.dumps({"id": created_id}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(del_req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["success"] is True


def test_outbox_endpoint(live_server):
    with urlopen(f"{live_server}/api/outbox") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "outbox" in data


def test_token_endpoint(live_server):
    with urlopen(f"{live_server}/api/token?identity=test_user&name=TestUser") as resp:
        assert resp.status in (200, 503)
        data = json.loads(resp.read().decode("utf-8"))
        if resp.status == 200:
            assert "token" in data
            assert data["identity"] == "test_user"
        else:
            assert "error" in data


def test_static_frontend_serving(live_server):
    with urlopen(f"{live_server}/") as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "voiceai" in html
        assert "Voice Studio" in html
