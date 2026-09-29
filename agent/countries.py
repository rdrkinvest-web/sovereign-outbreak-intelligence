"""National node configuration (Spec §4, §12 Step 4).

This describes what each sovereign node holds. It is node-side configuration:
the coordinator never imports it and never learns file names or paths.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeConfig:
    """One national Ministry-of-Health node."""

    country: str
    data_file: str
    border_regions: tuple[str, ...]
    interior_regions: tuple[str, ...]

    @property
    def regions(self) -> tuple[str, ...]:
        return self.border_regions + self.interior_regions


NODES: dict[str, NodeConfig] = {
    "Kenya": NodeConfig(
        country="Kenya",
        data_file="kenya.csv",
        border_regions=("Busia", "Bungoma"),
        interior_regions=("Nairobi", "Nakuru"),
    ),
    "Uganda": NodeConfig(
        country="Uganda",
        data_file="uganda.csv",
        border_regions=("Busia", "Tororo"),
        interior_regions=("Kampala", "Gulu"),
    ),
    "Tanzania": NodeConfig(
        country="Tanzania",
        data_file="tanzania.csv",
        border_regions=("Mara", "Kagera"),
        interior_regions=("Dar es Salaam", "Dodoma"),
    ),
}
