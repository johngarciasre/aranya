# Aranya Kubernetes take-home

A three-node Kubespray deployment with private Cilium networking, Argo CD,
clusterdOS and an nginx `hello aranya` service. Use [RUNBOOK.md](RUNBOOK.md) for
build, validation, handoff and operating instructions.

## Status

**The cluster, required addons, nginx and reviewer access preparation are complete;
the email handoff remains.** On 2026-11-06 UTC all three Kubernetes v1.36.4 nodes
were Ready, Cilium was 3/3 and its operator 2/2. Argo CD v3.6.1 workloads were
Ready: Redis 3/3, server and repo-server 2/2 each, and controller 1/1. All six
Applications, including clusterdOS and nginx, were Synced/Healthy. The metrics
APIService was Available with TLS verification and `kubectl top` worked; NFD
reported Intel CPU vendor labels on all three nodes. Nginx had three Ready pods,
one per node; public HTTP returned `hello aranya` on all three node addresses.
Reviewer SSH access is arranged. The admin kubeconfig was tested through the SSH
tunnel and encrypted to the three authenticated reviewer keys.

The vendored config chart and explicit cluster name restored reconciliation.
Persistent nftables host protection and the revised rollback playbook were applied
serially on all nodes. Public etcd, API and kubelet ports were filtered. SSH was
verified on both 22 and 2026 with password/keyboard-interactive authentication
disabled. Full non-disruptive live acceptance passed on 2026-11-06 UTC.
Discovery-first Ansible preflight also passed on all three nodes at 06:32 UTC,
with no node configuration changes; the generated private addresses matched the
existing inventory.

Disruptive failover testing was deliberately skipped because provider console
access was unavailable; one-node fault tolerance is designed, not demonstrated.
The rollback script was syntax-checked but not tested by inducing an SSH lockout.
Reviewer SSH access and GPG-encrypted kubeconfig handoff are treated as solved.
The original-thread email remains to be sent. A clean checkout rebuild on fresh
nodes has not been demonstrated, so reproduction is documented but not proven.

## Scope

The runbook starts with provider-provisioned, fresh Linux servers; provisioning
VMs is outside its scope. Kubespray installs containerd, Kubernetes, stacked etcd
and Cilium. Argo CD installs clusterdOS and reconciles nginx from the submission
repository. The operator workflow uses Bash on a Linux workstation.

The public repository contains example inventory, an environment sample and
inventory templates. Actual node addresses and SSH settings are in the ignored
production inventory. Credentials, kubeconfigs, artifacts and private tooling
are excluded from Git; `.local/` is the private, machine-specific workspace.
Git-ignore is not encryption or filesystem access control.

## Contents

| File | Purpose |
| --- | --- |
| [RUNBOOK.md](RUNBOOK.md) | Prerequisites, bootstrap, publication, live checks, endpoint switching, GPG handoff and operations |
| [inventory/example/hosts.yaml](inventory/example/hosts.yaml) | Three stacked control-plane/etcd nodes; public SSH, private cluster traffic |
| [inventory/example/group_vars](inventory/example/group_vars) | Cilium, Kubernetes, API SANs and kubelet certificate settings; no VIP |
| [inventory/production.env.example](inventory/production.env.example) | Sample environment values for private inventory rendering |
| [inventory/templates](inventory/templates) / [renderer](scripts/render_inventory.sh) | Templates and envsubst-based private inventory rendering |
| [tests/check_inventory_template.py](tests/check_inventory_template.py) | Local test for rendered inventory and derived values |
| [preflight script](scripts/preflight.sh) / [playbook](scripts/preflight.yaml) | Input validation, private-IP discovery and read-only node/network checks |
| [tests/check_preflight.py](tests/check_preflight.py) | Local regression check for preflight guards and Ansible assertions |
| [install.yaml](install.yaml) | clusterdOS Argo Application with required gitapps and pinned config chart override |
| [charts/config](charts/config) | Vendored clusterdOS v0.4.8 config chart with corrected Helm API version, upstream license and provenance |
| [argocd/nginx.yaml](argocd/nginx.yaml) | GitOps Application pinned to the published nginx manifest commit |
| [manifests/nginx/resources.yaml](manifests/nginx/resources.yaml) | Restricted nginx pods, page/config, NodePort 30080 and disruption budget |
| [requirements.lock](requirements.lock) | Pinned Ansible/Python provisioning dependencies |
| [tests/check.py](tests/check.py) | Local inventory, syntax, chart and optional nginx runtime checks |
| [tests/check_private_inventory.py](tests/check_private_inventory.py) | Production topology, private addresses, API SANs and no-VIP assertions |
| [deploy.yaml](deploy.yaml) | Runs the host firewall playbook, then pinned Kubespray |
| [firewall.yaml](firewall.yaml) / [template](templates/firewall.nft.j2) | Serial persistent host protection with timed lockout rollback |
| [tests/check_firewall.py](tests/check_firewall.py) | Firewall render and rollout ordering assertions |
| [tests/check_live_firewall.py](tests/check_live_firewall.py) | Preinstall-only temporary listeners to test private paths/public filtering |
| [tests/check_acceptance.py](tests/check_acceptance.py) | Read-only live acceptance checks, including optional nginx checks |

