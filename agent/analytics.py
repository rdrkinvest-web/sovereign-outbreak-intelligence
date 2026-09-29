"""Deterministic local analytics run inside a national node (Spec §8).

Every function takes the node's own DataFrame and returns aggregates only.
Anomaly strengths are demonstration anomaly scores, not scientifically
validated probabilities.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

Z_THRESHOLD = 3.0
MIN_PCT_CHANGE = 10.0
MIN_POSITIVITY_DIFF = 0.03
MIN_CELL = 10  # statistics from fewer observations are suppressed
ONSET_ROLLING_DAYS = 3

@contextmanager
def cell_suppression(min_cell: int) -> Iterator[None]:
    """Apply a node's own minimum cell size for the duration of a computation."""
    global MIN_CELL
    previous, MIN_CELL = MIN_CELL, min_cell
    try:
        yield
    finally:
        MIN_CELL = previous


MEAN_SIGNALS = ("platelets", "alt", "crp")
SIGNAL_NAMES = {"fever": "fever_rate", "malaria_test": "malaria_positivity"}


def load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["date"])


def load_csv_text(text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(text), parse_dates=["date"])


def in_scope(
    df: pd.DataFrame, region_scope: str, border_regions: tuple[str, ...]
) -> pd.DataFrame:
    if region_scope == "all":
        return df
    return df[df["region"].isin(border_regions)]


