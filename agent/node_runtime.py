"""National-node side of the AgentApp, running on a SuperNode (Spec §5A, §9).

The node reads its dataset through Flower's sandboxed `filesystem` connector,
which only sees the directory in FLWR_FILESYSTEM_ALLOWED_DIRS. Each container
mounts exactly one country's CSV there, so the file a node finds is also its
identity. The reply goes back with `push_reply_message`, the only Grid tool a
SuperNode agent has.
"""

from __future__ import annotations

import json
import os

import pandas as pd
from flwr.agentapp import AgentConnectors, AgentSession
from flwr.app import Context

from . import analytics
from .countries import NODES, NodeConfig
from .flower_transport import call_tool
from .model_client import FlowerModelClient, ModelClient
from .node_agent import NodeAgent

DATA_ROOT = "/data/national"


def _connector(connectors: AgentConnectors, name: str, call_id: str, **arguments: object) -> dict:
    item = connectors.call(
        {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments),
        }
    )
    return json.loads(item["output"])


def load_local_dataset(
    connectors: AgentConnectors, root: str | None = None
) -> tuple[NodeConfig, pd.DataFrame]:
    """Find and read the one national dataset mounted on this node."""
    root = root or os.environ.get("FLWR_FILESYSTEM_ALLOWED_DIRS", DATA_ROOT).split(os.pathsep)[0]
    entries = _connector(connectors, "filesystem_list_directory", "node-list", path=root)["entries"]
    files = {e["name"] for e in entries if e["type"] == "file"}
    matches = [node for node in NODES.values() if node.data_file in files]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one national dataset in {root}, found {sorted(files)}."
        )
    node = matches[0]
    content = _connector(
        connectors, "filesystem_read_file", "node-read", path=f"{root}/{node.data_file}"
    )["content"]
    return node, analytics.load_csv_text(content)


def node_model(context: Context) -> ModelClient | None:
    """The node's model, or None when node agents are switched off (Level C fallback)."""
    if not context.run_config.get("node-agents", True):
        return None
    return FlowerModelClient(str(context.run_config.get("model", "openai/gpt-5.6-sol")))


def run_national_node(
    agent: AgentSession,
    context: Context,
    root: str | None = None,
    model: ModelClient | None = None,
) -> str:
    """Answer the coordinator's instruction and return the reply that was sent."""
    instruction = json.loads(agent.prompt)  # {"message_id", "src_node_id", "payload"}
    node, data = load_local_dataset(agent.connectors, root)
    node_agent = NodeAgent(node, data, model if model is not None else node_model(context))
    reply = node_agent.handle(instruction["payload"])
    # Only code sends, and only after node_agent's validation and audit.
    sent = call_tool(agent.grid, "push_reply_message", "node-reply", payload=reply)
    if sent.get("error"):
        raise RuntimeError(f"Reply was not accepted: {sent['error']}")
    # Logs leave the node, so they carry only the reply's shape, never data.
    body = json.loads(reply)
    kind = body.get("response_type") or body.get("status")
    tools = ", ".join(body.get("tools_used", [])) or "none"
    print(
        f"[{node.country} MoH] replied: {kind}, mode={body.get('mode', 'n/a')}, "
        f"tools=[{tools}], {len(reply)} bytes, aggregates only"
    )
    if node_agent.last_error:
        print(f"[{node.country} MoH] agent fell back: {node_agent.last_error}")
    return reply
