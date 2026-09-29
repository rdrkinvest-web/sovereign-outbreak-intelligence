"""Generate the three synthetic national datasets (Spec §4, §12 Step 1).

SYNTHETIC DEMONSTRATION DATA. The marker pattern injected below is invented
for the demo and is not a validated signature of any real disease.

Each row is one clinic visit. A hidden event adds extra febrile visits with
low platelets, high ALT and modestly raised CRP to the border regions of Kenya
and Uganda during the final 14 days. Malaria positivity is deliberately left
unchanged. Tanzania is the control and stays at baseline.

Usage:
    uv run python scripts/generate_synthetic_data.py
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from agent.countries import NODES, NodeConfig

END_DATE = date(2026, 9, 28)
BASELINE_DAYS = 60
WINDOW_DAYS = 14
TOTAL_DAYS = BASELINE_DAYS + WINDOW_DAYS

VISITS_PER_REGION_PER_DAY = 5.0
AGE_BANDS = ["0-4", "5-14", "15-24", "25-44", "45-64", "65+"]
AGE_WEIGHTS = [0.14, 0.18, 0.18, 0.26, 0.16, 0.08]

MALARIA_POSITIVITY = 0.12  # identical for every visit type, so the event cannot move it

# The hidden event. `onset` is the day within the final window when extra
# visits begin; they ramp up linearly over RAMP_DAYS to `peak` per border region.
OUTBREAKS = {
    "Kenya": {"onset": 0, "peak": 4.0},
    "Uganda": {"onset": 4, "peak": 4.5},
}
RAMP_DAYS = 7
SEEDS = {"Kenya": 11, "Uganda": 5, "Tanzania": 37}

COLUMNS = [
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
]


def _test_results(
    rng: np.random.Generator, tested_p: np.ndarray, positive_p: float
) -> np.ndarray:
    tested = rng.random(len(tested_p)) < tested_p
    positive = rng.random(len(tested_p)) < positive_p
    return np.where(tested, np.where(positive, "positive", "negative"), "not_done")


def _visits(
    rng: np.random.Generator, n: int, day: date, region: str, event: bool
) -> pd.DataFrame:
    """Draw `n` visits; `event` visits carry the hidden marker pattern."""
    if event:
        fever = np.ones(n, dtype=int)
        platelets = rng.normal(115, 30, n)
        alt = rng.normal(92, 25, n)
        crp = rng.normal(16, 8, n)
        dengue_positive_p = 0.15
    else:
        fever = (rng.random(n) < 0.16).astype(int)
        platelets = rng.normal(255, 55, n)
        alt = rng.normal(28, 11, n)
        crp = np.where(fever == 1, rng.normal(20, 10, n), rng.normal(5, 3, n))
        dengue_positive_p = 0.06

    return pd.DataFrame(
        {
            "date": day.isoformat(),
            "region": region,
            "age_band": rng.choice(AGE_BANDS, n, p=AGE_WEIGHTS),
            "fever": fever,
            "rash": (rng.random(n) < 0.04).astype(int),
            "cough": (rng.random(n) < 0.12).astype(int),
            "diarrhea": (rng.random(n) < 0.06).astype(int),
            "platelets": np.clip(platelets, 15, 600).round().astype(int),
            "alt": np.clip(alt, 5, 400).round().astype(int),
            "crp": np.clip(crp, 0.5, 300).round(1),
            "malaria_test": _test_results(
                rng, np.where(fever == 1, 0.70, 0.08), MALARIA_POSITIVITY
            ),
            "dengue_test": _test_results(
                rng, np.where(fever == 1, 0.10, 0.01), dengue_positive_p
            ),
        },
        columns=COLUMNS,
    )


def generate(node: NodeConfig) -> pd.DataFrame:
    rng = np.random.default_rng(SEEDS[node.country])
    outbreak = OUTBREAKS.get(node.country)
    frames = []
    for i in range(TOTAL_DAYS):
        day = END_DATE - timedelta(days=TOTAL_DAYS - 1 - i)
        window_day = i - BASELINE_DAYS
        for region in node.regions:
            frames.append(
                _visits(rng, rng.poisson(VISITS_PER_REGION_PER_DAY), day, region, False)
            )
            if (
                outbreak
                and region in node.border_regions
                and window_day >= outbreak["onset"]
            ):
                ramp = min(1.0, (window_day - outbreak["onset"] + 1) / RAMP_DAYS)
                extra = rng.poisson(outbreak["peak"] * ramp)
                frames.append(_visits(rng, extra, day, region, True))
    return pd.concat(frames, ignore_index=True)


def summarize(df: pd.DataFrame, node: NodeConfig) -> dict[str, object]:
    """Border-region window vs baseline, for eyeballing the hidden signal."""
    border = df[df["region"].isin(node.border_regions)]
    cutoff = (END_DATE - timedelta(days=WINDOW_DAYS)).isoformat()
    base, win = border[border["date"] <= cutoff], border[border["date"] > cutoff]

    def positivity(frame: pd.DataFrame) -> float:
        tested = frame[frame["malaria_test"] != "not_done"]
        return (tested["malaria_test"] == "positive").mean()

    return {
        "country": node.country,
        "rows": len(df),
        "fever": f"{base['fever'].mean():.2f} -> {win['fever'].mean():.2f}",
        "platelets": f"{base['platelets'].mean():.0f} -> {win['platelets'].mean():.0f}",
        "alt": f"{base['alt'].mean():.0f} -> {win['alt'].mean():.0f}",
        "crp": f"{base['crp'].mean():.1f} -> {win['crp'].mean():.1f}",
        "malaria_pos": f"{positivity(base):.2f} -> {positivity(win):.2f}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[1] / "data"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    summaries = []
    for node in NODES.values():
        df = generate(node)
        df.to_csv(args.out / node.data_file, index=False)
        summaries.append(summarize(df, node))

    print("SYNTHETIC DEMONSTRATION DATA written to", args.out)
    print("Border regions, baseline -> final 14 days:")
    print(pd.DataFrame(summaries).to_string(index=False))


if __name__ == "__main__":
    main()
