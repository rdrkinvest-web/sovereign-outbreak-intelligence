"""In-process stand-in for the Flower federation (Spec §12 Step 5).

Each node loads its own CSV; the coordinator gets only this Transport. Step 7
replaces this with a Flower-backed Transport without touching the coordinator.
"""

from __future__ import annotations

from pathlib import Path

from . import analytics
from .countries import NODES
from .national import NationalAgent

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class LocalFederation:
    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR) -> None:
        self._nodes = {
            country: NationalAgent(node, analytics.load_csv(data_dir / node.data_file))
            for country, node in NODES.items()
        }

    def countries(self) -> list[str]:
        return list(self._nodes)

    def exchange(self, payloads: dict[str, str]) -> dict[str, str]:
        return {c: self._nodes[c].handle(p) for c, p in payloads.items()}
