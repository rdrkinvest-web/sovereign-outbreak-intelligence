"""Message log and privacy audit for everything crossing the federation (Spec §9).

The raw-row count is measured, not asserted: every payload is scanned for
anything shaped like a patient-level record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

RECORD_FIELDS = frozenset(
    {
        "date",
        "region",
        "age_band",
        "fever",
        "rash",
        "cough",
        "diarrhea",
        "platelets",
        "alt",
        "crp",
        "malaria_test",
        "dengue_test",
    }
)
_MIN_FIELDS_FOR_ROW = 3
_CSV_HEADER = re.compile(
    r"\b(" + "|".join(sorted(RECORD_FIELDS)) + r")\b\s*,\s*\b(" + "|".join(sorted(RECORD_FIELDS)) + r")\b"
)


def _row_like(value: object) -> int:
    if isinstance(value, dict):
        own = int(len(RECORD_FIELDS.intersection(value)) >= _MIN_FIELDS_FOR_ROW)
        return own + sum(_row_like(v) for v in value.values())
    if isinstance(value, list):
        return sum(_row_like(v) for v in value)
    if isinstance(value, str):
        # A CSV dump: a header naming record fields, then one row per line.
        if _CSV_HEADER.search(value):
            return max(1, value.count("\n"))
    return 0


def count_raw_rows(payload: str) -> int:
    """Count patient-level records in a message payload."""
    try:
        return _row_like(json.loads(payload))
    except json.JSONDecodeError:
        return _row_like(payload)


class PrivacyViolation(RuntimeError):
    """Raised when a node is about to send something row-shaped."""


def assert_aggregate_only(payload: str) -> None:
    rows = count_raw_rows(payload)
    if rows:
        raise PrivacyViolation(f"Outgoing payload contains {rows} row-like record(s).")


@dataclass
class LogEntry:
    round: str
    direction: str  # "coordinator->node" | "node->coordinator"
    country: str
    kind: str
    payload: str
    raw_rows: int

    @property
    def bytes(self) -> int:
        return len(self.payload)


@dataclass
class MessageLog:
    entries: list[LogEntry] = field(default_factory=list)

    def record(self, round_: str, direction: str, country: str, payload: str) -> LogEntry:
        try:
            body = json.loads(payload)
            kind = body.get("query_type") or body.get("response_type") or body.get("status")
        except (json.JSONDecodeError, AttributeError):
            kind = "free_text"
        entry = LogEntry(round_, direction, country, str(kind), payload, count_raw_rows(payload))
        self.entries.append(entry)
        return entry

    @property
    def messages_exchanged(self) -> int:
        return len(self.entries)

    @property
    def aggregate_result_objects(self) -> int:
        return sum(
            1
            for e in self.entries
            if e.direction == "node->coordinator" and e.kind in {"syndrome_scan", "follow_up"}
        )

    @property
    def raw_rows_received(self) -> int:
        return sum(e.raw_rows for e in self.entries if e.direction == "node->coordinator")
