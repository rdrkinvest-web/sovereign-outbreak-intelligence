"""Outbreak Console: a local web app that runs a prompt on the federation and shows it live.

    uv run python ui/server.py            # then open http://127.0.0.1:8765

Live runs go to Flower SuperGrid with your `flwr login supergrid` session, built
from this repo's current code (as `/load .` does in `flwr chat`). Offline runs
use the in-process LocalFederation: deterministic, no network, no models.

The page only ever receives what the coordinator holds: structured progress
events (agent/events.py) and the chat Markdown. It binds to 127.0.0.1 and
rejects other Host headers, because it starts runs with your credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agent import report  # noqa: E402
from agent.agent_app import OFF_TOPIC_REPLY, route, run_privacy_probe  # noqa: E402
from agent.coordinator import investigate  # noqa: E402
from agent.events import route_event  # noqa: E402
from agent.flower_transport import country_from_node_name  # noqa: E402
from agent.local_federation import LocalFederation  # noqa: E402

PAGE = Path(__file__).with_name("app.html")
MAX_PROMPT = 2000
NEBIUS_TTL_SECONDS = 60


class Run:
    """One prompt's run: an append-only list of items the page streams over SSE."""

    def __init__(self, prompt: str, target: str) -> None:
        self.key = uuid.uuid4().hex[:12]
        self.prompt = prompt
        self.target = target
        self.items: list[dict[str, Any]] = []
        self.done = False
        self.flower_run_id: int | None = None
        self.stop_requested = False
        self._cond = threading.Condition()

    def push(self, item: dict[str, Any]) -> None:
        with self._cond:
            if self.done:
                return
            self.items.append({"seq": len(self.items), **item})
            self._cond.notify_all()

    def event(self, event: dict[str, Any]) -> None:
        self.push({"kind": "event", "event": event})

    def text(self, delta: str) -> None:
        if delta:
            self.push({"kind": "text", "delta": delta})

    def status(self, state: str, message: str = "") -> None:
        run_id = str(self.flower_run_id) if self.flower_run_id is not None else None  # uint64: too big for JS numbers
        self.push({"kind": "status", "state": state, "message": message, "run_id": run_id})

    def finish(self, state: str, message: str = "") -> None:
        self.status(state, message)
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def wait_from(self, start: int, timeout: float) -> tuple[list[dict[str, Any]], bool]:
        with self._cond:
            self._cond.wait_for(lambda: len(self.items) > start or self.done, timeout)
            return self.items[start:], self.done


class Stopped(Exception):
    pass


class PacedFederation:
    """LocalFederation with a short pause per exchange, so rehearsals show the wait."""

    def __init__(self, run: Run, delay: float) -> None:
        self._fed = LocalFederation()
        self._run = run
        self._delay = delay

    def countries(self) -> list[str]:
        return self._fed.countries()

    def exchange(self, payloads: dict[str, str]) -> dict[str, str]:
        deadline = time.monotonic() + self._delay
        while time.monotonic() < deadline:
            if self._run.stop_requested:
                raise Stopped
            time.sleep(0.05)
        return self._fed.exchange(payloads)