## Architecture and availability

- Three schedulable nodes each belong to `kube_control_plane`, `etcd` and
  `kube_node`. Three etcd voters tolerate one unavailable node, not two.
  Workloads run on control-plane nodes because this is a small take-home cluster.
- SSH uses public `ansible_host`; Kubernetes, etcd and Cilium use private `ip`
  and `access_ip`. Cilium uses VXLAN with kube-proxy/IPVS retained and two
  operator replicas.
- BGP, kube-vip, Reserved IP and external API load balancing are not configured.
  Host clients use their local apiserver. The selected availability criterion is
  surviving nodes retaining API access after one whole-node failure. A local
  apiserver-only failure temporarily blocks that node's host clients until kubelet
  restarts the static pod; no automatic fallback is configured for those clients.
- Kubespray's nginx localhost proxies for non-control-plane nodes are not installed
  on these control planes. `loadbalancer_apiserver_localhost: true` and
  `kube_apiserver_bind_address: "::"` still direct Cilium and kube-proxy to node-local
  APIs. Disabling localhost loadbalancing would pin some clients to the first
  control plane. `kubeconfig_localhost_ansible_host: true` makes the exported admin
  config select the first node's public SSH address; it does not switch endpoints.
- External API clients initially use an authorized SSH tunnel to any surviving
  node. Public **6443** remains blocked; source-restricted reviewer API access can
  be added separately. Clients manually switch endpoints or supply their own
  load balancer. API certificate SANs include all three public node IPs and
  `127.0.0.1`. An admin kubeconfig does not grant SSH access.
