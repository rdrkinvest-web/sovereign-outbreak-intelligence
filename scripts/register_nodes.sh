#!/usr/bin/env bash
# Register the three national SuperNodes with SuperGrid (needs `flwr login supergrid`).
# Names must start with the country: the coordinator identifies nodes by them.
# Locations place each node at a border district on the federation map.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run flwr supernode register keys/kenya.pub supergrid \
  --name="Kenya MoH" --location="0.4608,34.1115"      # Busia, Kenya
uv run flwr supernode register keys/uganda.pub supergrid \
  --name="Uganda MoH" --location="0.6928,34.1808"     # Tororo, Uganda
uv run flwr supernode register keys/tanzania.pub supergrid \
  --name="Tanzania MoH" --location="-1.5000,33.8000"  # Mara, Tanzania
uv run flwr supernode list supergrid --verbose
