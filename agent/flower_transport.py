"""Flower-backed Transport: the coordinator's only channel to the nodes (Spec §12 Step 7).

Runs on SuperGrid. It calls the Grid tools Flower gives the coordinator
(get_nodes, push_messages, pull_messages) directly from code instead of
letting a model choose them, so the protocol stays a fixed state machine.
Nodes are identified by the name they were registered with, e.g. "Kenya MoH".
"""

from __future__ import annotations

import itertools
import json

from flwr.agentapp import AgentGrid

# Nodes may install the app's dependencies on their first task; SuperGrid
# stops any task after 5 minutes of running.
PULL_TIMEOUT_SECONDS = 240.0


def country_from_node_name(name: str | None) -> str | None:
    """'Kenya MoH' -> 'Kenya'. Unnamed nodes are not national nodes."""
    if not name or not name.strip():
        return None
    return name.split()[0]


def call_tool(grid: AgentGrid, name: str, call_id: str, **arguments: object) -> dict:
    """Invoke one Grid tool and return its parsed output."""
    item = grid.call(
        {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments),
        }
    )
    return json.loads(item["output"])


class FlowerTransport:
    def __init__(self, grid: AgentGrid, pull_timeout: float = PULL_TIMEOUT_SECONDS) -> None:
        self._grid = grid
        self._pull_timeout = pull_timeout
        self._call_ids = itertools.count(1)
        self._node_ids: dict[str, str] | None = None

    def _call(self, name: str, **arguments: object) -> dict:
        return call_tool(self._grid, name, f"coordinator-{next(self._call_ids)}", **arguments)

    def _nodes(self) -> dict[str, str]:
        if self._node_ids is None:
            found: dict[str, str] = {}
            for node in self._call("get_nodes", sample_size=None)["nodes"]:
                country = country_from_node_name(node["name"])
                if country is None:
                    continue
                if country in found:
                    raise RuntimeError(f"Two SuperNodes are registered for {country}.")
                found[country] = node["id"]
            if not found:
                raise RuntimeError(
                    "No named national SuperNodes are available in this federation. "
                    "Start the nodes and select the federation they belong to."
                )
            self._node_ids = dict(sorted(found.items()))
        return self._node_ids

    def countries(self) -> list[str]:
        return list(self._nodes())

    def exchange(self, payloads: dict[str, str]) -> dict[str, str]:
        """Deliver payloads; a node whose task failed gets one retry."""
        node_ids = self._nodes()
        unknown = set(payloads) - set(node_ids)
        if unknown:
            raise RuntimeError(f"No SuperNode for: {', '.join(sorted(unknown))}")

        replies, failed = self._round_trip(payloads, node_ids)
        if failed:
            # A node task can fail transiently (e.g. runtime dependency setup);
            # the same question is safe to ask again.
            print(f"[coordinator] retrying once: {', '.join(f'{c} ({e})' for c, e in failed.items())}")
            retried, still_failed = self._round_trip({c: payloads[c] for c in failed}, node_ids)
            replies.update(retried)
            if still_failed:
                raise RuntimeError(
                    "; ".join(f"{c} node failed: {e}" for c, e in still_failed.items())
                )
        return replies

    def _round_trip(
        self, payloads: dict[str, str], node_ids: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Push one message per country and pull the replies: (replies, errors)."""
        countries = list(payloads)
        pushed = self._call(
            "push_messages",
            messages=[
                {
                    "dst_node_id": node_ids[c],
                    "payload": payloads[c],
                    "reply_to_message_id": None,
                }
                for c in countries
            ],
        )["results"]
        rejected = [c for c, r in zip(countries, pushed) if not r["message_id"]]
        if rejected:
            raise RuntimeError(f"Message rejected for: {', '.join(rejected)}")
        country_by_message = {r["message_id"]: c for c, r in zip(countries, pushed)}

        pulled = self._call(
            "pull_messages",
            message_ids=list(country_by_message),
            timeout=self._pull_timeout,
        )
        if pulled["pending_message_ids"]:
            late = [country_by_message[m] for m in pulled["pending_message_ids"]]
            raise RuntimeError(
                f"No reply within {self._pull_timeout:.0f}s from: {', '.join(late)}"
            )

        replies: dict[str, str] = {}
        failed: dict[str, str] = {}
        for message in pulled["messages"]:
            country = country_by_message[message["reply_to_message_id"]]
            if message["error"]:
                failed[country] = message["error"]
            else:
                replies[country] = message["payload"]
        return replies, failed
