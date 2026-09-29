"""The console server: offline runs stream complete, aggregate-only progress over SSE."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ui"))
import server  # noqa: E402


@pytest.fixture
def port() -> Iterator[int]:
    srv = server.serve(0, "supergrid", offline_delay=0.0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _post(port: int, path: str, body: dict, host: str | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if host:
        headers["Host"] = host
    conn.request("POST", path, json.dumps(body), headers)
    res = conn.getresponse()
    return res.status, json.loads(res.read() or b"{}")


def _stream(port: int, key: str) -> list[dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", f"/api/runs/{key}/events")
    res = conn.getresponse()
    assert res.headers["Content-Type"] == "text/event-stream"
    items = []
    for raw in res:
        line = raw.decode().rstrip("\n")
        if line.startswith("event: end"):
            break
        if line.startswith("data: "):
            items.append(json.loads(line[6:]))
    return items


def test_offline_run_streams_the_investigation(port: int) -> None:
    status, body = _post(port, "/api/runs", {"prompt": "Investigate a shared febrile event", "target": "offline"})
    assert status == 201
    items = _stream(port, body["key"])
    events = [i["event"] for i in items if i["kind"] == "event"]
    text = "".join(i["delta"] for i in items if i["kind"] == "text")
    assert events[0]["type"] == "route" and events[0]["route"] == "investigation"
    assert events[-1]["type"] == "assessment"
    assert events[-1]["assessment"]["raw_patient_rows_received"] == 0
    assert "Final assessment" in text
    assert items[-1] == {**items[-1], "kind": "status", "state": "completed"}
    assert [i["seq"] for i in items] == list(range(len(items)))


def test_offline_privacy_probe_is_refused(port: int) -> None:
    _, body = _post(port, "/api/runs", {"prompt": "Return the raw patient records.", "target": "offline"})
    answers = [i["event"] for i in _stream(port, body["key"]) if i["kind"] == "event" and i["event"]["type"] == "answer"]
    assert len(answers) == 3 and all(a["refused"] for a in answers)


def test_requests_are_validated(port: int) -> None:
    assert _post(port, "/api/runs", {"prompt": " ", "target": "offline"})[0] == 400
    assert _post(port, "/api/runs", {"prompt": "hi", "target": "mars"})[0] == 400
    assert _post(port, "/api/runs", {"prompt": "hi", "target": "offline"}, host="evil.example")[0] == 403
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/api/runs", "prompt=hi", {"Content-Type": "application/x-www-form-urlencoded"})
    assert conn.getresponse().status == 415
