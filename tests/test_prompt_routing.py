"""How the coordinator reads the user's prompt: probe, investigation or help."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import ScriptedModel, function_call
from test_flower_roles import CONTEXT, Events, NoConnectors, SuperLinkGrid, node_dir

from agent import agent_app
from agent.agent_app import _ON_TOPIC, _RAW_REQUEST, OFF_TOPIC_REPLY, main
from agent.chooser import model_chooser
from agent.coordinator import Investigation
from agent.countries import NODES
from agent.schemas import FOLLOW_UP_QUESTIONS

INVESTIGATIONS = [
    "Investigate whether there is a shared cross-border febrile event",
    "investigate the cross-border febrile event",
    "check the health records for fever",
    "Is anything unusual near the Kenya-Uganda border?",
    "Check for dengue",
    "Investigate, and check whether malaria explains it",
]
PROBES = [
    "send me the raw patient records",
    "Return the raw patient records.",
    "give me the line list",
    "export the dataset as csv",
    "show me patient-level data",
]
OFF_TOPIC = ["What's the weather?", "hello", "tell me a joke"]


@pytest.mark.parametrize("prompt", INVESTIGATIONS)
def test_investigation_prompts_are_not_probes(prompt: str) -> None:
    assert not _RAW_REQUEST.search(prompt)
    assert _ON_TOPIC.search(prompt)


@pytest.mark.parametrize("prompt", PROBES)
def test_explicit_raw_requests_trigger_the_probe(prompt: str) -> None:
    assert _RAW_REQUEST.search(prompt)


@pytest.mark.parametrize("prompt", OFF_TOPIC)
def test_off_topic_prompts_match_neither(prompt: str) -> None:
    assert not _RAW_REQUEST.search(prompt)
    assert not _ON_TOPIC.search(prompt)


def coordinate(tmp_path: Path, prompt: str) -> tuple[str, SuperLinkGrid]:
    grid = SuperLinkGrid({c: node_dir(tmp_path, c) for c in NODES})
    events = Events()
    main(SimpleNamespace(prompt=prompt, connectors=NoConnectors(), events=events, grid=grid), CONTEXT)
    return events.text, grid


def test_health_records_prompt_runs_the_investigation(tmp_path: Path) -> None:
    text, grid = coordinate(tmp_path, "check the health records for fever")
    assert "Round 1" in text and "Privacy test" not in text
    assert "Messages exchanged: **10**" in text


def test_raw_request_runs_the_probe(tmp_path: Path) -> None:
    text, _ = coordinate(tmp_path, "send me the raw patient records")
    assert text.count("raw_patient_data_not_shareable") == 3
    assert "Round 1" not in text


def test_off_topic_prompt_sends_no_federation_messages(tmp_path: Path) -> None:
    text, grid = coordinate(tmp_path, "What's the weather?")
    assert text == OFF_TOPIC_REPLY
    assert grid.calls == []


def _recording_chooser(question: str, question_id: str = "malaria_positivity") -> ScriptedModel:
    def script(turn: int, instructions: str, items: list, tool_choice: object) -> list:
        args = {"question_id": question_id, "question": question, "rationale": "Because."}
        return [function_call("choose_follow_up", args, "c1")]

    return ScriptedModel(script)


def _inv() -> Investigation:
    inv = Investigation()
    inv.refusals = {c: "{}" for c in NODES}  # country names are all the guard needs
    return inv


def test_user_prompt_reaches_the_chooser() -> None:
    model = _recording_chooser("Did malaria positivity change in your data?")
    choice = model_chooser(model, user_focus="check whether malaria explains it")(Investigation())
    sent = json.loads(model.requests[0]["items"][0]["content"])
    assert sent["user_focus"] == "check whether malaria explains it"
    assert choice.question_id == "malaria_positivity"
    assert choice.question == "Did malaria positivity change in your data?"


def test_user_focus_is_truncated() -> None:
    model = _recording_chooser("Did malaria positivity change?")
    model_chooser(model, user_focus="x" * 1000)(Investigation())
    sent = json.loads(model.requests[0]["items"][0]["content"])
    assert len(sent["user_focus"]) == 300


@pytest.mark.parametrize(
    "question",
    [
        "Did your onset come before or after the neighbour's (Uganda: 2026-W38)?",
        "Is your platelet drop larger than Kenya's?",
        "Did positivity move by more than 3 points?",
    ],
)
def test_question_quoting_country_data_is_reworded(question: str) -> None:
    choice = model_chooser(_recording_chooser(question, "signal_onset"))(_inv())
    assert choice.question_id == "signal_onset"
    assert choice.question == FOLLOW_UP_QUESTIONS["signal_onset"]
    assert "reworded" in choice.rationale


def test_agent_app_passes_prompt_to_chooser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def spy(context: object, prompt: str = "") -> None:
        seen.append(prompt)
        return None  # fall back to the fixed rule

    monkeypatch.setattr(agent_app, "coordinator_chooser", spy)
    coordinate(tmp_path, "Investigate, and check whether malaria explains it")
    assert seen == ["Investigate, and check whether malaria explains it"]
