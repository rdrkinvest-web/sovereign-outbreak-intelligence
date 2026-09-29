#!/usr/bin/env bash
# Deploy ONE country's SuperNode to its own Nebius VM. Dry run by default.
#
#   scripts/deploy_node.sh <kenya|uganda|tanzania> <vm-public-ip>        # show what would happen
#   scripts/deploy_node.sh <kenya|uganda|tanzania> <vm-public-ip> --go   # do it
#
# Copies exactly four things for that country and nothing else: its CSV, its
# SuperNode private key, the single-node compose file, and the model key line
# from .env. It stops the laptop container for that country first, so no
# country ever has two live nodes.
set -euo pipefail
cd "$(dirname "$0")/.."

usage="usage: scripts/deploy_node.sh <kenya|uganda|tanzania> <vm-public-ip> [--go]"
country="${1:?$usage}"
host="${2:?$usage}"
go="${3:-}"
case "$country" in kenya | uganda | tanzania) ;; *) echo "unknown country: $country" >&2; exit 1 ;; esac

ssh_key=keys/nebius_ssh
remote="moh@$host"
dir=sovereign-node
ssh_opts=(-i "$ssh_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)

for f in "data/$country.csv" "keys/$country" nebius/compose.node.yaml .env "$ssh_key"; do
  [[ -f "$f" ]] || { echo "missing $f" >&2; exit 1; }
done
grep -q '^FLWR_MODEL_API_KEY=..*' .env || { echo ".env has no FLWR_MODEL_API_KEY value" >&2; exit 1; }

echo "Country: $country  ->  $remote:~/$dir"
echo "Files that leave this laptop (nothing else):"
echo "  data/$country.csv          -> ~/$dir/data/$country.csv"
echo "  keys/$country (private)    -> ~/$dir/keys/node"
echo "  nebius/compose.node.yaml   -> ~/$dir/compose.yaml"
echo "  FLWR_MODEL_API_KEY line    -> ~/$dir/.env   (no other .env lines)"
echo "Then: stop the laptop '$country' container, start the node on the VM."
if [[ "$go" != "--go" ]]; then
  echo
  echo "Dry run. Re-run with --go to deploy."
  exit 0
fi

echo "== waiting for the VM's setup (cloud-init) to finish"
ssh "${ssh_opts[@]}" "$remote" 'cloud-init status --wait >/dev/null; test -f /var/lib/cloud/moh-ready'

echo "== stopping the laptop $country node"
docker compose stop "$country" >/dev/null 2>&1 || true

env_line=$(mktemp)
trap 'rm -f "$env_line"' EXIT
grep '^FLWR_MODEL_API_KEY=' .env >"$env_line"

echo "== copying $country's files"
ssh "${ssh_opts[@]}" "$remote" "mkdir -p ~/$dir/data ~/$dir/keys && chmod 700 ~/$dir"
scp -q "${ssh_opts[@]}" "data/$country.csv" "$remote:$dir/data/$country.csv"
scp -q "${ssh_opts[@]}" "keys/$country" "$remote:$dir/keys/node"
scp -q "${ssh_opts[@]}" nebius/compose.node.yaml "$remote:$dir/compose.yaml"
scp -q "${ssh_opts[@]}" "$env_line" "$remote:$dir/.env"

echo "== starting the $country SuperNode on the VM"
# The container runs as uid 49999, so it must own the key it reads.
ssh "${ssh_opts[@]}" "$remote" "cd ~/$dir && chmod 600 .env \
  && sudo chown 49999:49999 keys/node && sudo chmod 400 keys/node \
  && sudo docker compose up -d && sleep 8 && sudo docker compose logs --tail 4"
echo "== done. Check: uv run flwr supernode list supergrid"