class Flower:
    """One Control API client for the whole process (token refresh rewrites credentials.yaml)."""

    def __init__(self, connection: str) -> None:
        self.connection_name = connection
        self._lock = threading.Lock()
        self._stub: Any = None
        self._connection: Any = None

    def stub(self) -> Any:
        with self._lock:
            if self._stub is None:
                from flwr.cli.flower_config import read_superlink_connection
                from flwr.cli.utils import init_http_client_from_connection

                self._connection = read_superlink_connection(self.connection_name)
                self._stub = init_http_client_from_connection(self._connection)
            return self._stub

    def status(self) -> dict[str, Any]:
        from flwr.proto.control_pb2 import ListFederationsRequest, ShowFederationRequest

        try:
            stub = self.stub()
            names = [f.name for f in stub.ListFederations(ListFederationsRequest()).federations]
            federations = []
            for name in names:
                nodes = stub.ShowFederation(ShowFederationRequest(federation_name=name)).federation.nodes
                federations.append({
                    "name": name,
                    "nodes": [
                        {"name": n.name, "country": country_from_node_name(n.name), "status": n.status}
                        for n in nodes
                        if n.status != "deleted" and country_from_node_name(n.name)
                    ],
                })
        except BaseException as err:  # click exits on auth errors; report, never crash
            return {
                "ok": False,
                "superlink": self.connection_name,
                "error": _short_error(err) or "Not signed in. Run: uv run flwr login supergrid",
            }
        # Default to the federation holding the most online national nodes; else as flwr chat does.
        preferred = getattr(self._connection, "federation", None)
        online = {f["name"]: sum(n["status"] == "online" for n in f["nodes"]) for f in federations}
        chat_default = preferred if preferred in names else next(
            (n for n in names if n.endswith("/personal")), names[0] if names else ""
        )
        best = max(names, key=lambda n: (online[n], n == chat_default), default="")
        return {
            "ok": True,
            "superlink": self.connection_name,
            "federations": federations,
            "federation": best,
        }

    def run(self, run: Run, federation: str) -> None:
        from flwr.cli.chat.chat_app import format_failure_event, parse_task_event, start_chat_run
        from flwr.cli.chat.chat_local_agent import build_local_agent
        from flwr.cli.constant import (
            CHAT_FAILURE_EVENTS,
            CHAT_TERMINAL_EVENTS,
            CHAT_TEXT_DELTA_EVENT,
        )
        from flwr.proto.control_pb2 import StreamRunEventsRequest

        run.status("starting", "Building the app from this repo and starting a run on SuperGrid…")
        stub = self.stub()
        agent = build_local_agent(REPO)
        run.flower_run_id, _ = start_chat_run(
            stub, run.prompt, federation or None, None, agent.app_spec, agent.fab_hash, agent.fab_content
        )
        run.status("running", f"Run {run.flower_run_id} started on {federation or 'the default federation'}.")
        for res in stub.StreamRunEvents(StreamRunEventsRequest(run_id=run.flower_run_id)):
            event_type, payload = parse_task_event(res.task_event)
            if event_type == "soi.event" and isinstance(payload.get("event"), dict):
                run.event(payload["event"])
            elif event_type == CHAT_TEXT_DELTA_EVENT:
                run.text(str(payload.get("delta", "")))
            elif event_type in CHAT_FAILURE_EVENTS:
                return run.finish("failed", format_failure_event(payload))
            elif event_type in CHAT_TERMINAL_EVENTS:
                return run.finish("completed")
            if run.stop_requested:
                return run.finish("stopped", "Stopped.")
        run.finish("stopped" if run.stop_requested else "failed", "The run ended without completing.")

    def stop(self, run: Run) -> None:
        from flwr.proto.control_pb2 import StopRunRequest

        if run.flower_run_id is not None:
            self.stub().StopRun(StopRunRequest(run_id=run.flower_run_id))