def split_periods(
    df: pd.DataFrame, window_days: int, baseline_days: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (baseline, window); the window ends on the latest date in the data."""
    window_start = df["date"].max() - pd.Timedelta(days=window_days)
    baseline_start = window_start - pd.Timedelta(days=baseline_days)
    window = df[df["date"] > window_start]
    baseline = df[(df["date"] > baseline_start) & (df["date"] <= window_start)]
    return baseline, window


def demo_score(z: float | None) -> float:
    """Map |z| onto 0..1 for display. A demonstration anomaly score only."""
    if z is None:
        return 0.0
    return round(abs(z) / (abs(z) + Z_THRESHOLD), 2)


@dataclass(frozen=True)
class Comparison:
    """Window vs baseline for one signal, as aggregates."""

    signal: str
    baseline_value: float | None
    current_value: float | None
    z: float | None
    n_baseline: int
    n_window: int

    @property
    def suppressed(self) -> bool:
        return self.z is None

    @property
    def pct_change(self) -> float | None:
        if self.suppressed or not self.baseline_value:
            return None
        return round(
            (self.current_value - self.baseline_value) / self.baseline_value * 100, 1
        )

    @property
    def direction(self) -> str:
        if self.suppressed or self.z == 0:
            return "flat"
        return "up" if self.z > 0 else "down"

    @property
    def anomalous(self) -> bool:
        pct = self.pct_change
        return (
            not self.suppressed
            and abs(self.z) >= Z_THRESHOLD
            and pct is not None
            and abs(pct) >= MIN_PCT_CHANGE
        )

    @property
    def strength(self) -> float:
        return demo_score(self.z)


def _suppressed(signal: str, n_b: int, n_w: int) -> Comparison:
    return Comparison(signal, None, None, None, n_b, n_w)


def compare_proportions(
    signal: str, hits_b: int, n_b: int, hits_w: int, n_w: int
) -> Comparison:
    if min(n_b, n_w) < MIN_CELL:
        return _suppressed(signal, n_b, n_w)
    p_b, p_w = hits_b / n_b, hits_w / n_w
    pooled = (hits_b + hits_w) / (n_b + n_w)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_b + 1 / n_w))
    z = (p_w - p_b) / se if se > 0 else 0.0
    return Comparison(signal, round(p_b, 3), round(p_w, 3), round(z, 2), n_b, n_w)


def compare_means(signal: str, base: pd.Series, win: pd.Series) -> Comparison:
    n_b, n_w = len(base), len(win)
    if min(n_b, n_w) < MIN_CELL:
        return _suppressed(signal, n_b, n_w)
    mean_b, mean_w = float(base.mean()), float(win.mean())
    se = math.sqrt(float(base.var()) / n_b + float(win.var()) / n_w)
    z = (mean_w - mean_b) / se if se > 0 else 0.0
    return Comparison(signal, round(mean_b, 1), round(mean_w, 1), round(z, 2), n_b, n_w)


def _tested(df: pd.DataFrame, test: str) -> pd.DataFrame:
    return df[df[test] != "not_done"]


def compare_signal(
    baseline: pd.DataFrame, window: pd.DataFrame, signal: str
) -> Comparison:
    name = SIGNAL_NAMES.get(signal, signal)
    if signal == "fever":
        return compare_proportions(
            name,
            int(baseline["fever"].sum()),
            len(baseline),
            int(window["fever"].sum()),
            len(window),
        )
    if signal in MEAN_SIGNALS:
        return compare_means(name, baseline[signal], window[signal])
    if signal == "malaria_test":
        tb, tw = _tested(baseline, signal), _tested(window, signal)
        return compare_proportions(
            name,
            int((tb[signal] == "positive").sum()),
            len(tb),
            int((tw[signal] == "positive").sum()),
            len(tw),
        )
    raise ValueError(f"Unknown signal: {signal}")


def malaria_status(comparison: Comparison) -> str:
    if comparison.suppressed:
        return "insufficient_data"
    diff = comparison.current_value - comparison.baseline_value
    if abs(comparison.z) >= Z_THRESHOLD and abs(diff) >= MIN_POSITIVITY_DIFF:
        return "increased" if diff > 0 else "decreased"
    return "no_material_change"


@dataclass(frozen=True)
class ScanResult:
    sample_size: int
    comparisons: tuple[Comparison, ...]
    malaria_signal: str

    @property
    def anomalies(self) -> tuple[Comparison, ...]:
        return tuple(c for c in self.comparisons if c.anomalous)

    @property
    def assessment(self) -> str:
        found = {(c.signal, c.direction) for c in self.anomalies}
        febrile = ("fever_rate", "up") in found
        markers = ("platelets", "down") in found or ("alt", "up") in found
        if febrile and markers:
            return "unusual_febrile_cluster"
        return "isolated_anomaly" if found else "near_baseline"


def local_scan(
    df: pd.DataFrame,
    border_regions: tuple[str, ...],
    time_window_days: int,
    baseline_days: int,
    region_scope: str,
    signals: list[str],
) -> ScanResult:
    """Compare every requested signal in the window against its baseline."""
    baseline, window = split_periods(
        in_scope(df, region_scope, border_regions), time_window_days, baseline_days
    )
    comparisons = tuple(
        compare_signal(baseline, window, s) for s in signals if s != "malaria_test"
    )
    malaria = (
        malaria_status(compare_signal(baseline, window, "malaria_test"))
        if "malaria_test" in signals
        else "not_requested"
    )
    return ScanResult(len(baseline) + len(window), comparisons, malaria)


# --- Follow-up analytics (Spec §7 allowed follow-up questions) -------------
# Each returns (answer, metrics): answer is "yes" | "no" | "inconclusive" and
# metrics is a flat dict of aggregate scalars.

Metrics = dict[str, float | int | str | None]


def _answer(condition: bool | None) -> str:
    if condition is None:
        return "inconclusive"
    return "yes" if condition else "no"


def check_malaria_positivity(
    baseline: pd.DataFrame, window: pd.DataFrame
) -> tuple[str, Metrics]:
    """Did malaria positivity materially change in the same window?"""
    cmp = compare_signal(baseline, window, "malaria_test")
    status = malaria_status(cmp)
    changed = None if status == "insufficient_data" else status != "no_material_change"
    return _answer(changed), {
        "malaria_status": status,
        "baseline_positivity": cmp.baseline_value,
        "window_positivity": cmp.current_value,
        "z": cmp.z,
        "baseline_tested": cmp.n_baseline,
        "window_tested": cmp.n_window,
    }


def check_marker_in_febrile_cohort(
    baseline: pd.DataFrame, window: pd.DataFrame, marker: str = "platelets"
) -> tuple[str, Metrics]:
    """Is the marker shift concentrated among febrile observations?"""
    febrile = compare_means(
        marker,
        baseline.loc[baseline["fever"] == 1, marker],
        window.loc[window["fever"] == 1, marker],
    )
    afebrile = compare_means(
        marker,
        baseline.loc[baseline["fever"] == 0, marker],
        window.loc[window["fever"] == 0, marker],
    )
    concentrated = None
    if not (febrile.suppressed or afebrile.suppressed):
        concentrated = febrile.anomalous and not afebrile.anomalous
    return _answer(concentrated), {
        "marker": marker,
        "febrile_baseline_mean": febrile.baseline_value,
        "febrile_window_mean": febrile.current_value,
        "febrile_z": febrile.z,
        "afebrile_baseline_mean": afebrile.baseline_value,
        "afebrile_window_mean": afebrile.current_value,
        "afebrile_z": afebrile.z,
    }


def check_marker_by_region(
    df: pd.DataFrame,
    border_regions: tuple[str, ...],
    time_window_days: int,
    baseline_days: int,
    marker: str = "alt",
) -> tuple[str, Metrics]:
    """Is the marker increase concentrated in the border regions and window?"""
    border_df = df[df["region"].isin(border_regions)]
    interior_df = df[~df["region"].isin(border_regions)]
    border = compare_signal(
        *split_periods(border_df, time_window_days, baseline_days), marker
    )
    interior = compare_signal(
        *split_periods(interior_df, time_window_days, baseline_days), marker
    )
    concentrated = None
    if not (border.suppressed or interior.suppressed):
        concentrated = border.anomalous and not interior.anomalous
    return _answer(concentrated), {
        "marker": marker,
        "border_baseline_mean": border.baseline_value,
        "border_window_mean": border.current_value,
        "border_z": border.z,
        "interior_baseline_mean": interior.baseline_value,
        "interior_window_mean": interior.current_value,
        "interior_z": interior.z,
    }


def check_signal_onset(
    baseline: pd.DataFrame, window: pd.DataFrame
) -> tuple[str, Metrics]:
    """When did the fever signal begin? Reports one aggregate onset date."""
    if min(len(baseline), len(window)) < MIN_CELL:
        return "inconclusive", {"onset_date": None}
    p = float(baseline["fever"].mean())
    daily = window.groupby("date")["fever"].agg(["sum", "count"])
    rolling = daily.rolling(ONSET_ROLLING_DAYS, min_periods=ONSET_ROLLING_DAYS).sum()
    rate = rolling["sum"] / rolling["count"]
    threshold = p + 2 * (p * (1 - p) / rolling["count"]).pow(0.5)
    above = rate[rate > threshold]
    # The rolling window ends on the flagged day; the signal began at its start.
    onset = (
        above.index[0] - pd.Timedelta(days=ONSET_ROLLING_DAYS - 1)
        if len(above)
        else None
    )
    return _answer(onset is not None), {
        "onset_date": onset.date().isoformat() if onset is not None else None,
        "baseline_fever_rate": round(p, 3),
        "rolling_days": ONSET_ROLLING_DAYS,
    }


def check_test_volume(
    baseline: pd.DataFrame,
    window: pd.DataFrame,
    time_window_days: int,
    baseline_days: int,
) -> tuple[str, Metrics]:
    """Is the fever anomaly still present after adjusting for visit volume?"""
    fever = compare_signal(baseline, window, "fever")  # a rate per visit
    return _answer(None if fever.suppressed else fever.anomalous), {
        "baseline_visits_per_day": round(len(baseline) / baseline_days, 1),
        "window_visits_per_day": round(len(window) / time_window_days, 1),
        "baseline_malaria_tests_per_day": round(
            len(_tested(baseline, "malaria_test")) / baseline_days, 1
        ),
        "window_malaria_tests_per_day": round(
            len(_tested(window, "malaria_test")) / time_window_days, 1
        ),
        "fever_rate_baseline": fever.baseline_value,
        "fever_rate_window": fever.current_value,
        "fever_rate_z": fever.z,
    }
