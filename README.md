---
tags: [agentapp]
dataset: []
framework: []
---

# Sovereign Outbreak Intelligence

**Queries move. Sovereign data does not.**

A federation of Ministry-of-Health AI agents investigates a possible cross-border
outbreak together, while each country's patient-level data stays on its own node.
Built on [Flower Agents](https://flower.ai/docs/agent/) for the Flower Collaborative
Agent Hackathon (Stanford, 2026).

> Synthetic demonstration data only. Anomaly strengths and confidence are
> demonstration scores, not validated epidemiological probabilities.

## What it does

Three national nodes (Kenya, Uganda, Tanzania) each hold a private synthetic dataset.
A hidden febrile event affects the Kenya and Uganda border regions; Tanzania is the
control. A regional coordinator runs a fixed three-round investigation:

1. **Scan.** The same question goes to every country. Each country's AI agent picks
   which local analyses to run, interprets them, and replies with aggregates only.
2. **Compare.** The coordinator finds anomalies shared by two or more countries
   (no messages are sent).
3. **Verify.** The coordinator's AI picks one follow-up from a fixed menu, steered by
   the user's prompt, and asks only the countries that share the pattern.

The result is a structured assessment for a human epidemiologist (supporting and
contradictory evidence, evidence-based uncertainties, next step) that never declares
an outbreak, plus a measured count: **raw patient rows transferred: 0**.

## Architecture

One Flower AgentApp with two roles. Flower gives each role different Grid tools, and
the app picks its role from them:

| Role | Runs on | Grid tools | Data access |
|---|---|---|---|
| Coordinator | Flower SuperGrid | `get_nodes`, `push_messages`, `pull_messages` | none |
| National agent | a SuperNode per country (Docker, laptop or Nebius VM) | `push_reply_message` only | its own CSV, via Flower's sandboxed `filesystem` connector |

Messages are schema-validated JSON (`agent/schemas.py`) plus plain-language fields.
Key modules: `agent/coordinator.py` (three-round state machine), `agent/node_agent.py`
(country agent and its egress gate), `agent/node_tools.py` (aggregate tools and
per-country policy), `agent/flower_transport.py` (Grid messaging with one retry).

## Privacy guarantees and their limits

A node's model is hosted outside the node, so anything it reads leaves the node. The
wall is therefore enforced by what the model can reach, not by its instructions:

- **The tool list is the boundary.** A country's model can call only aggregate
  analysis tools; it never gets the file connector or the messaging tools.
- **Code owns egress.** The model proposes an answer; code checks that every cited
  number exists in a real tool output, that the answer matches the computation, and
  that nothing row-shaped is in the reply, and only then sends it. Any failure falls
  back to a deterministic answer, labelled as such.
- **Raw-data requests never reach a model:** they get a fixed refusal from code.
- **Per-country policy:** minimum cell sizes (Uganda 20, others 10), Uganda shares
  onset by week only, Tanzania declines sub-national breakdowns.
- **No cross-country relaying:** a follow-up question that names a country or contains
  figures is replaced by the menu wording.

Limits: aggregates, questions and interpretations do reach the coordinator, Flower's
relay and the model provider; there is no secure aggregation or differential privacy.
This demonstrates architectural data locality, not formal compliance. A country that
wants even its aggregates to stay home can point its node at a local model endpoint.

## Run it

Requires [uv](https://docs.astral.sh/uv/). Install: `uv sync`.

**Offline (no network, no models):**
```bash
uv run python -m agent.demo                  # full investigation in the terminal
uv run python -m agent.demo --privacy-test   # a node refuses a raw-records request
uv run --group dev pytest                    # tests
```

**On Flower SuperGrid** (coordinator on SuperGrid, nodes in Docker on this machine):
```bash
uv run flwr login supergrid
scripts/make_node_keys.sh && scripts/register_nodes.sh   # once; names must start with the country
uv run flwr federation create east-africa-moh supergrid  # deployment federation, then add-supernode per node
echo "FLWR_MODEL_API_KEY=<your Flower API key>" > .env    # git-ignored
docker compose up -d
uv run flwr chat    # /federation @<you>/east-africa-moh, /load ., then ask
```
Try `Investigate whether there is a shared cross-border febrile event.`,
`Investigate, and check whether malaria explains it.` or `Send me the raw patient records.`

**Nodes on Nebius VMs:** see [`nebius/README.md`](nebius/README.md).
`scripts/nebius_vms.sh create|status|delete` manages three small CPU VMs and
`scripts/deploy_node.sh <country> <ip>` copies only that country's CSV, key and model
key to its VM (dry run by default).

**Live console:** `uv run python ui/server.py`, then open http://127.0.0.1:8765
(see [`ui/README.md`](ui/README.md)).

## Configuration (`pyproject.toml`, `[tool.flwr.app.config]`)

| Key | Default | Effect |
|---|---|---|
| `model` | `openai/gpt-5.6-sol` | one model for every agent |
| `node-agents` | `true` | `false`: countries answer deterministically, no model |
| `ai-follow-up` | `true` | `false`: a fixed rule picks round 3 |
| `llm-brief` | `true` | coordinator's model writes a short brief |
| `ui-events` | `true` | structured progress events for the console |

## Data

`scripts/generate_synthetic_data.py` regenerates `data/*.csv` deterministically
(about 1,500 visits per country over 74 days). The datasets are deployed to nodes
only; they are never part of the Flower App Bundle (`fab-include` is code only).