- Argo CD's pinned HA bundle bootstraps clusterdOS v0.4.8. Required gitapps are
  `certmanager`, `metricsserver` and `nfd`; its `argo` and `cilium` gitapps are
  disabled to avoid competing managers. The `config` gitapp uses the vendored
  chart described in [the config workaround](#clusterdos-config-workaround).
- metrics-server verifies kubelet TLS and uses cert-manager for its own serving
  certificate and aggregated API CA injection. Argo ignores the injected
  APIService CA bundle rather than overwriting cainjector's work. Kubelet serving
  CSRs need individually verified approval; automatic serving CSR approval is
  disabled because provider addressing/DNS is not independently verified.
  Kubespray still enables client certificate rotation. NFD worker pods inspect
  host hardware by design.
- Three nginx replicas serve HTTP on each node's public port **30080**, without a
  cloud load-balancer controller. Pods have node spread, a disruption budget,
  probes and resource bounds; the container runs non-root with a read-only
  filesystem. HTTP clients switch to a surviving node manually. One fixed public
  node URL is not an automatically failing-over endpoint.

## Known limits

This is production-oriented, not a claim of complete production readiness.
External API and HTTP clients must manually switch node addresses or use a
separately managed load balancer. Workload access through the Kubernetes Service
needs live recovery tests. NFD has replicated masters and labels all three nodes,
but its failure recovery remains unverified; not every auxiliary chart component
is replicated. A PDB protects voluntary interruptions, not node crashes.

No automatic node replacement, off-host etcd backup, public HTTPS, OIDC or alerting
backend is configured. Sharing one admin identity gives full control but not
per-reviewer audit attribution. The intended lifetime is the evaluation period,
followed by teardown. See [status](#status) for unverified tests and handoff gaps.

## Firewall and SSH design

[firewall.yaml](firewall.yaml) owns only the `inet aranya` nftables table, never
flushes the ruleset, and leaves outbound traffic and private/pod forwarding to
Kubernetes/Cilium. Public forwarding permits only the nginx NodePort-to-pod
port 8080 flow. ICMP/IPv6 neighbor discovery and established connections remain
allowed. This assumes an IPv4 cluster and the standard kube-proxy NAT path.

SSH listens on **22** with an operator source allowlist and **2026** open to all
sources. Both require authorized keys; password and keyboard-interactive
authentication are disabled. The playbook configures both sshd and Ubuntu's
`ssh.socket`, validates SSH configuration before restarting listeners, preserves
existing authenticated sessions, and verifies new connections on both ports.
Existing authorized keys are unchanged; opening 2026 does not provision reviewer
accounts. `HostKeyAlias` in the tunnel command reuses the independently verified
host key stored under the node IP for the non-default port.

The deployment wrapper applies the firewall before Kubespray, serially, one host
at a time. A candidate rules file is validated before activation. The playbook
saves an existing `/etc/aranya-firewall.nft` as `/etc/aranya-firewall.previous.nft`
and arms a 180-second rollback timer before replacing active rules. If a fresh
SSH connection is not verified, the rollback script restores previous rules and
their persistent service; on first installation it disables the Aranya service
instead. Successful reconnection cancels the timer. A firewall failure stops the
deployment, and reruns atomically replace only the owned table.

The [default rules table](RUNBOOK.md#default-firewall-rules-applied-by-ansible)
follows the Ansible deployment command and labels public listeners,
source-restricted access and private cluster paths. Provider connectivity for
DNS, NTP and upstream downloads is checked during preflight; the host table
leaves outbound traffic allowed rather than adding destination allowlists.

### Operator access via SSH 2026

On an already provisioned cluster, an operator whose public IPv4 is not yet
allowlisted for port 22 can connect with Ansible on **2026**, provided their SSH
key is already authorized. This does not grant reviewer access or install keys.
Use the production inventory, trusted host keys and workstation setup from the
[runbook](RUNBOOK.md#2-set-up-the-workstation). Keep any verified
`UserKnownHostsFile` setting in `ANSIBLE_SSH_COMMON_ARGS`.

1. Verify authenticated access through port 2026 on all three nodes:

   ```bash
   export INVENTORY="$ROOT/inventory/production/hosts.yaml"
   export ANSIBLE_HOST_KEY_CHECKING=True
   export ANSIBLE_SSH_ARGS='-o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=yes'
   "$ROOT/.venv/bin/ansible" -i "$INVENTORY" all -m ansible.builtin.ping \
     -e '{"ansible_port":2026,"ansible_ssh_extra_args":"-o HostKeyAlias={{ ansible_host }}"}'
   ```

   `HostKeyAlias` reuses the independently verified key stored under each node's
   IP. Do not disable host-key checking to work around a failed connection.
2. After authorization, add the operator's public **egress IPv4** (including any
   VPN/NAT) to `firewall_admin_sources` in
   `inventory/production/group_vars/all.yaml`. Preserve every existing address,
   including the source of the current Ansible connection. For example, append
   `203.0.113.20` as another YAML list entry; substitute the actual public IPv4,
   not a private workstation address. Also add it to the comma-separated
   `FIREWALL_ADMIN_SOURCES` in your trusted `.local/production.env`, if used, so
   future inventory rendering retains it. Do not overwrite existing inventory.
3. Apply the persistent host rules through the firewall playbook, still using
   port 2026. **This changes firewall rules and may refresh SSH listeners on all
   three nodes**; use the existing serial rollout and rollback protection:

   ```bash
   "$ROOT/.venv/bin/ansible-playbook" -i "$INVENTORY" "$ROOT/firewall.yaml" \
     -e '{"ansible_port":2026,"ansible_ssh_extra_args":"-o HostKeyAlias={{ ansible_host }}"}'
   ```

   Add `--ask-become-pass` in an interactive terminal if sudo requires it. Require
   a successful recap for all three nodes, then verify port 22 from the newly
   allowlisted source with a fresh connection:

   ```bash
   "$ROOT/.venv/bin/ansible" -i "$INVENTORY" all -m ansible.builtin.ping \
     -e '{"ansible_port":22,"ansible_ssh_args":"-o ControlMaster=no -o StrictHostKeyChecking=yes"}'
   ```

The allowlist affects **SSH 22 only**; SSH 2026 remains public and key-only, and
public API port 6443 remains blocked. This repository manages nftables, not a
separate iptables host allowlist. Do not insert ad hoc `nft`/`iptables` rules or
flush either ruleset: the playbook persists the owned `inet aranya` table, while
Kubernetes/Cilium retain their own networking rules. For reviewer kubeconfig
access, pair separately authorized SSH credentials with the
[node-local API tunnel](RUNBOOK.md#workstation-api-connectivity-and-manual-switching).

## Versions

| Component | Pin |
| --- | --- |
| Kubespray | v2.32.0 / `9751ea6f63244e433a7a6263d9ecf381e57bb8e9` |
| Kubernetes | 1.36.4 (Kubespray release default) |
| Cilium | 1.20.2 (Kubespray release default) |
| Ansible / ansible-core | 12.3.0 / 2.19.15 (`requirements.lock`) |
| Argo CD | v3.6.1 HA bundle |
| clusterdOS | v0.4.8 / `92a191cdb1ecda83c25d9d218fce3dd3f7b2ba1c` |
| cert-manager chart | v1.21.1 (clusterdOS defaults) |
| metrics-server chart | 3.14.0 (clusterdOS defaults) |
| NFD chart | v0.19.0 (clusterdOS defaults) |
| Helm (local rendering only) | v3.19.0 |
| nginx | 1.30.6-alpine; amd64 digest pinned in its Deployment |

Argo 3.6 documents testing against Kubernetes 1.34-1.37. The nginx digest is
amd64-specific. `requirements.lock` records resolved Python dependencies.

## Inventory rendering

The environment sample contains documentation-only addresses, not deployment
inputs. The operator supplies eight required values and two optional subnet values.
Preflight discovers the three private IPv4 addresses on the explicitly selected
private interface and writes them to a private local environment file. After the
operator sources that file, the renderer receives all 11 required values. It derives
`access_ip` from each private IP, API SANs from the public IPs and firewall YAML
from a comma-separated operator source list.

The renderer refuses to overwrite existing inventory. It validates required
values, IPv4 addresses, unique node addresses, RFC1918 private IPs, firewall source
addresses, distinct interfaces and pod/service subnet overlap, then renders and
YAML-parses the files. It cannot check routes or networks not supplied. Output
is git-ignored and mode-restricted. The current workstation already has production
inventory; a fresh rebuild using the public templates remains untested.

## clusterdOS config workaround

`charts/config` vendors the v0.4.8 chart at upstream commit
`92a191cdb1ecda83c25d9d218fce3dd3f7b2ba1c`. Its only chart change is
`Chart.yaml`'s unsupported `apiVersion: v3` to `v2`. Upstream values and template
are unchanged; the directory preserves the GPLv3 license and provenance README.

`install.yaml` redirects only `gitapps.config` with `internal: false` to the public
repository's `charts/config` at a pinned commit. It explicitly sets
`gitapps.config.values.clustername` equal to `clusterdos.clustername`: Helm 4 omits
the default null map, bypassing upstream's injection. Kubespray `cluster_name`
also influences the DNS domain and is distinct from the clusterdOS display name;
metrics-server's `tls.clusterDomain` must match any changed Kubespray DNS domain.

## Validation coverage

Local checks cover inventory/private-IP/no-VIP assertions, upstream Kubespray
syntax, clusterdOS chart renders, replica/anti-affinity/TLS settings, nginx manifest
security and optional real nginx HTTP using non-root/read-only Podman. For the
vendored chart, `tests/check.py` checks unchanged upstream files and license,
Helm lint/render, and failure when the cluster name is missing. Local checks do
not contact nodes or demonstrate deployment. Upstream syntax checks may warn
about unused groups: `bastion`, `calico_rr`, `_kubespray_needs_etcd`.

The preflight script runs an Ansible playbook that validates the eight exported
inputs before remote checks. It discovers exactly one global RFC1918 IPv4 address
on each selected private interface, checks uniqueness and subnet overlap, and
writes `.local/discovered.env` only after all nodes pass. Ansible gathers fresh
facts and verifies privilege escalation, resources, interfaces, peer routes/pings,
time synchronization, firewall tooling and DNS/HTTPS endpoints without changing
node configuration. An unsuccessful run may leave an older discovery file in place;
it must not be sourced. Python/Ansible may create temporary files and a local
facts cache. Host-key identity, package-repository access, external route overlap
and provider TCP/UDP policy still need manual verification; a reachable registry
endpoint does not prove image pulls. The RAM floor allows OS-reserved memory on
nominal 8 GiB nodes. The local preflight regression test never contacts the cluster.

Read-only live acceptance checks node readiness, private InternalIPs, Cilium,
Argo and required addon health, metrics TLS/API, NFD, public SSH 2026 and filtering
of etcd/API/kubelet ports. `--nginx` additionally checks config/nginx Application
health, three spread nginx pods and public HTTP on all three nodes. These checks
do not prove failover or reviewer-side decryption. See [status](#status) for results.

## Publication

The public repository is [johngarciasre/aranya](https://github.com/johngarciasre/aranya).
The submission is published on `main` and `takehome` and tagged `v0.1.1`.
Resolve the release commit with `git rev-parse 'v0.1.1^{commit}'`.

The config chart and nginx manifest source commit is
`153915629b15f772ea88ada63556c148768d22d0`. Both Applications pin content SHAs,
not branch heads. A pointer commit references previously published content rather
than itself; later content changes need a new content commit and pointer update.

## Remaining handoff

Reviewer SSH access and GPG encryption are treated as solved. Send the final
runbook and encrypted admin kubeconfig in the original email thread, along with
the public repository link, nginx URL and SSH tunnel instructions. Do not attach
either plaintext kubeconfig. Reviewer decryption and live access confirmation
remain to be collected. Follow the
[handoff procedure](RUNBOOK.md#10-encrypt-admin-kubeconfig-for-all-three-reviewers)
and [email checklist](RUNBOOK.md#11-final-email-checklist).
