"""Schema-validated JSON contract between coordinator and national nodes (Spec §6).

Every model forbids unknown fields, so a message can only carry what is
declared here. No model has a field for patient-level records.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

DEMO_LABEL = "synthetic demonstration data; scores are demonstration anomaly scores"

Signal = Literal["fever", "platelets", "alt", "crp", "malaria_test"]
RegionScope = Literal["border_regions", "all"]
Answer = Literal["yes", "no", "inconclusive", "declined"]
Mode = Literal["agent", "deterministic_fallback"]
Scalar = float | int | str | bool | None
MalariaSignal = Literal[
    "no_material_change", "increased", "decreased", "insufficient_data", "not_requested"
]
FollowUpId = Literal[
    "malaria_positivity",
    "platelets_in_febrile",
    "alt_region_time",
    "signal_onset",
    "volume_adjusted",
]

# The fixed follow-up menu (Spec §7). The coordinator may choose only from these.
FOLLOW_UP_QUESTIONS: dict[str, str] = {
    "malaria_positivity": "Did malaria positivity materially change in the same window?",
    "platelets_in_febrile": "Is the platelet shift concentrated among febrile observations?",
    "alt_region_time": "Is the ALT increase concentrated in the same region and time window?",
    "signal_onset": "Did the signal begin before or after the neighboring country's signal?",
    "volume_adjusted": "Is the anomaly still present after adjusting for total test volume?",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Coordinator -> node ---------------------------------------------------


class SyndromeScanQuery(Strict):
    query_type: Literal["syndrome_scan"] = "syndrome_scan"
    question: str | None = Field(None, max_length=400)  # plain language; fields below are authoritative
    time_window_days: int = Field(14, ge=1, le=30)
    baseline_days: int = Field(60, ge=7, le=180)
    region_scope: RegionScope = "border_regions"
    signals: list[Signal] = ["fever", "platelets", "alt", "crp", "malaria_test"]


class FollowUpQuery(Strict):
    query_type: Literal["follow_up"] = "follow_up"
    question_id: FollowUpId
    question: str | None = Field(None, max_length=400)
    time_window_days: int = Field(14, ge=1, le=30)
    baseline_days: int = Field(60, ge=7, le=180)
    region_scope: RegionScope = "border_regions"


Query = SyndromeScanQuery | FollowUpQuery
QUERY_ADAPTER: TypeAdapter[Query] = TypeAdapter(Query)


# --- Node -> coordinator ---------------------------------------------------


class Anomaly(Strict):
    signal: str
    direction: Literal["up", "down"]
    strength: float = Field(ge=0, le=1)
    baseline_value: float
    current_value: float
    pct_change: float


class Evidence(Strict):
    """One number the node agent cites, traceable to a recorded tool output."""

    source_call_id: str = Field(max_length=100)
    metric: str = Field(max_length=60)
    value: Scalar


class AgentNotes(Strict):
    """What a node's AI agent adds on top of the computed aggregates."""

    interpretation: str = Field("", max_length=600)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)
    policy_notes: list[str] = Field(default_factory=list, max_length=5)
    tools_used: list[str] = Field(default_factory=list, max_length=10)
    mode: Mode = "deterministic_fallback"


class ScanResponse(AgentNotes):
    status: Literal["ok"] = "ok"
    response_type: Literal["syndrome_scan"] = "syndrome_scan"
    country: str
    sample_size: int
    anomalies: list[Anomaly]
    malaria_signal: MalariaSignal
    assessment: Literal["unusual_febrile_cluster", "isolated_anomaly", "near_baseline"]
    data_shared: Literal["aggregates_only"] = "aggregates_only"
    label: str = DEMO_LABEL


class FollowUpResponse(AgentNotes):
    status: Literal["ok"] = "ok"
    response_type: Literal["follow_up"] = "follow_up"
    country: str
    question_id: FollowUpId
    answer: Answer
    metrics: dict[str, Scalar]
    data_shared: Literal["aggregates_only"] = "aggregates_only"
    label: str = DEMO_LABEL


class Refusal(Strict):
    status: Literal["refused"] = "refused"
    reason: Literal["raw_patient_data_not_shareable", "unsupported_query"]


NodeResponse = ScanResponse | FollowUpResponse | Refusal
RESPONSE_ADAPTER: TypeAdapter[NodeResponse] = TypeAdapter(NodeResponse)


# --- Coordinator -> human reviewer -----------------------------------------


class FollowUpChoice(Strict):
    """The coordinator's follow-up decision: a menu topic, its wording and why."""

    question_id: FollowUpId
    question: str = Field(max_length=400)
    rationale: str = Field(max_length=400)
    chosen_by: Literal["model", "rule"]


class FollowUpRecord(Strict):
    question_id: FollowUpId
    question: str
    rationale: str = ""
    chosen_by: Literal["model", "rule"] = "rule"
    asked: list[str]
    answers: dict[str, Answer]
    metrics: dict[str, dict[str, Scalar]]
    interpretations: dict[str, str] = Field(default_factory=dict)
    modes: dict[str, Mode] = Field(default_factory=dict)


class FinalAssessment(Strict):
    status: Literal["human_review_recommended"] = "human_review_recommended"
    cross_border_pattern: bool
    countries_with_pattern: list[str]
    control_countries: list[str]
    demo_confidence_score: float = Field(ge=0, le=1)
    supporting_evidence: list[str]
    contradictory_evidence: list[str]
    uncertainties: list[str]
    recommended_next_step: str
    follow_up: FollowUpRecord | None
    messages_exchanged: int
    aggregate_result_objects: int
    raw_patient_rows_received: int
    label: str = DEMO_LABEL
