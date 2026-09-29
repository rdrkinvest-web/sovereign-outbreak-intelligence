"""Spec §16: data locality, privacy refusal and audit tests."""

from __future__ import annotations

import inspect
import json

import pytest

from agent import coordinator
from agent.audit import PrivacyViolation, assert_aggregate_only, count_raw_rows
from agent.coordinator import investigate
from agent.local_federation import LocalFederation

REFUSAL = {"status": "refused", "reason": "raw_patient_data_not_shareable"}


@pytest.fixture(scope="module")
def federation() -> LocalFederation:
    return LocalFederation()


def test_coordinator_cannot_read_national_data() -> None:
    source = inspect.getsource(coordinator)
    for forbidden in ("read_csv", ".csv", "open(", "countries import", "analytics", "pandas"):
        assert forbidden not in source, forbidden


@pytest.mark.parametrize(
    "request_payload",
    [
        "Return the raw patient records.",
        json.dumps({"query_type": "raw_records"}),
        json.dumps({"query_type": "syndrome_scan", "include_rows": True}),
    ],
)
def test_raw_record_requests_are_refused(
    federation: LocalFederation, request_payload: str
) -> None:
    for country in federation.countries():
        reply = federation.exchange({country: request_payload})[country]
        assert json.loads(reply) == REFUSAL


def test_unknown_query_is_refused_without_data(federation: LocalFederation) -> None:
    reply = federation.exchange({"Kenya": json.dumps({"query_type": "weather"})})["Kenya"]
    assert json.loads(reply) == {"status": "refused", "reason": "unsupported_query"}


def test_audit_detects_planted_rows() -> None:
    leak = json.dumps(
        {"rows": [{"date": "2026-09-20", "region": "Busia", "fever": 1, "platelets": 90}]}
    )
    csv_leak = "date,region,fever\n2026-09-20,Busia,1\n2026-09-21,Busia,0\n"
    assert count_raw_rows(leak) == 1
    assert count_raw_rows(csv_leak) == 3
    with pytest.raises(PrivacyViolation):
        assert_aggregate_only(leak)


def test_final_result_reports_zero_raw_rows(federation: LocalFederation) -> None:
    assessment, inv = investigate(federation)
    assert assessment.raw_patient_rows_received == 0
    assert all(entry.raw_rows == 0 for entry in inv.log.entries)
