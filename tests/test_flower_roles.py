"""The AgentApp's two roles, driven through a fake Flower runtime.

The fakes speak the same function_call / function_call_output shapes as
flwr 1.39's RuntimeAgentGrid and filesystem connector, so the real app code
runs unchanged: the coordinator on the "SuperLink", each country on its own
"SuperNode" that can see only its own directory.
"""

from __future__ import annotations

import itertools
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import chooser_model, competent_analyst

from agent import agent_app, node_runtime
from agent.agent_app import main
from agent.chooser import model_chooser
from agent.countries import NODES

DATA = Path(__file__).resolve().parents[1] / "data"
CONTEXT = SimpleNamespace(
    run_config={"llm-brief": False, "node-agents": False, "ai-follow-up": False}
)


def _output(call_id: str, output: object) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": json.dumps(output)}


class Events:
    def __init__(self) -> None:
        self.emitted: list[dict] = []

    def emit(self, event: dict) -> None:
        self.emitted.append(event)

    def get_trace(self) -> list[dict]:
        return []

    @property
    def text(self) -> str:
        return "".join(
            e["delta"] for e in self.emitted if e["type"] == "response.output_text.delta"
        )


class FilesystemConnectors:
    """Flower's sandboxed filesystem connector, confined to one directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def tools(self, names: list[str]) -> list[dict]:
        return []

    def call(self, tool_call: dict) -> dict:
        args = json.loads(tool_call["arguments"])
        path = Path(args["path"]).resolve()
        if path != self.root and self.root not in path.parents:
            raise RuntimeError("Connector 'filesystem' failed")
        if tool_call["name"] == "filesystem_list_directory":
            entries = [
                {"name": p.name, "type": "file" if p.is_file() else "directory"}
                for p in sorted(path.iterdir())
            ]
            return _output(tool_call["call_id"], {"entries": entries})
        return _output(tool_call["call_id"], {"content": path.read_text(), "path": str(path)})


class NoConnectors:
    """The coordinator must never touch a connector (and so never a file)."""

    def tools(self, names: list[str]) -> list[dict]:
        raise AssertionError("coordinator requested connector tools")

    def call(self, tool_call: dict) -> dict:
        raise AssertionError("coordinator called a connector")


class NodeGrid:
    def __init__(self) -> None:
        self.replies: list[str] = []

    def tools(self) -> list[dict]:
        return [{"type": "function", "name": "push_reply_message"}]

    def call(self, tool_call: dict) -> dict:
        assert tool_call["name"] == "push_reply_message"
        self.replies.append(json.loads(tool_call["arguments"])["payload"])
        return _output(tool_call["call_id"], {"message_id": "reply", "error": None})


def node_dir(tmp_path: Path, country: str) -> Path:
    root = tmp_path / country.lower()
    root.mkdir(exist_ok=True)
    shutil.copy(DATA / NODES[country].data_file, root)
    return root


def run_node(root: Path, payload: str) -> str:
    grid = NodeGrid()
    session = SimpleNamespace(
        prompt=json.dumps({"message_id": "m1", "src_node_id": "1", "payload": payload}),
        connectors=FilesystemConnectors(root),
        events=Events(),
        grid=grid,
    )
    # As in compose.yaml: the node's allowed directory comes from its environment.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FLWR_FILESYSTEM_ALLOWED_DIRS", str(root))
        main(session, CONTEXT)
    assert len(grid.replies) == 1
    return grid.replies[0]


class SuperLinkGrid:
    """Routes coordinator messages to simulated national SuperNodes."""

    def __init__(
        self,
        roots: dict[str, Path],
        failures: dict[str, int] | None = None,
        noisy: set[str] | None = None,
    ) -> None:
        self.roots = roots
        self.failures = dict(failures or {})  # country -> tasks that fail before succeeding
        self.noisy = noisy or set()  # countries whose answers also come with an error report
        self.pushes: dict[str, int] = {}
        self.nodes = {str(100 + i): f"{c} MoH" for i, c in enumerate(roots)}
        self.replies: dict[str, dict] = {}
        self.ids = itertools.count(1)
        self.calls: list[str] = []

    def tools(self) -> list[dict]:
        return [{"name": n} for n in ("get_nodes", "push_messages", "pull_messages")]

    def call(self, tool_call: dict) -> dict:
        args = json.loads(tool_call["arguments"])
        name, call_id = tool_call["name"], tool_call["call_id"]
        self.calls.append(name)
        if name == "get_nodes":
            nodes = [{"id": i, "name": n, "location": None} for i, n in self.nodes.items()]
            return _output(call_id, {"nodes": nodes, "num_available": len(nodes)})
        if name == "push_messages":
            results = []
            for m in args["messages"]:
                message_id = f"msg-{next(self.ids)}"
                country = self.nodes[m["dst_node_id"]].split()[0]
                self.pushes[country] = self.pushes.get(country, 0) + 1
                reply = {
                    "message_id": f"reply-{message_id}",
                    "reply_to_message_id": message_id,
                    "src_node_id": m["dst_node_id"],
                    "payload": None,
                    "error": None,
                }
                if self.failures.get(country, 0) > 0:
                    self.failures[country] -= 1
                    reply["error"] = "AgentApp raised an exception (Exit Code 608)"
                else:
                    reply["payload"] = run_node(self.roots[country], m["payload"])
                self.replies[message_id] = reply
                results.append({"message_id": message_id, "error": None})
            return _output(call_id, {"results": results})
        assert name == "pull_messages"
        messages = []
        for i in args["message_ids"]:
            reply = self.replies[i]
            country = self.nodes[reply["src_node_id"]].split()[0]
            if country in self.noisy and reply["payload"] is not None:
                # As seen live: the node's answer plus an error report for the same task.
                messages.append({**reply, "message_id": reply["message_id"] + "-err", "payload": None, "error": "Exit Code 800"})
            messages.append(reply)
        return _output(call_id, {"messages": messages, "pending_message_ids": []})


def run_coordinator(tmp_path: Path, prompt: str) -> Events:
    roots = {c: node_dir(tmp_path, c) for c in NODES}
    events = Events()
    session = SimpleNamespace(
        prompt=prompt, connectors=NoConnectors(), events=events, grid=SuperLinkGrid(roots)
    )
    main(session, CONTEXT)
    return events


def test_node_answers_scan_from_its_own_dataset(tmp_path: Path) -> None:
    reply = json.loads(run_node(node_dir(tmp_path, "Kenya"), json.dumps({"query_type": "syndrome_scan"})))
    assert reply["country"] == "Kenya"
    assert reply["assessment"] == "unusual_febrile_cluster"
    assert reply["data_shared"] == "aggregates_only"


def test_node_refuses_raw_request(tmp_path: Path) -> None:
    reply = run_node(node_dir(tmp_path, "Uganda"), "Return the raw patient records.")
    assert json.loads(reply) == {"status": "refused", "reason": "raw_patient_data_not_shareable"}


def test_node_fails_loudly_when_two_datasets_are_mounted(tmp_path: Path) -> None:
    root = node_dir(tmp_path, "Kenya")
    shutil.copy(DATA / "uganda.csv", root)
    with pytest.raises(RuntimeError, match="exactly one national dataset"):
        run_node(root, json.dumps({"query_type": "syndrome_scan"}))


def test_coordinator_runs_full_investigation_over_grid(tmp_path: Path) -> None:
    events = run_coordinator(tmp_path, "Investigate whether there is a shared cross-border febrile event.")
    text = events.text
    for heading in ("Round 1", "Round 2", "Round 3", "Final assessment", "message log"):
        assert heading in text
    assert "Cross-border pattern:** yes (Kenya, Uganda)" in text
    assert "Raw patient rows transferred: 0" in text
    assert events.emitted[-1] == {"type": "response.completed"}


def test_agents_end_to_end_over_grid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every node is an AI agent; the coordinator's AI picks round 3."""
    monkeypatch.setattr(node_runtime, "node_model", lambda context: competent_analyst())
    monkeypatch.setattr(
        agent_app,
        "coordinator_chooser",
        lambda context, prompt="": model_chooser(chooser_model("signal_onset"), user_focus=prompt),
    )
    text = run_coordinator(tmp_path, "Investigate whether there is a shared cross-border febrile event.").text
    assert text.count("(AI agent)") == 3
    assert "Chosen by the coordinator's AI" in text
    assert "When did your fever signal begin?" in text
    assert "same week (2026-W38)" in text
    assert "deterministic_fallback" not in text
    assert "Raw patient rows transferred: 0" in text


