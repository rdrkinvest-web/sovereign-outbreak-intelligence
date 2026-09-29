"""Structured progress events for a live console (ui/app.html).

Every event is built from what the coordinator already holds: the questions it
sends, the parsed aggregate replies it receives and its own assessment. Raw
payloads never go into an event; only their size does.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .audit import LogEntry
from .schemas import FinalAssessment, FollowUpChoice, FollowUpResponse, Refusal, ScanResponse

Event = dict[str, Any]
EventSink = Callable[[Event], None]

ROUND_LABELS = {
    "1-scan": "Round 1 · Syndrome scan",
    "2-compare": "Round 2 · Compare",
    "3-verify": "Round 3 · Follow-up",
    "privacy": "Privacy test",
}

_ASSESSMENT = {
    "unusual_febrile_cluster": "Unusual febrile cluster",
    "isolated_anomaly": "Isolated anomaly",
    "near_baseline": "Near baseline",
}


def round_event(round_: str) -> Event:
    return {"type": "round", "id": round_, "label": ROUND_LABELS.get(round_, round_)}


def question_event(round_: str, entries: list[LogEntry], text: str) -> Event:
    return {
        "type": "question",
        "round": round_,
        "to": [e.country for e in entries],
        "kind": entries[0].kind if entries else "",
        "bytes": entries[0].bytes if entries else 0,
        "text": text,
    }


_SIGNAL = {
    "fever_rate": "fever rate",
    "platelets": "platelets",
    "alt": "ALT (liver)",
    "crp": "CRP (inflammation)",
}
_MALARIA = {
    "no_material_change": "unchanged",
    "increased": "up",
    "decreased": "down",
    "insufficient_data": "too few tests to tell",
}


def signal_phrase(signal: str, direction: str) -> str:
    """'platelets', 'down' -> 'lower platelets'."""
    return ("higher " if direction == "up" else "lower ") + _SIGNAL.get(signal, signal.replace("_", " "))


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def summary(reply: object) -> str:
    """One plain sentence from a reply's aggregate fields, for answers without the agent's prose."""
    if isinstance(reply, ScanResponse):
        signals = [signal_phrase(a.signal, a.direction) for a in reply.anomalies]
        malaria = _MALARIA.get(reply.malaria_signal, reply.malaria_signal.replace("_", " "))
        found = f": {_and(signals)}" if signals else ""
        return f"{_ASSESSMENT[reply.assessment]}{found}. Malaria positivity {malaria}."
    if isinstance(reply, FollowUpResponse):
        onset = reply.metrics.get("onset_date") or reply.metrics.get("onset_week")
        answer = {"yes": "Yes", "no": "No"}.get(reply.answer, reply.answer.capitalize())
        return f"{answer}." + (f" Onset {onset}." if onset else "")
    if isinstance(reply, Refusal):
        return "Refused: " + reply.reason.replace("_", " ") + "."
    return ""


def answer_event(round_: str, entry: LogEntry, reply: object | None, elapsed_s: float) -> Event:
    notes = reply if isinstance(reply, (ScanResponse, FollowUpResponse)) else None
    return {
        "type": "answer",
        "round": round_,
        "country": entry.country,
        "kind": entry.kind,
        "bytes": entry.bytes,
        "raw_rows": entry.raw_rows,
        "refused": entry.kind == "refused",
        "mode": notes.mode if notes else "",
        "tools_used": list(notes.tools_used) if notes else [],
        "interpretation": notes.interpretation if notes else "",
        "summary": summary(reply),
        "elapsed_s": round(elapsed_s, 1),
    }


def followup_event(choice: FollowUpChoice, asked: list[str]) -> Event:
    return {
        "type": "followup",
        "question": choice.question,
        "chosen_by": choice.chosen_by,
        "rationale": choice.rationale,
        "asked": asked,
    }


def compare_event(shared: dict[tuple[str, str], list[str]]) -> Event:
    return {
        "type": "compare",
        "shared": [
            {"signal": s, "direction": d, "phrase": signal_phrase(s, d), "countries": cs}
            for (s, d), cs in shared.items()
        ],
    }


def assessment_event(assessment: FinalAssessment) -> Event:
    return {"type": "assessment", "assessment": assessment.model_dump(mode="json")}


def route_event(route: str, prompt: str) -> Event:
    return {"type": "route", "route": route, "prompt": prompt}
