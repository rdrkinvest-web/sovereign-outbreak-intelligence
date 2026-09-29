"""National Ministry-of-Health agent: deterministic path and shared builders (Spec §5A).

Parses a schema-valid query, runs the node's policy-enforcing toolbox, and
returns schema-valid aggregates; anything else is refused. This is also the
fallback whenever the node's AI agent (node_agent.py) is off or fails, and the
refusal pre-filter that keeps raw-data requests away from any model.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd
from pydantic import ValidationError

from .audit import assert_aggregate_only
from .countries import NodeConfig
from .node_tools import TOPIC_TOOL, NodeToolbox
from .policies import POLICIES, SharingPolicy
from .schemas import (
    QUERY_ADAPTER,
    Anomaly,
    FollowUpQuery,
    FollowUpResponse,
    NodeResponse,
    Query,
    Refusal,
    ScanResponse,
    SyndromeScanQuery,
)

_RAW_REQUEST = re.compile(
    r"raw|record|rows?\b|patient[- ]level|line[- ]list|export|dump|csv", re.IGNORECASE
)


def parse_or_refuse(payload: str) -> Query | Refusal:
    """A schema-valid query, or the refusal for anything else (no model involved)."""
    try:
        return QUERY_ADAPTER.validate_json(payload)
    except ValidationError:
        pass
    try:
        query_type = str(json.loads(payload).get("query_type", ""))
    except (json.JSONDecodeError, AttributeError):
        query_type = ""
    if _RAW_REQUEST.search(query_type) or _RAW_REQUEST.search(payload):
        return Refusal(reason="raw_patient_data_not_shareable")
    return Refusal(reason="unsupported_query")


def toolbox_for(
    node: NodeConfig, data: pd.DataFrame, policy: SharingPolicy, query: Query
) -> NodeToolbox:
    return NodeToolbox(
        node, data, policy, query.time_window_days, query.baseline_days, query.region_scope
    )


def scan_response(country: str, scan: dict[str, Any], **notes: Any) -> ScanResponse:
    """Build the scan reply from a scan_signals tool output (provenance by construction)."""
    return ScanResponse(
        country=country,
        sample_size=scan["sample_size"],
        anomalies=[
            Anomaly(
                signal=s["signal"],
                direction=s["direction"],
                strength=s["strength"],
                baseline_value=s["baseline_value"],
                current_value=s["current_value"],
                pct_change=s["pct_change"],
            )
            for s in scan["signals"]
            if s["anomalous"]
        ],
        malaria_signal=scan["malaria_signal"],
        assessment=scan["assessment"],
        **notes,
    )


def follow_up_response(
    country: str, query: FollowUpQuery, output: dict[str, Any], **notes: Any
) -> FollowUpResponse:
    """Build the follow-up reply from the topic's canonical tool output."""
    metrics = {k: v for k, v in output.items() if k != "answer"}
    return FollowUpResponse(
        country=country,
        question_id=query.question_id,
        answer=output["answer"],
        metrics=metrics,
        **notes,
    )


class NationalAgent:
    """Answers coordinator queries from one country's local data, without a model."""

    def __init__(
        self, node: NodeConfig, data: pd.DataFrame, policy: SharingPolicy | None = None
    ) -> None:
        self.node = node
        self.policy = policy or POLICIES[node.country]
        self._data = data

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
        if isinstance(query, Refusal):
            return query
        return self.answer(query)

    def answer(self, query: Query) -> NodeResponse:
        toolbox = toolbox_for(self.node, self._data, self.policy, query)
        notes = {"policy_notes": self.policy.describe()}
        if isinstance(query, SyndromeScanQuery):
            scan = toolbox.call("auto-scan", "scan_signals", {})
            return scan_response(self.country, scan, tools_used=toolbox.calls, **notes)
        tool, args = TOPIC_TOOL[query.question_id]
        output = toolbox.call("auto-follow-up", tool, args)
        return follow_up_response(self.country, query, output, tools_used=toolbox.calls, **notes)
