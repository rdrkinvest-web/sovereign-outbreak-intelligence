"""Scripted stand-ins for the model, speaking the Responses function-call shapes."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

from agent.node_tools import TOPIC_TOOL

Items = list[dict[str, Any]]


def function_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": call_id}


def tool_outputs(items: Items) -> dict[str, dict[str, Any]]:
    """Every tool output the model has seen so far, by call id."""
    return {
        i["call_id"]: json.loads(i["output"])
        for i in items
        if i.get("type") == "function_call_output"
    }


class ScriptedModel:
    """Calls `script(turn, instructions, items, tool_choice)` for each request."""

    def __init__(self, script: Callable[..., Items]) -> None:
        self.script = script
        self.requests: list[dict[str, Any]] = []

    def respond(self, *, instructions: str, input_items: Items, tools: Items, tool_choice: Any = "auto") -> Items:
        self.requests.append(
            {"instructions": instructions, "items": list(input_items), "tools": tools, "tool_choice": tool_choice}
        )
        return self.script(len(self.requests) - 1, instructions, input_items, tool_choice)


def _first_number(output: dict[str, Any]) -> tuple[str, Any]:
    if "signals" in output:
        s = next(s for s in output["signals"] if s["anomalous"]) if any(
            s["anomalous"] for s in output["signals"]
        ) else output["signals"][0]
        return f"{s['signal']}.pct_change", s["pct_change"]
    for key, value in output.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return key, value
    return "answer", output["answer"]


def competent_analyst(tamper: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> ScriptedModel:
    """Runs the required tool, then submits an answer grounded in its output."""
    ids = itertools.count(1)

    def script(turn: int, instructions: str, items: Items, tool_choice: Any) -> Items:
        request = json.loads(items[0]["content"])
        if turn == 0:
            if request["topic"] == "syndrome_scan":
                return [function_call("scan_signals", {}, f"call-{next(ids)}")]
            tool, args = TOPIC_TOOL[request["topic"]]
            return [function_call(tool, args, f"call-{next(ids)}")]
        call_id, output = next(iter(tool_outputs(items).items()))
        metric, value = _first_number(output)
        submission = {
            "answer": output.get("answer", "yes"),
            "interpretation": f"Computed locally: {metric} is {value}. Interpretation: consistent with the question.",
            "evidence": [{"call_id": call_id, "metric": metric, "value": value}],
            "policy_notes": [],
        }
        if tamper:
            submission = tamper(submission)
        return [function_call("submit_answer", submission, f"call-{next(ids)}")]

    return ScriptedModel(script)


def chooser_model(question_id: str = "signal_onset") -> ScriptedModel:
    def script(turn: int, instructions: str, items: Items, tool_choice: Any) -> Items:
        args = {
            "question_id": question_id,
            "question": "When did your fever signal begin?",
            "rationale": "Timing across the border tests whether the signals are linked.",
        }
        return [function_call("choose_follow_up", args, "choose-1")]

    return ScriptedModel(script)