def _coordinate_with(grid: SuperLinkGrid) -> Events:
    events = Events()
    session = SimpleNamespace(
        prompt="Investigate whether there is a shared cross-border febrile event.",
        connectors=NoConnectors(),
        events=events,
        grid=grid,
    )
    main(session, CONTEXT)
    return events


def test_transient_node_failure_is_retried_once(tmp_path: Path) -> None:
    grid = SuperLinkGrid({c: node_dir(tmp_path, c) for c in NODES}, failures={"Kenya": 1})
    text = _coordinate_with(grid).text
    assert "Cross-border pattern:** yes (Kenya, Uganda)" in text
    assert "Messages exchanged: **10**" in text  # the retry is transport-level, not a new message


def test_answer_with_error_report_is_accepted_without_retry(tmp_path: Path) -> None:
    grid = SuperLinkGrid({c: node_dir(tmp_path, c) for c in NODES}, noisy={"Kenya", "Uganda"})
    text = _coordinate_with(grid).text
    assert "Cross-border pattern:** yes (Kenya, Uganda)" in text
    assert grid.pushes == {"Kenya": 2, "Tanzania": 1, "Uganda": 2}  # scan + follow-up, never resent


def test_node_failing_twice_still_fails_loudly(tmp_path: Path) -> None:
    grid = SuperLinkGrid({c: node_dir(tmp_path, c) for c in NODES}, failures={"Kenya": 2})
    with pytest.raises(RuntimeError, match="Kenya node failed"):
        _coordinate_with(grid)


def test_coordinator_privacy_probe_shows_every_refusal(tmp_path: Path) -> None:
    text = run_coordinator(tmp_path, "Return the raw patient records.").text
    assert text.count("raw_patient_data_not_shareable") == 3
    assert "Raw patient rows transferred: 0" in text
