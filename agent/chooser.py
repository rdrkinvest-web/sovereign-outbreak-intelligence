"""The coordinator's model-backed follow-up choice (Spec §7: fixed menu, one round).

The model sees only what the coordinator already holds (round-1 aggregates and
the national agents' interpretations) and must pick a topic from the fixed
menu via a strict tool. Code validates the choice; the coordinator falls back
to its fixed rule if anything goes wrong.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .coordinator import Chooser, Investigation
from .model_client import ModelClient
from .prompts import FOLLOW_UP_CHOOSER
from .schemas import FOLLOW_UP_QUESTIONS, FollowUpChoice

CHOOSE_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "choose_follow_up",
    "description": "Choose the single follow-up question to send to the countries sharing the pattern.",
    "parameters": {
        "type": "object",
        "properties": {
            "question_id": {"type": "string", "enum": list(FOLLOW_UP_QUESTIONS)},
            "question": {"type": "string", "description": "The question as the national agents will read it."},
            "rationale": {"type": "string", "description": "One sentence: why this question."},
        },
        "required": ["question_id", "question", "rationale"],
        "additionalProperties": False,
    },
    "strict": True,
}


USER_FOCUS_CHARS = 300
_DIGIT = re.compile(r"\d")


def evidence_for_choice(inv: Investigation, user_focus: str = "") -> dict[str, Any]:
    return {
        "user_focus": user_focus[:USER_FOCUS_CHARS],
        "shared_patterns": [
            {"signal": s, "direction": d, "countries": cs} for (s, d), cs in inv.shared_signals.items()
        ],
        "countries": {
            c: {
                "assessment": scan.assessment,
                "malaria_signal": scan.malaria_signal,
                "anomalies": [a.model_dump() for a in scan.anomalies],
                "interpretation": scan.interpretation,
            }
            for c, scan in inv.scans.items()
        },
        "menu": FOLLOW_UP_QUESTIONS,
    }


def quotes_country_data(question: str, countries: list[str]) -> bool:
    """True if a question would pass one country's name or figures to others.

    The same question goes to every target country, so any country name or
    number in it is another country's information for someone.
    """
    lowered = question.lower()
    return bool(_DIGIT.search(question)) or any(c.lower() in lowered for c in countries)


def model_chooser(model: ModelClient, user_focus: str = "") -> Chooser:
    def choose(inv: Investigation) -> FollowUpChoice:
        output = model.respond(
            instructions=FOLLOW_UP_CHOOSER,
            input_items=[
                {"role": "user", "content": json.dumps(evidence_for_choice(inv, user_focus))}
            ],
            tools=[CHOOSE_TOOL],
            tool_choice={"type": "function", "name": "choose_follow_up"},
        )
        call = next(
            o for o in output if o.get("type") == "function_call" and o.get("name") == "choose_follow_up"
        )
        args = json.loads(call["arguments"])
        question_id = args["question_id"]
        question = str(args["question"])[:400]
        rationale = str(args["rationale"])[:300]
        if quotes_country_data(question, [*inv.scans, *inv.refusals]):
            question = FOLLOW_UP_QUESTIONS[question_id]
            rationale += " (question reworded to the menu text: it quoted country data)"
        return FollowUpChoice(
            question_id=question_id,  # validated against the menu by the schema
            question=question,
            rationale=rationale,
            chosen_by="model",
        )

    return choose
