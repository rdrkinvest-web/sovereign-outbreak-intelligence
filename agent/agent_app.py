"""Sovereign Outbreak Intelligence: one Flower AgentApp, two roles.

On SuperGrid it is the regional coordinator; on a national SuperNode it is
that country's Ministry-of-Health agent. Flower gives each role different Grid
tools (the coordinator can send, a node can only reply), so the app picks its
role from the tools it was given.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable

from flwr.agentapp import AgentApp, AgentEvents, AgentSession
from flwr.app import Context
from openai import OpenAI

from . import events, report
from .audit import MessageLog
from .chooser import model_chooser
from .coordinator import Chooser, Transport, investigate
from .events import EventSink
from .flower_transport import FlowerTransport
from .model_client import FlowerModelClient
from .node_runtime import run_national_node
from .prompts import COORDINATOR_BRIEF
from .schemas import RESPONSE_ADAPTER, FinalAssessment

DEFAULT_MODEL = "openai/gpt-5.6-sol"

# Explicit raw-data requests only: "check the health records for fever" is an
# investigation, "send me the patient records" is a privacy probe.
_RAW_REQUEST = re.compile(
    r"\braw\b|patient[- ]level|line[- ]list"
    r"|\b(send|give|export|share|dump|download|show)\b.{0,30}"
    r"\b(rows?|records?|csv|dataset|data ?set|line ?list)\b",
    re.IGNORECASE,
)
_ON_TOPIC = re.compile(
    r"investigat|outbreak|febrile|fever|signal|scan|cross[- ]border|border|surveillance"
    r"|anomal|unusual|spike|cluster|illness|disease|epidemi|health|malaria|dengue"
    r"|platelet|onset|kenya|uganda|tanzania|analy[sz]e|assess",
    re.IGNORECASE,
)
OFF_TOPIC_REPLY = (
    "I coordinate a cross-border outbreak investigation across the Kenya, Uganda and "
    "Tanzania Ministry-of-Health nodes, using aggregate answers only. Try:\n\n"
    "- `Investigate whether there is a shared cross-border febrile event.`\n"
    "- `Investigate, and check whether malaria explains it.`\n"
)

app = AgentApp()


@app.main()
def main(agent: AgentSession, context: Context) -> None:
    tools = {tool["name"] for tool in agent.grid.tools()}
    if "push_reply_message" in tools:
        run_national_node(agent, context)
    else:
        run_coordinator(agent, context)


def coordinator_chooser(context: Context, prompt: str = "") -> Chooser | None:
    """The model-backed follow-up choice, steered by the user's prompt, or None for the fixed rule."""
    if not context.run_config.get("ai-follow-up", True):
        return None
    model = FlowerModelClient(str(context.run_config.get("model", DEFAULT_MODEL)))
    return model_chooser(model, user_focus=prompt)


class ChatWriter:
    """Streams Markdown into Flower Chat as one assistant response."""

    def __init__(self, events: AgentEvents) -> None:
        self._events = events
        self._parts: list[str] = []

    def write(self, text: str) -> None:
        if text:
            self._parts.append(text)
            self._events.emit({"type": "response.output_text.delta", "delta": text})

    def close(self) -> None:
        self._events.emit({"type": "response.completed"})
        print("".join(self._parts))


def route(prompt: str) -> str:
    """'privacy' for explicit raw-data requests, 'investigation' when on topic, else 'off_topic'."""
    if _RAW_REQUEST.search(prompt):
        return "privacy"
    return "investigation" if _ON_TOPIC.search(prompt) else "off_topic"


def event_sink(agent_events: AgentEvents, context: Context) -> EventSink:
    """Structured progress for the live console; Flower Chat ignores this event type."""
    if not context.run_config.get("ui-events", True):
        return lambda _event: None
    return lambda event: agent_events.emit({"type": "soi.event", "event": event})


def run_coordinator(agent: AgentSession, context: Context) -> None:
    chat = ChatWriter(agent.events)
    emit = event_sink(agent.events, context)
    try:
        kind = route(agent.prompt)
        emit(events.route_event(kind, agent.prompt))
        if kind == "off_topic":
            chat.write(OFF_TOPIC_REPLY)  # no federation messages for off-topic prompts
            return
        transport = FlowerTransport(agent.grid)
        chat.write(report.header())
        if kind == "privacy":
            run_privacy_probe(transport, agent.prompt, chat.write, emit)
            return
        assessment, inv = investigate(
            transport,
            observe=lambda r, i: chat.write(report.round_markdown(r, i)),
            choose=coordinator_chooser(context, agent.prompt),
            on_event=emit,
        )
        if context.run_config.get("llm-brief", True):
            model = str(context.run_config.get("model", DEFAULT_MODEL))
            chat.write("### Coordinator brief\n\n")
            write_brief(assessment, model, chat.write)
            chat.write("\n\n")
        chat.write(report.assessment_markdown(assessment))
        chat.write(report.audit_markdown(inv.log))
        chat.write(report.footer(assessment))
    except Exception as err:
        emit({"type": "error", "message": str(err)})
        chat.write(f"\n\n**Investigation failed:** {err}\n")
        raise
    finally:
        chat.close()


def run_privacy_probe(
    transport: Transport,
    request: str,
    write: Callable[[str], None],
    emit: EventSink = lambda _event: None,
) -> None:
    """Forward a raw-data request so the audience sees every node refuse it."""
    log = MessageLog()
    countries = transport.countries()
    write(f"### Privacy test\n\n> {request}\n\nForwarded verbatim to {', '.join(countries)}.\n\n")
    emit(events.round_event("privacy"))
    sent = [log.record("privacy", "coordinator->node", c, request) for c in countries]
    emit(events.question_event("privacy", sent, request))
    started = time.monotonic()
    replies = transport.exchange({c: request for c in countries})
    elapsed = time.monotonic() - started
    for c in countries:
        entry = log.record("privacy", "node->coordinator", c, replies[c])
        emit(events.answer_event("privacy", entry, _parse_reply(replies[c]), elapsed))
        write(f"- **{c}:** `{replies[c]}`\n")
    write("\n" + report.audit_markdown(log))
    write(f"**Raw patient rows transferred: {log.raw_rows_received}**\n")


def _parse_reply(reply: str) -> object | None:
    try:
        return RESPONSE_ADAPTER.validate_json(reply)
    except ValueError:
        return None


def write_brief(assessment: FinalAssessment, model: str, write: Callable[[str], None]) -> None:
    """Have the model summarize the computed assessment; never invent a result."""
    try:
        client = OpenAI(
            base_url=os.environ["FLWR_RUNTIME_BASE_URL"],
            api_key=os.environ["FLWR_RUNTIME_API_KEY"],
            max_retries=0,
        )
        stream = client.responses.create(
            model=model,
            instructions=COORDINATOR_BRIEF,
            input=json.dumps(assessment.model_dump()),
            stream=True,
        )
        for event in stream:
            if event.type == "response.output_text.delta":
                write(event.delta)
            elif event.type in {"error", "response.failed", "response.incomplete"}:
                raise RuntimeError(f"model response did not complete ({event.type})")
    except Exception as err:  # the deterministic assessment below still stands
        write(f"_Model brief unavailable: {err}. The assessment below is computed without a model._")
