# Aranya Kubernetes take-home

A reproducible three-node Kubespray deployment with private Cilium networking,
Argo CD, clusterdOS and an nginx `hello aranya` service. BGP and kube-vip are disabled.

**Status: cluster, addons and nginx are deployed; failure testing and reviewer
handoff remain pending.** On 2026-11-06 UTC all three Kubernetes v1.36.4 nodes
were Ready, Cilium was 3/3 and its operator 2/2. Argo CD v3.6.1 workloads were
Ready: Redis 3/3, server and repo-server 2/2 each, and controller 1/1. All six
Applications, including clusterdOS and nginx, were Synced/Healthy. The metrics
APIService was Available with TLS verification and `kubectl top` worked; NFD
reported Intel CPU vendor labels on all three nodes. Nginx had three Ready pods,
one per node; public HTTP returned `hello aranya` on all three node addresses.

Upstream clusterdOS v0.4.8 uses unsupported `apiVersion: v3` for its config chart;
the vendored fix and an explicit cluster name restore reconciliation. Persistent
nftables host protection and the revised rollback playbook were applied serially
on all nodes. Public etcd, API and kubelet ports were filtered. The rollback script
was not tested by inducing an SSH lockout. Failover, reviewer access, GPG encryption,
recipient decryption and email handoff remain unverified.

## Contents

| File | Purpose |
| --- | --- |
| [RUNBOOK.md](RUNBOOK.md) | Prerequisites, bootstrap, publication, live checks, endpoint switching, GPG handoff and operations |
| [inventory/example/hosts.yaml](inventory/example/hosts.yaml) | Three stacked control-plane/etcd nodes; public SSH, private cluster traffic |
| [inventory/example/group_vars](inventory/example/group_vars) | Cilium, Kubernetes, API SANs and kubelet certificate settings; no VIP |
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

- Three schedulable control-plane nodes with three etcd voters, tolerating one
  whole-node failure at the quorum level. Cilium VXLAN uses private node addresses.
- Host clients use their local apiserver. The selected availability criterion is
  surviving nodes retaining API access after one whole-node failure. A local
  apiserver-only failure temporarily blocks that node's host clients until kubelet
  restarts the static pod; no automatic fallback is configured for those clients.
- External API clients initially use an authorized SSH tunnel to any surviving
  node. Public **6443** remains blocked; source-restricted reviewer API access can
  be added separately. Clients manually switch endpoints or supply their own load
  balancer. All public node IPs are included in production API certificate SANs.
- Argo CD's pinned HA bundle bootstraps clusterdOS v0.4.8. Required gitapps are
  `certmanager`, `metricsserver` and `nfd`; unnecessary apps remain disabled.
  The `config` gitapp is redirected to the published vendored chart.
- metrics-server verifies kubelet TLS and uses cert-manager for its own serving
  certificate/aggregated API CA injection. Kubelet serving CSRs need individually
  verified approval; no blanket insecure TLS workaround is included.
- Three nginx replicas serve HTTP on each node's public
  port **30080**. The container runs non-root with a read-only filesystem, probes
  and resource bounds. HTTP clients also switch to a surviving node manually.
  One fixed public node URL is not an automatically failing-over endpoint.

## Local validation

With the pinned sources and tools installed per the runbook:

```bash
.venv/bin/python tests/check.py --container
.venv/bin/python tests/check_private_inventory.py inventory/production/hosts.yaml
.venv/bin/python tests/check_firewall.py
.venv/bin/python tests/check_firewall.py inventory/production/hosts.yaml
.venv/bin/python -m pip check
```

The checks cover inventory/private-IP/no-VIP assertions, upstream Kubespray syntax,
clusterdOS chart renders, replica/anti-affinity/TLS settings, nginx manifest
security and optional real nginx HTTP using non-root/read-only Podman. For the
vendored config chart, `tests/check.py` checks unchanged upstream files and license,
Helm lint/render, and failure when the cluster name is missing. Upstream syntax
checking may warn about unused optional/internal groups: `bastion`, `calico_rr`,
`_kubespray_needs_etcd`.

With an authenticated SSH tunnel on localhost, run the read-only live checks:

```bash
.venv/bin/python tests/check_acceptance.py inventory/production/hosts.yaml --server https://127.0.0.1:16443
# Full acceptance, after nginx deployment:
.venv/bin/python tests/check_acceptance.py inventory/production/hosts.yaml --server https://127.0.0.1:16443 --nginx
```

The default excludes `clusterdos-config` and nginx; `--nginx` also checks both
Applications and public HTTP. These checks do not prove failover.

Full non-disruptive live acceptance passed on 2026-11-06 UTC. It does not prove
failover or recipient decryption. The config chart and nginx manifest source commit
is `153915629b15f772ea88ada63556c148768d22d0` on the public `takehome` branch.

## Complete the assignment

1. Obtain approval for disruptive failure tests; restore any stopped node afterward.
2. Agree and provision reviewer access; public API and unauthorized SSH remain blocked.
3. Authenticate all three reviewer GPG fingerprints, encrypt a tested admin
   kubeconfig to all three recipients, and supply manual API switching instructions.
4. With approval, attach the final runbook and encrypted kubeconfig in the
   original email thread.

Credentials, production inventory, artifacts and private tooling directories are
ignored by Git. Review the staged file list and diff for secrets before publication.