class Nebius:
    """VM state per country from scripts/nebius_vms.sh status (read-only, cached)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {"available": False, "checking": True, "vms": {}}
        self._checked = 0.0

    def status(self) -> dict[str, Any]:
        with self._lock:
            stale = time.monotonic() - self._checked > NEBIUS_TTL_SECONDS
            if stale:
                self._checked = time.monotonic()
                threading.Thread(target=self._refresh, daemon=True).start()
            return dict(self._cache)

    def _refresh(self) -> None:
        try:
            out = subprocess.run(
                [str(REPO / "scripts" / "nebius_vms.sh"), "status"],
                capture_output=True, text=True, timeout=45, cwd=REPO,
            )
            vms = {}
            for line in out.stdout.splitlines():
                m = re.match(r"moh-(\w+):\s*(\S+)", line)
                if m:
                    vms[m.group(1).capitalize()] = m.group(2).lower()
            result = (
                {"available": True, "vms": vms}
                if vms
                else {"available": False, "vms": {}, "error": (out.stderr.strip().splitlines() or ["No VMs reported"])[-1][:200]}
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            result = {"available": False, "vms": {}, "error": _short_error(err)}
        with self._lock:
            self._cache = result


def run_offline(run: Run, delay: float) -> None:
    kind = route(run.prompt)
    run.event(route_event(kind, run.prompt))
    run.status("running", "Offline rehearsal: deterministic node answers, no network.")
    if kind == "off_topic":
        run.text(OFF_TOPIC_REPLY)
        return run.finish("completed")
    fed = PacedFederation(run, delay)
    run.text(report.header())
    if kind == "privacy":
        run_privacy_probe(fed, run.prompt, run.text, run.event)
    else:
        assessment, inv = investigate(
            fed, observe=lambda r, i: run.text(report.round_markdown(r, i)), on_event=run.event
        )
        run.text(report.assessment_markdown(assessment))
        run.text(report.audit_markdown(inv.log))
        run.text(report.footer(assessment))
    run.finish("completed")


def _short_error(err: BaseException) -> str:
    message = getattr(err, "message", None) or str(err)
    return message.strip().splitlines()[0][:300] if message.strip() else ""


class Console:
    def __init__(self, superlink: str, offline_delay: float) -> None:
        self.flower = Flower(superlink)
        self.nebius = Nebius()
        self.offline_delay = offline_delay
        self.runs: dict[str, Run] = {}

    def start(self, prompt: str, target: str, federation: str) -> Run:
        run = Run(prompt, target)
        self.runs[run.key] = run

        def work() -> None:
            try:
                if target == "offline":
                    run_offline(run, self.offline_delay)
                else:
                    self.flower.run(run, federation)
            except Stopped:
                run.finish("stopped", "Stopped.")
            except BaseException as err:  # surface every failure to the page
                run.finish("failed", _short_error(err) or type(err).__name__)

        threading.Thread(target=work, daemon=True).start()
        return run

    def stop(self, run: Run) -> None:
        run.stop_requested = True
        if run.target == "supergrid":
            try:
                self.flower.stop(run)
            except BaseException as err:
                run.status("running", f"Stop request failed: {_short_error(err)}")


def make_handler(console: Console, allowed_hosts: set[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "OutbreakConsole/1.0"

        def log_message(self, format: str, *args: Any) -> None:  # quieter default logging
            if not self.path.startswith("/api/status"):
                sys.stderr.write("%s %s\n" % (self.command, self.path))

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in allowed_hosts:
                return True
            self._json(403, {"error": "This console only answers on localhost."})
            return False

        def _json(self, code: int, body: object) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict[str, Any] | None:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(415, {"error": "Send JSON."})
                return None
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(min(length, 64_000)) or b"{}")
            except json.JSONDecodeError:
                body = None
            if not isinstance(body, dict):
                self._json(400, {"error": "Send a JSON object."})
                return None
            return body

        def do_GET(self) -> None:
            if not self._host_ok():
                return
            if self.path in {"/", "/index.html"}:
                data = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif self.path == "/api/status":
                self._json(200, {"flower": console.flower.status(), "nebius": console.nebius.status()})
            elif (m := re.fullmatch(r"/api/runs/(\w+)/events", self.path)) and m.group(1) in console.runs:
                self._stream(console.runs[m.group(1)])
            else:
                self._json(404, {"error": "Not found."})

        def do_POST(self) -> None:
            if not self._host_ok():
                return
            if self.path == "/api/runs":
                body = self._body()
                if body is None:
                    return
                prompt = str(body.get("prompt", "")).strip()
                target = body.get("target")
                if not prompt:
                    return self._json(400, {"error": "Type a prompt first."})
                if len(prompt) > MAX_PROMPT:
                    return self._json(400, {"error": f"Keep the prompt under {MAX_PROMPT} characters."})
                if target not in {"supergrid", "offline"}:
                    return self._json(400, {"error": "Choose Live SuperGrid or Offline rehearsal."})
                run = console.start(prompt, target, str(body.get("federation") or ""))
                self._json(201, {"key": run.key})
            elif (m := re.fullmatch(r"/api/runs/(\w+)/stop", self.path)) and m.group(1) in console.runs:
                if self._body() is None:
                    return
                console.stop(console.runs[m.group(1)])
                self._json(202, {"ok": True})
            else:
                self._json(404, {"error": "Not found."})

        def _stream(self, run: Run) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                start = int(self.headers.get("Last-Event-ID", "-1")) + 1
            except ValueError:
                start = 0
            try:
                while True:
                    items, done = run.wait_from(start, timeout=15)
                    for item in items:
                        self.wfile.write(f"id: {item['seq']}\ndata: {json.dumps(item)}\n\n".encode())
                    start += len(items)
                    if done and start >= len(run.items):
                        self.wfile.write(b"event: end\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    if not items:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def serve(port: int, superlink: str, offline_delay: float) -> ThreadingHTTPServer:
    console = Console(superlink, offline_delay)
    server = ThreadingHTTPServer(("127.0.0.1", port), None)  # type: ignore[arg-type]
    actual = server.server_address[1]
    server.RequestHandlerClass = make_handler(console, {f"127.0.0.1:{actual}", f"localhost:{actual}"})
    server.daemon_threads = True
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (8000 is oMLX's)")
    parser.add_argument("--superlink", default="supergrid", help="connection name in ~/.flwr/config.toml")
    parser.add_argument("--offline-delay", type=float, default=1.5, help="seconds per offline exchange")
    args = parser.parse_args()
    server = serve(args.port, args.superlink, args.offline_delay)
    print(f"Outbreak Console on http://127.0.0.1:{server.server_address[1]}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
