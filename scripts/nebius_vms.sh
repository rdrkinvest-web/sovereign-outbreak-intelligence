#!/usr/bin/env bash
# Create, inspect and delete the three national-node VMs on Nebius.
#
#   scripts/nebius_vms.sh create          # show the create commands (no charge)
#   scripts/nebius_vms.sh create --go     # create moh-kenya, moh-uganda, moh-tanzania
#   scripts/nebius_vms.sh status          # state and public IP of each VM
#   scripts/nebius_vms.sh delete --go     # delete all three (teardown, stops billing)
#
# Uses the audited CLI at ~/.nebius/bin/nebius (0.12.279) and the logged-in
# "hackathon" profile. Each VM: cpu-d3 2 vCPU / 8 GiB (~$0.066/h), 20 GiB SSD,
# Ubuntu 24.04, public IP for SSH only; nebius/cloud-init.yaml does the rest.
set -euo pipefail
cd "$(dirname "$0")/.."

NB=("$HOME/.nebius/bin/nebius" --no-check-update)
# uk-south1 (London): this tenant's CPU-VM quota is 0 in eu-north1 and us-central1.
PROJECT=project-e03z8qb0lc00qfgxdk1e1d   # default-project-uk-south1
SUBNET=vpcsubnet-e03b48kmsc3t3sa5v2      # its default subnet
PLATFORM=cpu-d3                          # AMD Epyc Genoa (only CPU platform here)
PUBLIC_IMAGES=project-e03public-images
COUNTRIES=(kenya uganda tanzania)
action="${1:?usage: scripts/nebius_vms.sh <create|status|delete> [--go]}"
go="${2:-}"

vm_id() {
  "${NB[@]}" compute instance get-by-name --parent-id "$PROJECT" --name "moh-$1" \
    --format jsonpath='{.metadata.id}' 2>/dev/null || true
}

case "$action" in
create)
  for c in "${COUNTRIES[@]}"; do
    cmd=("${NB[@]}" compute instance create
      --parent-id "$PROJECT" --name "moh-$c" --labels "purpose=moh-hackathon"
      --resources-platform "$PLATFORM" --resources-preset 2vcpu-8gb
      --boot-disk-attach-mode read_write
      --boot-disk-managed-disk-name "moh-$c-boot"
      --boot-disk-managed-disk-type network_ssd
      --boot-disk-managed-disk-size-gibibytes 20
      --boot-disk-managed-disk-source-image-family-image-family ubuntu24.04-driverless
      --boot-disk-managed-disk-source-image-family-parent-id "$PUBLIC_IMAGES"
      --network-interfaces "[{\"name\":\"eth0\",\"subnet_id\":\"$SUBNET\",\"ip_address\":{},\"public_ip_address\":{}}]"
      --cloud-init-user-data "$(cat nebius/cloud-init.yaml)")
    if [[ "$go" == "--go" ]]; then
      if [[ -n "$(vm_id "$c")" ]]; then
        echo "moh-$c already exists, skipping"
        continue
      fi
      echo "== creating moh-$c"
      "${cmd[@]}" --format jsonpath='{.metadata.id}'
      echo
    else
      printf '%q ' "${cmd[@]:0:$((${#cmd[@]} - 1))}"
      echo "<nebius/cloud-init.yaml>"
    fi
  done
  [[ "$go" == "--go" ]] || echo -e "\nDry run. Re-run with --go to create (about \$0.06/hour per VM)."
  ;;
status)
  for c in "${COUNTRIES[@]}"; do
    id=$(vm_id "$c")
    if [[ -z "$id" ]]; then
      echo "moh-$c: not found"
      continue
    fi
    "${NB[@]}" compute instance get --id "$id" --format json | python3 -c "
import json, sys
d = json.load(sys.stdin)
nic = (d.get('status', {}).get('network_interfaces') or [{}])[0]
ip = (nic.get('public_ip_address') or {}).get('address', 'pending').split('/')[0]
print(f\"moh-$c: {d.get('status', {}).get('state', '?')}  public_ip={ip}  id={d['metadata']['id']}\")"
  done
  ;;
delete)
  for c in "${COUNTRIES[@]}"; do
    id=$(vm_id "$c")
    [[ -n "$id" ]] || { echo "moh-$c: not found"; continue; }
    if [[ "$go" == "--go" ]]; then
      echo "== deleting moh-$c ($id)"
      "${NB[@]}" compute instance delete --id "$id"
    else
      echo "would delete moh-$c ($id)"
    fi
  done
  if [[ "$go" == "--go" ]]; then
    echo "== leftover disks labelled or named moh-*:"
    "${NB[@]}" compute disk list --parent-id "$PROJECT" --format json |
      python3 -c "import json,sys; [print(' ', d['metadata']['name'], d['metadata']['id']) for d in json.load(sys.stdin).get('items', []) if d['metadata']['name'].startswith('moh-')]" || true
  else
    echo "Dry run. Re-run with --go to delete."
  fi
  ;;
*)
  echo "unknown action: $action" >&2
  exit 1
  ;;
esac
