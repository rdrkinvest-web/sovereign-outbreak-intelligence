"""Regional coordinator (Spec §5B, §7).

A fixed three-round state machine: scan all countries, compare, ask one
follow-up of the countries sharing the pattern, then stop. The coordinator
only ever sees JSON replies through a Transport; it has no access to national
data, file paths or node configuration.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import mean
from typing import Protocol

from . import events
from .audit import MessageLog
from .events import EventSink
from .schemas import (
    FOLLOW_UP_QUESTIONS,
    RESPONSE_ADAPTER,
    FinalAssessment,
    FollowUpChoice,
    FollowUpId,
    FollowUpQuery,
    FollowUpRecord,
    FollowUpResponse,
    ScanResponse,
    SyndromeScanQuery,
)

MIN_COUNTRIES_FOR_PATTERN = 2


class Transport(Protocol):
    """Delivers payloads to national nodes and returns their replies."""

    def countries(self) -> list[str]: ...

    def exchange(self, payloads: dict[str, str]) -> dict[str, str]:
        """Send one payload per country; return one reply per country."""
        ...


@dataclass
class Investigation:
    scans: dict[str, ScanResponse] = field(default_factory=dict)
    refusals: dict[str, str] = field(default_factory=dict)
    shared_signals: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    follow_up: FollowUpRecord | None = None
    log: MessageLog = field(default_factory=MessageLog)


# Called after each round so a UI or terminal can narrate progress.
Observer = Callable[[str, Investigation], None]


def _emit_nothing(_event: events.Event) -> None:
    pass


def _exchange(
    transport: Transport,
    round_: str,
    payloads: dict[str, str],
    log: MessageLog,
    emit: EventSink = _emit_nothing,
    question: str = "",
) -> dict[str, object]:
    sent = [log.record(round_, "coordinator->node", c, p) for c, p in payloads.items()]
    emit(events.question_event(round_, sent, question))
    started = time.monotonic()
    replies = transport.exchange(payloads)
    elapsed = time.monotonic() - started
    missing = set(payloads) - set(replies)
    if missing:
        raise RuntimeError(f"No reply from: {', '.join(sorted(missing))}")
    parsed = {}
    for country in payloads:
        entry = log.record(round_, "node->coordinator", country, replies[country])
        parsed[country] = RESPONSE_ADAPTER.validate_json(replies[country])
        emit(events.answer_event(round_, entry, parsed[country], elapsed))
    return parsed


def compare(scans: dict[str, ScanResponse]) -> dict[tuple[str, str], list[str]]:
    """Group countries by (signal, direction) anomalies they share."""
    shared: dict[tuple[str, str], list[str]] = {}
    for country, scan in scans.items():
        for a in scan.anomalies:
            shared.setdefault((a.signal, a.direction), []).append(country)
    return {
        key: sorted(countries)
        for key, countries in shared.items()
        if len(countries) >= MIN_COUNTRIES_FOR_PATTERN
    }


def pattern_countries(shared: dict[tuple[str, str], list[str]]) -> list[str]:
    """Countries that carry every shared anomaly of the strongest cluster."""
    if not shared:
        return []
    groups: dict[tuple[str, ...], int] = {}
    for countries in shared.values():
        groups[tuple(countries)] = groups.get(tuple(countries), 0) + 1
    best = max(groups, key=lambda g: (groups[g], len(g)))
    return list(best)


def choose_follow_up(shared: dict[tuple[str, str], list[str]]) -> FollowUpId:
    """Deterministic choice from the fixed menu, based on round-1 evidence."""
    if ("platelets", "down") in shared:
        return "platelets_in_febrile"
    if ("alt", "up") in shared:
        return "alt_region_time"
    return "malaria_positivity"


def rule_choice(inv: Investigation, reason: str = "") -> FollowUpChoice:
    question_id = choose_follow_up(inv.shared_signals)
    return FollowUpChoice(
        question_id=question_id,
        question=FOLLOW_UP_QUESTIONS[question_id],
        rationale=reason or "Chosen by the fixed rule: the strongest shared marker is checked first.",
        chosen_by="rule",
    )


# Chooses round 3's question from the fixed menu; may be model-backed.
Chooser = Callable[[Investigation], FollowUpChoice]

SCAN_QUESTION = (
    "Please scan your border regions: compared with the previous 60 days, is there "
    "unusual febrile illness in the last 14 days, and do laboratory markers or malaria "
    "positivity explain it? Share aggregates only."
)


def investigate(
    transport: Transport,
    follow_up: FollowUpId | None = None,
    observe: Observer | None = None,
    choose: Chooser | None = None,
    on_event: EventSink | None = None,
) -> tuple[FinalAssessment, Investigation]:
    inv = Investigation()
    notify = observe or (lambda _round, _inv: None)
    emit = on_event or _emit_nothing
    countries = transport.countries()

    # Round 1: the same scan for every country.
    emit(events.round_event("1-scan"))
    scan = SyndromeScanQuery(question=SCAN_QUESTION).model_dump_json()
    for country, reply in _exchange(
        transport, "1-scan", {c: scan for c in countries}, inv.log, emit, SCAN_QUESTION
    ).items():
        if isinstance(reply, ScanResponse):
            inv.scans[country] = reply
        else:
            inv.refusals[country] = reply.model_dump_json()
    notify("1-scan", inv)

    # Round 2: compare locally; no messages.
    emit(events.round_event("2-compare"))
    inv.shared_signals = compare(inv.scans)
    cluster = pattern_countries(inv.shared_signals)
    emit(events.compare_event(inv.shared_signals))
    notify("2-compare", inv)

    # Round 3: one targeted follow-up to the countries sharing the pattern.
    if cluster:
        if follow_up:
            choice = FollowUpChoice(
                question_id=follow_up,
                question=FOLLOW_UP_QUESTIONS[follow_up],
                rationale="Requested explicitly.",
                chosen_by="rule",
            )
        elif choose:
            try:
                choice = choose(inv)
            except Exception as err:  # the script continues on the fixed rule
                choice = rule_choice(inv, f"Fixed rule used; model choice failed ({err}).")
        else:
            choice = rule_choice(inv)
        emit(events.round_event("3-verify"))
        emit(events.followup_event(choice, cluster))
        query = FollowUpQuery(
            question_id=choice.question_id, question=choice.question
        ).model_dump_json()
        replies = _exchange(
            transport, "3-verify", {c: query for c in cluster}, inv.log, emit, choice.question
        )
        answers = {c: r for c, r in replies.items() if isinstance(r, FollowUpResponse)}
        inv.follow_up = FollowUpRecord(
            question_id=choice.question_id,
            question=choice.question,
            rationale=choice.rationale,
            chosen_by=choice.chosen_by,
            asked=cluster,
            answers={c: r.answer for c, r in answers.items()},
            metrics={c: r.metrics for c, r in answers.items()},
            interpretations={c: r.interpretation for c, r in answers.items() if r.interpretation},
            modes={c: r.mode for c, r in answers.items()},
        )
    notify("3-verify", inv)

    assessment = assess(inv, countries, cluster)
    emit(events.assessment_event(assessment))
    return assessment, inv


def _supporting_follow_up(record: FollowUpRecord) -> list[str]:
    """Plain-language evidence from a follow-up, per question."""
    who = " and ".join(record.asked)
    yes = [c for c, a in record.answers.items() if a == "yes"]
    q = record.question_id
    if q == "malaria_positivity" and not yes:
        return [f"Follow-up: malaria positivity did not materially change in {who}"]
    if q == "platelets_in_febrile" and yes:
        return [f"Follow-up: the platelet decrease is concentrated among febrile patients in {' and '.join(yes)}"]
    if q == "alt_region_time" and yes:
        return [f"Follow-up: the ALT increase is confined to border regions in {' and '.join(yes)}"]
    if q == "volume_adjusted" and yes:
        return [f"Follow-up: the fever anomaly persists after adjusting for visit volume in {' and '.join(yes)}"]
    if q == "signal_onset":
        # Countries may share onset only at week precision; compare at the
        # finest precision every country shared.
        dates = {c: m.get("onset_date") for c, m in record.metrics.items()}
        weeks = {c: m.get("onset_week") for c, m in record.metrics.items()}
        if len(dates) >= 2 and all(dates.values()):
            order = sorted(dates, key=lambda c: str(dates[c]))
            return ["Follow-up: signal onset " + ", then ".join(f"{c} ({dates[c]})" for c in order)]
        if len(weeks) >= 2 and all(weeks.values()):
            if len(set(weeks.values())) == 1:
                return [f"Follow-up: {who} signals began in the same week ({next(iter(weeks.values()))})"]
            order = sorted(weeks, key=lambda c: str(weeks[c]))
            return ["Follow-up: signal onset " + ", then ".join(f"{c} ({weeks[c]})" for c in order)]
    return []


MODERATE_STRENGTH = 0.6


def uncertainties(inv: Investigation, cluster: list[str], control: list[str]) -> list[str]:
    """What limits this conclusion, derived from the evidence actually returned.

    The synthetic-data disclaimer is not repeated here; it labels the whole
    output (FinalAssessment.label and the report header and footer).
    """
    notes = ["No pathogen confirmation: the signal rests on syndromic and routine laboratory markers"]

    if len(control) == 1:
        notes.append(f"Only one control country ({control[0]}) to compare against")
    elif not control:
        notes.append("No control country near baseline to compare against")

    for signal, direction in inv.shared_signals:
        weak = [
            f"{c} {a.strength:.2f}"
            for c in cluster
            for a in inv.scans[c].anomalies
            if (a.signal, a.direction) == (signal, direction) and a.strength < MODERATE_STRENGTH
        ]
        if weak:
            notes.append(f"The shared {signal} signal is only moderate ({', '.join(weak)})")

    for c in cluster:
        if inv.scans[c].malaria_signal == "insufficient_data":
            notes.append(f"Malaria positivity could not be assessed in {c}: too few tests")

    record = inv.follow_up
    if record:
        for c, metrics in record.metrics.items():
            if record.answers.get(c) == "declined":
                notes.append(f"{c} declined the follow-up under its data-sharing policy")
            elif record.question_id == "signal_onset" and metrics.get("onset_week") and not metrics.get("onset_date"):
                notes.append(f"{c} shares onset by week only, so onset order within that week cannot be resolved")
            elif record.answers.get(c) == "inconclusive":
                notes.append(f"{c}'s follow-up was inconclusive (statistics suppressed for small counts)")
        fallbacks = sorted(c for c, m in record.modes.items() if m == "deterministic_fallback")
    else:
        fallbacks = []
    fallbacks = sorted(
        set(fallbacks) | {c for c in cluster if inv.scans[c].mode == "deterministic_fallback"}
    )
    if fallbacks:
        notes.append(
            f"{' and '.join(fallbacks)}: answered deterministically, without an analyst "
            "agent's local interpretation"
        )
    return notes


def assess(
    inv: Investigation, countries: list[str], cluster: list[str]
) -> FinalAssessment:
    control = [c for c in countries if c not in cluster and c in inv.scans]
    supporting, contradictory = [], []

    if cluster:
        shared = [f"{s} {d}" for (s, d), cs in inv.shared_signals.items() if cs == cluster]
        supporting.append(
            f"{' and '.join(cluster)} independently report aligned anomalies: {', '.join(shared)}"
        )
    for c in control:
        if inv.scans[c].assessment == "near_baseline":
            supporting.append(f"{c} remains near its historical baseline")
        else:
            contradictory.append(f"{c} reports {inv.scans[c].assessment} with a different profile")
    malaria = {c: inv.scans[c].malaria_signal for c in cluster}
    if cluster and all(v == "no_material_change" for v in malaria.values()):
        supporting.append("Malaria positivity does not explain the change")
    for c, v in malaria.items():
        if v in {"increased", "decreased"}:
            contradictory.append(f"Malaria positivity {v} in {c}")

    confidence = 0.0
    if inv.follow_up:
        supporting.extend(_supporting_follow_up(inv.follow_up))
        for c, a in inv.follow_up.answers.items():
            if a != "yes" and inv.follow_up.question_id != "malaria_positivity":
                contradictory.append(f"Follow-up answer from {c} was '{a}'")
    if cluster:
        strengths = [
            a.strength
            for c in cluster
            for a in inv.scans[c].anomalies
            if (a.signal, a.direction) in inv.shared_signals
        ]
        confidence = round(mean(strengths) * (1.0 if not contradictory else 0.8), 2)
    for c in inv.refusals:
        contradictory.append(f"{c} declined the scan")

    return FinalAssessment(
        cross_border_pattern=len(cluster) >= MIN_COUNTRIES_FOR_PATTERN,
        countries_with_pattern=cluster,
        control_countries=control,
        demo_confidence_score=confidence,
        supporting_evidence=supporting,
        contradictory_evidence=contradictory,
        uncertainties=uncertainties(inv, cluster, control),
        recommended_next_step=(
            "request targeted confirmatory testing in "
            + " and ".join(cluster)
            + " border regions"
            if cluster
            else "continue routine surveillance"
        ),
        follow_up=inv.follow_up,
        messages_exchanged=inv.log.messages_exchanged,
        aggregate_result_objects=inv.log.aggregate_result_objects,
        raw_patient_rows_received=inv.log.raw_rows_received,
    )
