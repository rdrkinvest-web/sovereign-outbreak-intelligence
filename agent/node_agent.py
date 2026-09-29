"""A national node's AI agent, behind the privacy wall.

The model's provider sits outside the node, so everything the model reads is
treated as leaving the node. The wall is therefore enforced by reach, not by
instructions:

1. Raw-data requests are refused by code before any model call.
2. The model's only tools are aggregate analytics (node_tools); it never gets
   the filesystem connector or push_reply_message. Tool outputs are audited.
3. The model ends by calling submit_answer. Code then validates the answer,
   checks that every cited number exists in a recorded tool output, checks the
   answer agrees with the computed result, and audits the reply. Only code
   sends it (node_runtime).
4. Any failure falls back to the deterministic answer, labelled as such.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .audit import assert_aggregate_only
from .countries import NodeConfig
from .model_client import ModelClient
from .national import (
    NationalAgent,
    follow_up_response,
    parse_or_refuse,
    scan_response,
    toolbox_for,
)
from .node_tools import ANSWER_MEANING, TOOL_SCHEMAS, TOPIC_TOOL, NodeToolbox, find_value
from .policies import POLICIES, SharingPolicy
from .prompts import NODE_ANALYST
from .schemas import (
    FOLLOW_UP_QUESTIONS,
    Evidence,
    NodeResponse,
    Query,
    Refusal,
    SyndromeScanQuery,
)

MAX_TOOL_TURNS = 4
DEADLINE_SECONDS = 150.0  # the coordinator waits 240 s; leave room for the fallback
FORBIDDEN_TOOL_PREFIXES = ("filesystem", "push_", "pull_", "get_nodes")

SUBMIT_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "submit_answer",
    "description": "Submit your final answer to the coordinator. Call exactly once.",
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "enum": ["yes", "no", "inconclusive", "declined"]},
            "interpretation": {"type": "string", "description": "At most 3 sentences."},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "call_id": {"type": "string"},
                        "metric": {"type": "string"},
                        "value": {"type": ["number", "string", "boolean", "null"]},
                    },
                    "required": ["call_id", "metric", "value"],
                    "additionalProperties": False,
                },
            },
            "policy_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer", "interpretation", "evidence", "policy_notes"],
        "additionalProperties": False,
    },
    "strict": True,
}


class AgentFailure(RuntimeError):
    """The agent's output cannot be sent; the node falls back."""


class _Submitted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class _Cited(BaseModel):
        model_config = ConfigDict(extra="forbid")
        call_id: str
        metric: str
        value: float | int | str | bool | None

    answer: str
    interpretation: str = Field(max_length=600)
    evidence: list[_Cited] = Field(max_length=5)
    policy_notes: list[str] = Field(max_length=5)


def model_tools() -> list[dict[str, Any]]:
    """The model's complete tool list; asserted free of data or messaging access."""
    tools = [*TOOL_SCHEMAS, SUBMIT_TOOL]
    for tool in tools:
        if tool["name"].startswith(FORBIDDEN_TOOL_PREFIXES):
            raise AssertionError(f"forbidden tool exposed to the model: {tool['name']}")
    return tools


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


