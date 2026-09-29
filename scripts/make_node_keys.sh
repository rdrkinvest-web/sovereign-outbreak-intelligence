#!/usr/bin/env bash
# Create one SuperNode key pair per country in keys/ (git-ignored).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p keys
for country in kenya uganda tanzania; do
  if [[ -f "keys/$country" ]]; then
    echo "keys/$country exists, keeping it"
  else
    ssh-keygen -q -t ecdsa -b 384 -N "" -C "$country-moh" -f "keys/$country"
    echo "created keys/$country"
  fi
done
