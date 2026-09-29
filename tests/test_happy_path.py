"""Spec §16: signal, cross-border reasoning, follow-up and reliability tests."""

from __future__ import annotations

from typing import get_args

import pytest

from agent.coordinator import investigate
from agent.local_federation import LocalFederation
from agent.schemas import FollowUpId


@pytest.fixture(scope="module")
def federation() -> LocalFederation:
    return LocalFederation()


def test_outbreak_countries_score_stronger_than_control(federation: LocalFederation) -> None:
    _, inv = investigate(federation)

    def strength(country: str, signal: str) -> float:
        return next(
            (a.strength for a in inv.scans[country].anomalies if a.signal == signal), 0.0
        )

    for signal in ("fever_rate", "platelets", "alt"):
        assert strength("Kenya", signal) > strength("Tanzania", signal)
        assert strength("Uganda", signal) > strength("Tanzania", signal)
    assert inv.scans["Tanzania"].assessment == "near_baseline"


def test_cross_border_conclusion(federation: LocalFederation) -> None:
    assessment, _ = investigate(federation)
    assert assessment.status == "human_review_recommended"
    assert assessment.cross_border_pattern
    assert assessment.countries_with_pattern == ["Kenya", "Uganda"]
    assert assessment.control_countries == ["Tanzania"]
    assert "Malaria positivity does not explain the change" in assessment.supporting_evidence


def test_follow_up_round_occurs(federation: LocalFederation) -> None:
    assessment, inv = investigate(federation)
    assert assessment.follow_up is not None
    assert {e.round for e in inv.log.entries} == {"1-scan", "3-verify"}
    # 3 scans + 3 replies + 2 follow-ups + 2 replies
    assert assessment.messages_exchanged == 10
    assert assessment.aggregate_result_objects == 5


@pytest.mark.parametrize("question_id", get_args(FollowUpId))
def test_every_follow_up_supports_the_pattern(
    federation: LocalFederation, question_id: str
) -> None:
    assessment, _ = investigate(federation, follow_up=question_id)
    assert set(assessment.follow_up.answers) == {"Kenya", "Uganda"}
    expected = "no" if question_id == "malaria_positivity" else "yes"
    assert set(assessment.follow_up.answers.values()) == {expected}


def test_onset_respects_each_countrys_precision_policy(federation: LocalFederation) -> None:
    assessment, _ = investigate(federation, follow_up="signal_onset")
    kenya, uganda = (assessment.follow_up.metrics[c] for c in ("Kenya", "Uganda"))
    assert kenya["onset_date"] == "2026-09-17"
    assert uganda["onset_date"] is None  # Uganda shares week precision only
    assert kenya["onset_week"] == uganda["onset_week"] == "2026-W38"
    assert any("same week" in e for e in assessment.supporting_evidence)


def test_uncertainties_come_from_evidence_not_boilerplate(federation: LocalFederation) -> None:
    assessment, _ = investigate(federation, follow_up="signal_onset")
    text = " ".join(assessment.uncertainties).lower()
    assert "synthetic" not in text and "demonstration" not in text
    assert "only one control country (tanzania)" in text
    assert "uganda shares onset by week only" in text
    assert assessment.label.startswith("synthetic demonstration data")  # labelled once, on the output


def test_happy_path_is_deterministic_three_times() -> None:
    results = [investigate(LocalFederation())[0].model_dump_json() for _ in range(3)]
    assert len(set(results)) == 1
