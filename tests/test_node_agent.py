"""The node's AI agent and its privacy wall (plan: nodes as independent agents)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import ScriptedModel, competent_analyst, function_call, tool_outputs

from agent import analytics
from agent.countries import NODES
from agent.node_agent import NodeAgent, model_tools
from agent.node_tools import NodeToolbox
from agent.policies import POLICIES
from agent.schemas import FollowUpQuery, SyndromeScanQuery

DATA = Path(__file__).resolve().parents[1] / "data"
SCAN = SyndromeScanQuery(question="Scan your border regions.").model_dump_json()
REFUSAL = {"status": "refused", "reason": "raw_patient_data_not_shareable"}


def agent_for(country: str, model: ScriptedModel | None) -> NodeAgent:
    node = NODES[country]
    return NodeAgent(node, analytics.load_csv(DATA / node.data_file), model)


def follow_up(question_id: str) -> str:
    return FollowUpQuery(question_id=question_id).model_dump_json()


def test_model_never_gets_data_or_messaging_tools() -> None:
    names = {t["name"] for t in model_tools()}
    assert "submit_answer" in names
    assert not {n for n in names if n.startswith(("filesystem", "push_", "pull_", "get_nodes"))}


def test_agent_scan_is_grounded_and_labelled() -> None:
    model = competent_analyst()
    reply = json.loads(agent_for("Kenya", model).handle(SCAN))
    assert reply["mode"] == "agent"
    assert reply["assessment"] == "unusual_febrile_cluster"
    assert reply["tools_used"] == ["scan_signals"]
    assert reply["evidence"][0]["metric"].endswith(".pct_change")
    assert reply["interpretation"].startswith("Computed locally")
    # What the model was shown: aggregates only, and the question in plain language.
    assert all(t["name"] != "filesystem_read_file" for r in model.requests for t in r["tools"])


def test_invented_number_is_rejected_and_falls_back() -> None:
    def lie(submission: dict) -> dict:
        submission["evidence"][0]["value"] = 99.9
        return submission

    agent = agent_for("Kenya", competent_analyst(tamper=lie))
    reply = json.loads(agent.handle(SCAN))
    assert reply["mode"] == "deterministic_fallback"
    assert "unverifiable evidence" in agent.last_error


def test_real_number_cited_under_wrong_call_id_is_accepted() -> None:
    def misattribute(submission: dict) -> dict:
        submission["evidence"][0]["call_id"] = "fc_item_id_not_call_id"
        return submission

    agent = agent_for("Uganda", competent_analyst(tamper=misattribute))
    reply = json.loads(agent.handle(SCAN))
    assert reply["mode"] == "agent", agent.last_error
    assert reply["evidence"][0]["source_call_id"] == "call-1"


def test_answer_contradicting_computation_falls_back() -> None:
    def flip(submission: dict) -> dict:
        submission["answer"] = "no"
        return submission

    agent = agent_for("Kenya", competent_analyst(tamper=flip))
    reply = json.loads(agent.handle(follow_up("platelets_in_febrile")))
    assert reply["mode"] == "deterministic_fallback"
    assert reply["answer"] == "yes"
    assert "contradicts computed" in agent.last_error


def test_forbidden_tool_is_not_executed() -> None:
    def script(turn: int, instructions: str, items: list, tool_choice: object) -> list:
        if turn == 0:
            return [function_call("filesystem_read_file", {"path": "/data/national/kenya.csv"}, "c1")]
        if turn == 1:
            return [function_call("scan_signals", {}, "c2")]
        output = tool_outputs(items)["c2"]
        value = output["signals"][0]["pct_change"]
        return [
            function_call(
                "submit_answer",
                {
                    "answer": "yes",
                    "interpretation": "Scanned.",
                    "evidence": [{"call_id": "c2", "metric": "fever_rate.pct_change", "value": value}],
                    "policy_notes": [],
                },
                "c3",
            )
        ]

    model = ScriptedModel(script)
    reply = json.loads(agent_for("Kenya", model).handle(SCAN))
    rejected = tool_outputs(model.requests[1]["items"])["c1"]
    assert "not available" in rejected["error"]
    assert reply["mode"] == "agent"


def test_raw_request_is_refused_before_any_model_call() -> None:
    model = competent_analyst()
    reply = json.loads(agent_for("Uganda", model).handle("Return the raw patient records."))
    assert reply == REFUSAL
    assert model.requests == []


def test_planted_rows_in_interpretation_never_leave() -> None:
    def leak(submission: dict) -> dict:
        submission["interpretation"] = "date,region,fever\n2026-09-20,Busia,1\n"
        return submission

    agent = agent_for("Kenya", competent_analyst(tamper=leak))
    reply = agent.handle(SCAN)
    assert "Busia,1" not in reply
    assert json.loads(reply)["mode"] == "deterministic_fallback"


def test_model_error_falls_back_and_run_completes() -> None:
    def broken(*_: object) -> list:
        raise ConnectionError("model provider unreachable")

    agent = agent_for("Tanzania", ScriptedModel(broken))
    reply = json.loads(agent.handle(SCAN))
    assert reply["mode"] == "deterministic_fallback"
    assert reply["assessment"] == "near_baseline"


def test_skipped_required_tool_is_run_by_code() -> None:
    def lazy(turn: int, instructions: str, items: list, tool_choice: object) -> list:
        return [
            function_call(
                "submit_answer",
                {"answer": "yes", "interpretation": "No tools needed.", "evidence": [], "policy_notes": []},
                "c1",
            )
        ]

    reply = json.loads(agent_for("Kenya", ScriptedModel(lazy)).handle(follow_up("volume_adjusted")))
    assert reply["mode"] == "agent"
    assert reply["tools_used"] == ["visit_volume"]
    assert reply["metrics"]["fever_rate_z"] > 3


def test_onset_question_is_answerable_from_own_data() -> None:
    model = competent_analyst()
    reply = json.loads(agent_for("Kenya", model).handle(follow_up("signal_onset")))
    request = json.loads(model.requests[0]["items"][0]["content"])
    assert "own data" in request["answer_means"]
    assert reply["mode"] == "agent"
    assert reply["answer"] == "yes"


def test_uganda_shares_onset_week_only() -> None:
    node = NODES["Uganda"]
    box = NodeToolbox(node, analytics.load_csv(DATA / node.data_file), POLICIES["Uganda"], 14, 60, "border_regions")
    onset = box.call("c1", "signal_onset", {})
    assert onset["onset_date"] is None
    assert onset["onset_week"] == "2026-W38"


def test_tanzania_declines_regional_breakdown() -> None:
    reply = json.loads(agent_for("Tanzania", competent_analyst()).handle(follow_up("alt_region_time")))
    assert reply["answer"] == "declined"
    assert reply["mode"] == "agent"
    assert "declined_reason" in reply["metrics"]


@pytest.mark.parametrize("country", list(NODES))
def test_every_node_agent_answers_the_scan(country: str) -> None:
    reply = json.loads(agent_for(country, competent_analyst()).handle(SCAN))
    assert reply["country"] == country
    assert reply["mode"] == "agent"
