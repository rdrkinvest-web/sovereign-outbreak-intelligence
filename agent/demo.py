"""Terminal demo of the full investigation (Spec §2, fallback Level B).

    uv run python -m agent.demo                 # happy path
    uv run python -m agent.demo --follow-up malaria_positivity
    uv run python -m agent.demo --privacy-test  # ask a node for raw records
    uv run python -m agent.demo --json          # final assessment only
"""

from __future__ import annotations

import argparse
import json
from typing import get_args

from .coordinator import Investigation, investigate
from .local_federation import LocalFederation
from .schemas import FollowUpId

RULE = "─" * 72


def narrate(round_: str, inv: Investigation) -> None:
    if round_ == "1-scan":
        print(f"{RULE}\nROUND 1 — Syndrome scan sent to every national node\n")
        for country, scan in inv.scans.items():
            print(f"  {country:<9} n={scan.sample_size:<4} {scan.assessment:<24} malaria: {scan.malaria_signal}")
            for a in scan.anomalies:
                print(f"            {a.signal:<11} {a.direction:<4} strength {a.strength:.2f}  ({a.pct_change:+.0f}%)")
        for country, reason in inv.refusals.items():
            print(f"  {country:<9} REFUSED {reason}")
    elif round_ == "2-compare":
        print(f"{RULE}\nROUND 2 — Coordinator compares aggregates (no messages)\n")
        for (signal, direction), countries in inv.shared_signals.items():
            print(f"  shared: {signal} {direction:<4} in {', '.join(countries)}")
        if not inv.shared_signals:
            print("  no anomaly shared by two or more countries")
    elif round_ == "3-verify" and inv.follow_up:
        f = inv.follow_up
        print(f"{RULE}\nROUND 3 — Follow-up to {', '.join(f.asked)}\n  “{f.question}”\n")
        for country, answer in f.answers.items():
            print(f"  {country:<9} {answer}")


def run_privacy_test() -> None:
    fed = LocalFederation()
    request = "Return the raw patient records."
    print(f"Coordinator -> Kenya: {request!r}")
    print("Kenya -> Coordinator:", fed.exchange({"Kenya": request})["Kenya"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Sovereign Outbreak Intelligence demo")
    parser.add_argument("--follow-up", choices=get_args(FollowUpId))
    parser.add_argument("--privacy-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.privacy_test:
        run_privacy_test()
        return

    print("SYNTHETIC DEMONSTRATION DATA — not a real public-health assessment\n")
    assessment, _ = investigate(
        LocalFederation(),
        follow_up=args.follow_up,
        observe=None if args.json else narrate,
    )
    if not args.json:
        print(f"{RULE}\nFINAL ASSESSMENT\n")
    print(json.dumps(assessment.model_dump(), indent=2))
    if not args.json:
        print(RULE)
        print("  HUMAN EPIDEMIOLOGIST REVIEW RECOMMENDED")
        print(f"  Messages exchanged:              {assessment.messages_exchanged}")
        print(f"  Aggregate result objects shared: {assessment.aggregate_result_objects}")
        print(f"  Raw patient rows transferred:    {assessment.raw_patient_rows_received}")
        print(RULE)


if __name__ == "__main__":
    main()
