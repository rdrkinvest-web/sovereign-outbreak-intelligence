"""Aggregate-only analytics tools for a national node, under its sharing policy.

This is the privacy wall's inner layer. The node's AI agent can reach local
data only through these tools; every tool returns aggregates, applies the
country's policy, and is audited before its output is released, including to
the model (whose provider sits outside the node). The time window, baseline
and region scope come from the coordinator's structured query, so the model
cannot widen them. Outputs are recorded by call id so the node can later
prove that every number in its answer came from a tool.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from . import analytics
from .audit import assert_aggregate_only
from .countries import NodeConfig
from .policies import SharingPolicy

Output = dict[str, Any]

MARKERS = ["platelets", "alt", "crp"]


def _function(name: str, description: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    properties = properties or {}
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }


_MARKER_ARG = {"marker": {"type": "string", "enum": MARKERS, "description": "Laboratory marker."}}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    _function(
        "scan_signals",
        "Compare fever rate, platelets, ALT and CRP in the query window against the "
        "baseline, and check malaria positivity. Returns aggregate statistics only.",
    ),
    _function(
        "malaria_positivity",
        "Whether malaria test positivity materially changed between baseline and window.",
    ),
    _function(
        "marker_in_febrile_cohort",
        "Whether a marker shift is concentrated among febrile rather than afebrile visits.",
        _MARKER_ARG,
    ),
    _function(
        "marker_by_region",
        "Whether a marker shift is concentrated in border regions rather than the interior.",
        _MARKER_ARG,
    ),
    _function("signal_onset", "When the fever signal began, at the policy's date precision."),
    _function(
        "visit_volume",
        "Visits and malaria tests per day, and whether the fever anomaly persists as a "
        "rate per visit (i.e. after adjusting for volume).",
    ),
]
TOOL_NAMES = frozenset(t["name"] for t in TOOL_SCHEMAS)

# The tool whose output settles each follow-up topic (and its fixed arguments).
TOPIC_TOOL: dict[str, tuple[str, dict[str, Any]]] = {
    "malaria_positivity": ("malaria_positivity", {}),
    "platelets_in_febrile": ("marker_in_febrile_cohort", {"marker": "platelets"}),
    "alt_region_time": ("marker_by_region", {"marker": "alt"}),
    "signal_onset": ("signal_onset", {}),
    "volume_adjusted": ("visit_volume", {}),
}

# What "yes" means for each topic, answerable from one country's own data.
# Cross-country comparisons (e.g. which onset came first) are the coordinator's job.
ANSWER_MEANING: dict[str, str] = {
    "malaria_positivity": "yes = malaria positivity materially changed in your data",
    "platelets_in_febrile": "yes = your platelet shift is concentrated among febrile visits",
    "alt_region_time": "yes = your ALT shift is concentrated in your border regions; declined = policy forbids the breakdown",
    "signal_onset": (
        "yes = you detected an onset of the fever signal in your own data. You cannot see "
        "other countries; report your onset and the coordinator compares them"
    ),
    "volume_adjusted": "yes = your fever anomaly persists as a rate per visit",
}


class NodeToolbox:
    def __init__(
        self,
        node: NodeConfig,
        data: pd.DataFrame,
        policy: SharingPolicy,
        time_window_days: int,
        baseline_days: int,
        region_scope: str,
    ) -> None:
        self.node = node
        self.policy = policy
        self._data = data
        self._window_days = time_window_days
        self._baseline_days = baseline_days
        self._region_scope = region_scope
        self.outputs: dict[str, Output] = {}
        self.log: dict[str, tuple[str, dict[str, Any]]] = {}
        self.calls: list[str] = []

    def call(self, call_id: str, name: str, arguments: dict[str, Any]) -> Output:
        """Run one tool, audit its output, and record it under `call_id`."""
        if name not in TOOL_NAMES:
            raise ValueError(f"Tool {name!r} is not available on this node.")
        output = getattr(self, f"_{name}")(**arguments)
        assert_aggregate_only(json.dumps(output))
        self.outputs[call_id] = output
        self.log[call_id] = (name, dict(arguments))
        self.calls.append(name if not arguments else f"{name}({', '.join(map(str, arguments.values()))})")
        return output

    # --- executors ---------------------------------------------------------

    def _periods(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        scoped = analytics.in_scope(self._data, self._region_scope, self.node.border_regions)
        return analytics.split_periods(scoped, self._window_days, self._baseline_days)

    def _scan_signals(self) -> Output:
        with analytics.cell_suppression(self.policy.min_cell):
            result = analytics.local_scan(
                self._data,
                self.node.border_regions,
                self._window_days,
                self._baseline_days,
                self._region_scope,
                ["fever", "platelets", "alt", "crp", "malaria_test"],
            )
        return {
            "sample_size": result.sample_size,
            "assessment": result.assessment,
            "malaria_signal": result.malaria_signal,
            "signals": [
                {
                    "signal": c.signal,
                    "baseline_value": c.baseline_value,
                    "current_value": c.current_value,
                    "pct_change": c.pct_change,
                    "z": c.z,
                    "direction": c.direction,
                    "anomalous": c.anomalous,
                    "strength": c.strength,
                }
                for c in result.comparisons
            ],
        }

    def _followup(self, fn: Any, *args: Any) -> Output:
        with analytics.cell_suppression(self.policy.min_cell):
            answer, metrics = fn(*args)
        return {"answer": answer, **metrics}

    def _malaria_positivity(self) -> Output:
        return self._followup(analytics.check_malaria_positivity, *self._periods())

    def _marker_in_febrile_cohort(self, marker: str) -> Output:
        return self._followup(analytics.check_marker_in_febrile_cohort, *self._periods(), marker)

    def _marker_by_region(self, marker: str) -> Output:
        if not self.policy.allow_region_breakdown:
            return {
                "answer": "declined",
                "declined_reason": "Sub-national breakdowns are not shared under this country's policy.",
            }
        return self._followup(
            analytics.check_marker_by_region,
            self._data,
            self.node.border_regions,
            self._window_days,
            self._baseline_days,
            marker,
        )

    def _signal_onset(self) -> Output:
        output = self._followup(analytics.check_signal_onset, *self._periods())
        onset = output.get("onset_date")
        if onset:
            year, week, _ = pd.Timestamp(onset).isocalendar()
            output["onset_week"] = f"{year}-W{week:02d}"
            if self.policy.onset_precision == "week":
                output["onset_date"] = None
        else:
            output["onset_week"] = None
        return output

    def _visit_volume(self) -> Output:
        return self._followup(
            analytics.check_test_volume, *self._periods(), self._window_days, self._baseline_days
        )


def find_value(output: Output, metric: str) -> tuple[bool, Any]:
    """Look up `metric` in a tool output; dotted paths reach into scan signals."""
    if metric in output:
        return True, output[metric]
    if "." in metric and "signals" in output:  # e.g. "platelets.pct_change"
        signal, field = metric.split(".", 1)
        for s in output["signals"]:
            if s["signal"] == signal and field in s:
                return True, s[field]
    return False, None