class NodeAgent:
    def __init__(
        self,
        node: NodeConfig,
        data: pd.DataFrame,
        model: ModelClient | None,
        policy: SharingPolicy | None = None,
        max_tool_turns: int = MAX_TOOL_TURNS,
        deadline_seconds: float = DEADLINE_SECONDS,
    ) -> None:
        self.node = node
        self.policy = policy or POLICIES[node.country]
        self._data = data
        self._model = model
        self._max_tool_turns = max_tool_turns
        self._deadline_seconds = deadline_seconds
        self._fallback = NationalAgent(node, data, self.policy)
        self.last_error: str | None = None

    @property
    def country(self) -> str:
        return self.node.country

    def handle(self, payload: str) -> str:
        """Answer one inbound payload. The reply is audited before it leaves."""
        reply = self.respond(payload).model_dump_json()
        assert_aggregate_only(reply)
        return reply

    def respond(self, payload: str) -> NodeResponse:
        query = parse_or_refuse(payload)
        if isinstance(query, Refusal) or self._model is None:
            return query if isinstance(query, Refusal) else self._fallback.answer(query)
        try:
            response = self._run(query)
            assert_aggregate_only(response.model_dump_json())
            return response
        except Exception as err:  # never send an unchecked answer
            self.last_error = f"{type(err).__name__}: {err}"
            return self._fallback.answer(query)

    # --- agent loop ----------------------------------------------------------

    def _run(self, query: Query) -> NodeResponse:
        toolbox = toolbox_for(self.node, self._data, self.policy, query)
        if isinstance(query, SyndromeScanQuery):
            topic, required_tool = "syndrome_scan", "scan_signals"
            default_question = "Scan for unusual febrile illness."
        else:
            topic, required_tool = query.question_id, TOPIC_TOOL[query.question_id][0]
            default_question = FOLLOW_UP_QUESTIONS[query.question_id]
        instructions = NODE_ANALYST.format(
            country=self.country,
            policy="\n".join(f"- {rule}" for rule in self.policy.describe()),
            required_tool=required_tool,
        )
        request = {
            "question": query.question or default_question,
            "topic": topic,
            "answer_means": ANSWER_MEANING.get(topic, "yes = you found an unusual febrile cluster"),
            "time_window_days": query.time_window_days,
            "baseline_days": query.baseline_days,
            "region_scope": query.region_scope,
        }
        items: list[dict[str, Any]] = [{"role": "user", "content": json.dumps(request)}]
        tools = model_tools()
        deadline = time.monotonic() + self._deadline_seconds

        for turn in range(self._max_tool_turns + 1):
            if time.monotonic() > deadline:
                raise AgentFailure("deadline exceeded")
            last_turn = turn == self._max_tool_turns
            output = self._model.respond(
                instructions=instructions,
                input_items=items,
                tools=tools,
                tool_choice={"type": "function", "name": "submit_answer"} if last_turn else "auto",
            )
            calls = [o for o in output if o.get("type") == "function_call"]
            submit = next((c for c in calls if c.get("name") == "submit_answer"), None)
            if submit is not None:
                return self._finalize(query, toolbox, json.loads(submit["arguments"]))
            # Keep only what the next request needs; reasoning items are dropped.
            items.extend(o for o in output if o.get("type") in {"function_call", "message"})
            if not calls:
                items.append({"role": "user", "content": "Run the tools you need, then call submit_answer."})
                continue
            for call in calls:
                try:
                    result: Any = toolbox.call(call["call_id"], call["name"], json.loads(call["arguments"] or "{}"))
                except Exception as err:  # unknown tool, bad arguments, policy: tell the model
                    result = {"error": str(err)}
                items.append(
                    {"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}
                )
        raise AgentFailure("no answer submitted")

    def _finalize(self, query: Query, toolbox: NodeToolbox, raw: dict[str, Any]) -> NodeResponse:
        try:
            submitted = _Submitted.model_validate(raw)
        except ValidationError as err:
            raise AgentFailure(f"invalid submission: {err.error_count()} error(s)") from err

        evidence = []
        for cited in submitted.evidence:  # provenance: no invented numbers
            source = self._source_of(toolbox, cited.call_id, cited.metric, cited.value)
            if source is None:
                raise AgentFailure(f"unverifiable evidence {cited.metric}={cited.value!r}")
            evidence.append(Evidence(source_call_id=source, metric=cited.metric, value=cited.value))

        notes = {
            "interpretation": submitted.interpretation,
            "evidence": evidence,
            "policy_notes": submitted.policy_notes or self.policy.describe(),
            "mode": "agent",
        }
        if isinstance(query, SyndromeScanQuery):
            scan = self._required_output(toolbox, "scan_signals", {})
            return scan_response(self.country, scan, tools_used=toolbox.calls, **notes)

        tool, args = TOPIC_TOOL[query.question_id]
        output = self._required_output(toolbox, tool, args)
        if submitted.answer != output["answer"]:  # the answer must match the computation
            raise AgentFailure(f"answer {submitted.answer!r} contradicts computed {output['answer']!r}")
        return follow_up_response(self.country, query, output, tools_used=toolbox.calls, **notes)

    @staticmethod
    def _source_of(toolbox: NodeToolbox, call_id: str, metric: str, value: Any) -> str | None:
        """The recorded tool call holding exactly this metric and value, if any.

        Models sometimes cite a call by its item id rather than its call_id, so
        the cited call is tried first, then every other recorded output.
        """
        ordered = [call_id, *(c for c in toolbox.outputs if c != call_id)]
        for candidate in ordered:
            output = toolbox.outputs.get(candidate)
            if output is None:
                continue
            found, recorded = find_value(output, metric)
            if found and _same(recorded, value):
                return candidate
        return None

    @staticmethod
    def _required_output(toolbox: NodeToolbox, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """The canonical tool's output; run by code if the model skipped it."""
        for call_id, (name, call_args) in toolbox.log.items():
            if name == tool and call_args == args:
                return toolbox.outputs[call_id]
        return toolbox.call(f"auto-{tool}", tool, args)
