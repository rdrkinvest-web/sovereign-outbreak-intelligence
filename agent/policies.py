"""Each Ministry of Health's own data-sharing policy (node-side).

Policies are enforced in code by the node toolbox, whether the node answers
with its AI agent or deterministically, and are also given to the node's
agent so it can explain a decline. The coordinator never imports this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SharingPolicy:
    min_cell: int = 10
    onset_precision: Literal["day", "week"] = "day"
    allow_region_breakdown: bool = True

    def describe(self) -> list[str]:
        rules = [f"Statistics from fewer than {self.min_cell} observations are suppressed."]
        if self.onset_precision == "week":
            rules.append("Signal onset is shared at ISO-week precision only, never exact dates.")
        if not self.allow_region_breakdown:
            rules.append("Sub-national (border vs interior) breakdowns are not shared.")
        return rules


POLICIES: dict[str, SharingPolicy] = {
    "Kenya": SharingPolicy(),
    "Uganda": SharingPolicy(min_cell=20, onset_precision="week"),
    "Tanzania": SharingPolicy(allow_region_breakdown=False),
}
