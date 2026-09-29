# Running the national SuperNodes on Nebius

Three small CPU VMs, one per country. Each VM holds only its own country's CSV,
its own SuperNode key, one compose file and the model key. The coordinator stays
on SuperGrid; nothing about the federation or node registrations changes (the
SuperNode keys are the same, so the nodes keep their identities).

**Cost:** AMD Epyc Genoa CPU is $0.015/vCPU-h + $0.0045/GiB-h. A 2 vCPU / 8 GiB VM
is ~$0.07/h, so three VMs for 12 h is ~$2.50 plus a few cents of disk.

## 1. Create the three VMs

**With the CLI (what we used):** install the audited Nebius CLI to `~/.nebius/bin`,
log in with `~/.nebius/bin/nebius profile create`, then:

```
scripts/nebius_vms.sh create        # dry run
scripts/nebius_vms.sh create --go   # uk-south1 (London), cpu-d3 2 vCPU / 8 GiB each
scripts/nebius_vms.sh status        # state and public IPs
```

The region is uk-south1 because this account's CPU quota was 0 in eu-north1.

**Or in the web console** (labels may differ slightly), for each of `moh-kenya`,
`moh-uganda`, `moh-tanzania`:

1. **Compute → Virtual machines → Create virtual machine.**
2. **Name:** `moh-kenya` (etc.).
3. **Platform:** non-GPU, AMD Epyc Genoa (`cpu-d3`). **Preset:** the smallest
   (2 vCPU / 8 GiB).
4. **Boot disk:** Ubuntu 24.04 LTS, 20 GiB network SSD.
5. **Network:** default subnet, **public IP address: on** (needed for SSH only).
6. **Access:** if the console asks for a username and SSH key, use `moh` and the
   contents of `keys/nebius_ssh.pub`.
7. **Advanced / User data (cloud-init):** paste all of `nebius/cloud-init.yaml`.
   It installs Docker, pre-pulls the SuperNode image and closes every inbound
   port except SSH (the node only dials *out* to `fleet-supergrid.flower.ai:443`).
8. **Create**, then copy the VM's **public IP**.

Setup takes a few minutes after the VM starts; the deploy script waits for it.

## 2. Deploy each country (from `~/Desktop/agent`)

```
scripts/deploy_node.sh kenya <kenya-ip>          # dry run: shows exactly what is copied
scripts/deploy_node.sh kenya <kenya-ip> --go     # stops the laptop Kenya node, starts it on the VM
```

Repeat for `uganda` and `tanzania`. Then check all three are online:

```
uv run flwr supernode list supergrid --verbose
```

The first investigation on fresh VMs installs the app's packages on each node;
run one warm-up investigation before timing or demoing.

## 3. Teardown (do this after the event)

1. `scripts/nebius_vms.sh delete --go` (or delete them in the console), then check
   no `moh-*` disks or public IPs remain and **Billing** shows no running compute.
   Remove the CLI and its stored login with `rm -rf ~/.nebius`.
2. Laptop fallback, if you still need the nodes: `docker compose start`.
3. Revoke or rotate the Flower API key that was copied to the VMs
   (flower.ai → Profile → Settings → API Keys).
