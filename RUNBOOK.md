# Aranya: build and operate the cluster

## Status and scope

**The cluster and required addons are installed; config repair, publication,
nginx and end-to-end acceptance remain pending.** Three Kubernetes v1.36.4 nodes
are Ready, Cilium is 3/3 and its operator is 2/2. Argo CD v3.6.1 workloads are
Ready: Redis 3/3, server and repo-server 2/2 each, and controller 1/1. The
clusterdOS parent and cert-manager, metrics-server and NFD Applications are
Synced/Healthy. The metrics APIService is Available with TLS verification,
`kubectl top` works, and NFD reports Intel CPU vendor labels on all three nodes.

`clusterdos-config` is Unknown with ComparisonError: the upstream v0.4.8 chart
uses unsupported `apiVersion: v3`. The corrected vendored chart and `install.yaml`
override are prepared but must be published and applied. Host firewall protection
is active, and preinstall listeners verified private paths and public filtering.
Public HTTP, failover, GPG encryption, recipient decryption and email handoff
remain unverified. The private production inventory contains the supplied node
addresses and SSH settings; the public example remains parameterized. BGP and
kube-vip are disabled; external clients switch node endpoints manually or provide
their own load balancer.

This runbook starts with provider-provisioned, fresh Linux servers (the email
supplies servers, so provisioning VMs is outside its scope). Kubespray installs
containerd, Kubernetes, stacked etcd and Cilium. Argo CD installs clusterdOS and
reconciles nginx from the submission repository. No application secrets belong in
Git. Run commands in Bash on the operator workstation, except where stated.

## 1. Design and prerequisites

### Topology

- Three nodes, each in `kube_control_plane`, `etcd` and `kube_node`.
  Three etcd voters tolerate **one** unavailable node, not two. Workloads are
  permitted on control-plane nodes because this is a small take-home cluster.
- SSH uses public `ansible_host`; Kubernetes/etcd/Cilium use private `ip` and
  `access_ip`. These must be real addresses assigned to the nodes.
- Cilium uses VXLAN with kube-proxy/IPVS retained; its operator has two replicas.
- No BGP, kube-vip, Reserved IP or external API load balancer is configured.
  Host components use their local apiserver. Losing one whole node leaves two
  apiservers and etcd quorum; external clients manually select a surviving node.
- Argo CD uses the upstream HA install bundle; clusterdOS manages cert-manager,
  metrics-server and NFD. Disable its `argo` and `cilium` gitapps to prevent two
  tools managing those components. Once published and applied, the override directs
  the enabled `config` gitapp to the vendored chart instead of the broken upstream chart.
- The nginx design spreads three pods across nodes, with resource bounds, HTTP
  probes, restricted pod security and a PDB. A NodePort exposes HTTP at
  `http://NODE_PUBLIC_IP:30080/` without assuming a cloud load-balancer controller;
  deployment and public reachability remain to be verified.

### Known limits

This is production-oriented, not a claim of complete production readiness.
The selected availability criterion is surviving nodes retaining API access after
one whole-node failure. External API and HTTP clients must manually switch node
addresses or use a separately managed load balancer; a fixed node URL is not an
HA endpoint. Local apiserver-only failure temporarily blocks that node's host
clients until kubelet restarts the static pod; there is no host-client fallback to
another apiserver. Workloads using the Kubernetes Service need live recovery tests.
No automatic node replacement, off-host etcd backup, public HTTPS, OIDC or alerting
backend is configured. NFD has replicated masters and labels all three nodes, but
its failure recovery remains unverified; not every auxiliary chart component is
replicated. A PDB protects voluntary interruptions, not node crashes. Sharing one
admin identity gives full control but not per-reviewer audit attribution; delete
the take-home cluster after evaluation.

### Infrastructure gate: do not skip

Confirm with the provider/assignment owner:

1. Three disposable servers, preferably Ubuntu 24.04 LTS, x86_64, kernel >=5.10,
   unique names/MACs, Python 3, key-based SSH and sudo. Aim for >=4 vCPU/8 GiB RAM
   and >=30 GiB disk **per node** to leave room for the Argo HA bundle and addons.
   Do not run Kubespray over an existing cluster or valuable workloads.
2. Private connectivity between all nodes. Confirm their private CIDR, interface
   names and routes using `ip -br address` and `ip route` over SSH. Ensure pod
   `10.233.64.0/18` and service `10.233.0.0/18` ranges do not overlap the node,
   VPN, corporate or other routed networks; change them before installation if so.
3. Agree reviewer access: source-restricted public TCP 6443 on each node, or
   SSH tunnels to individual private node APIs. No VIP or provider BGP is needed.
4. DNS, time synchronization and outbound HTTPS for package repositories,
   GitHub, GitLab, registry.k8s.io, quay.io, Docker Hub and Helm repositories.
5. Firewall rules below exist at the provider and/or OS layer. Kubespray is not
   a provider-firewall manager. Keep the firewall; do not blindly disable it.
6. Operator workstation: Linux, Python 3.12 with venv/pip, Git, curl, tar,
   sha256sum, OpenSSH, OpenSSL and GnuPG. Podman is optional for the smoke test.
   On Ubuntu, install missing prerequisites only after reviewing their impact:
   `sudo apt-get install python3-venv python3-pip git curl openssh-client openssl gnupg`.

### Firewall policy

| Path | Allow | Restriction |
| --- | --- | --- |
| Operator/reviewer -> node | TCP 22 | Only authenticated operator/reviewer source IPs |
| Internet -> node | TCP 30080 | Only intended public HTTP service |
| Operator/reviewer -> public API | TCP 6443 | Only authorized source IPs; omit for SSH-only access |
| Private nodes -> API | TCP 6443 | Cluster node addresses |
| Private control planes -> etcd | TCP 2379, 2380 | Only cluster nodes, never public |
| Private nodes/pods -> kubelet | TCP 10250 | Cluster node/pod CIDRs, never public |
| Private nodes -> nodes | UDP 8472; TCP 4240; ICMP | VXLAN and Cilium health checks |
| Control planes -> pod CIDR | TCP 10250, 443 | Metrics-server and webhook traffic, via CNI |
| Cluster workloads -> resolvers | UDP/TCP 53 | Configured DNS resolvers |
| Nodes -> time source | UDP 123 | Configured NTP source |
| Nodes/workloads -> upstreams | TCP 443; OS repo ports as needed | Required downloads and GitOps |

Do not open etcd, kubelet, Cilium overlay or Argo UI to the public internet. With
an external public API LB, allow TCP 6443 only from authorized reviewer sources
and the LB backend paths. Hubble/Prometheus ports are unnecessary unless enabled.
See the pinned [Kubespray ports](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/operations/port-requirements.md)
and [Cilium requirements](https://docs.cilium.io/en/v1.20/operations/system_requirements/)
for additional features. Test the actual network, not just the rule definitions.

The approved host implementation is `firewall.yaml` with `templates/firewall.nft.j2`.
It owns only the `inet aranya` table, never flushes the ruleset, and leaves outbound
traffic and private/pod forwarding to Kubernetes/Cilium. Public forwarding permits
only the selected nginx NodePort-to-pod port 8080 flow. Public API access stays
closed; use SSH tunnels initially. ICMP/IPv6 neighbor discovery and established
connections remain allowed. This assumes the current IPv4 cluster and standard
kube-proxy NAT path; rerun live HTTP and filtering tests after Cilium installation.

## 2. Versions and workstation setup

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

Argo 3.6 documents testing against Kubernetes 1.34-1.37. Do not use upstream
`master`, `main`, `stable`, `latest` or unpinned chart upgrades during reproduction.
The nginx image digest is amd64-specific; choose and verify the appropriate digest
before using ARM nodes. `requirements.lock` records all resolved Python dependencies.

After publication, clone the submission repository and replace the revision with
its full submission commit SHA:

```bash
git clone https://github.com/johngarciasre/aranya.git
cd aranya
git checkout REPLACE_WITH_SUBMISSION_COMMIT
export ROOT="$PWD"
umask 077
mkdir -p "$ROOT/.cache" "$ROOT/.local"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.lock"
git clone --depth 1 --branch v2.32.0 \
  https://github.com/kubernetes-sigs/kubespray.git "$ROOT/.cache/kubespray"
test "$(git -C "$ROOT/.cache/kubespray" rev-parse HEAD)" = \
  9751ea6f63244e433a7a6263d9ecf381e57bb8e9
git clone --depth 1 --branch v0.4.8 \
  https://gitlab.com/aranya-tech/public/clusterdos.git "$ROOT/.cache/clusterdos"
test "$(git -C "$ROOT/.cache/clusterdos" rev-parse HEAD)" = \
  92a191cdb1ecda83c25d9d218fce3dd3f7b2ba1c
```

Commands creating directories/clones are for a fresh checkout. On a repeat run,
reuse and verify existing clones rather than overwriting them. For local rendering
checks, download Helm into `.cache` (this example workstation is linux/amd64):

```bash
curl --fail --show-error --location \
  https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz \
  --output "$ROOT/.cache/helm-v3.19.0-linux-amd64.tar.gz"
curl --fail --show-error --location \
  https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz.sha256sum \
  --output "$ROOT/.cache/helm-v3.19.0-linux-amd64.tar.gz.sha256sum"
(cd "$ROOT/.cache" && sha256sum --check helm-v3.19.0-linux-amd64.tar.gz.sha256sum)
tar -xzf "$ROOT/.cache/helm-v3.19.0-linux-amd64.tar.gz" -C "$ROOT/.cache"
"$ROOT/.cache/linux-amd64/helm" version --short
"$ROOT/.venv/bin/python" "$ROOT/tests/check.py"
# Optional: pulls nginx and runs/removes only a test container on this workstation.
"$ROOT/.venv/bin/python" "$ROOT/tests/check.py" --container
```

The checks parse inventory, syntax-check upstream `cluster.yml`, render the parent
and requested child charts, and assert nginx settings. For `charts/config`, they
check the unchanged upstream values, template and license byte-for-byte, verify
`apiVersion: v2`, run Helm lint/render and require rendering without a cluster name
to fail. They never contact cluster nodes. Upstream syntax checking may warn about
unused `bastion`, `calico_rr` and `_kubespray_needs_etcd` groups. These are not
deployment validation.

## 3. Fill the inventory

For a fresh checkout only; reuse an existing production inventory without copying
over it. The current workstation already has the supplied production inventory.

```bash
test ! -e "$ROOT/inventory/production" && cp -R "$ROOT/inventory/example" "$ROOT/inventory/production"
export INVENTORY="$ROOT/inventory/production/hosts.yaml"
export SSH_KEY="$HOME/.ssh/REPLACE_WITH_ASSIGNMENT_KEY"
chmod 600 "$SSH_KEY"
```

Edit the production files, not `.cache/kubespray/inventory/sample`:

| File/field | Required input |
| --- | --- |
| `hosts.yaml`: `ansible_host` | Node's public SSH IP; examples `192.0.2.*` are documentation-only |
| `hosts.yaml`: `ip`, `access_ip` | Node's actual private IP; keep them equal |
| `ansible_user`, `ansible_ssh_private_key_file` | Provided SSH username/key; production sets these in `hosts.yaml` |
| `kube_vip_*` | Keep disabled; do not add `loadbalancer_apiserver` for this design |
| `group_vars/k8s_cluster.yaml`: API SAN list | All three actual public control-plane IPs |
| `group_vars/k8s_cluster.yaml`: pod/service ranges | Confirm no network overlap |
| `group_vars/all.yaml`: `firewall_admin_sources` | Exact authorized operator IPv4 addresses; include the current SSH source |
| `group_vars/all.yaml`: `firewall_private_interface`, `firewall_public_interface` | Actual interfaces, currently eth1 and eth0 |
| `install.yaml`: `clusterdos.clustername` | Already set to `aranya-takehome`; choose a unique name if needed |
| `install.yaml`: `gitapps.config.targetRevision` | Full published commit SHA containing `charts/config` |
| `argocd/nginx.yaml`: repo URL/revision | `https://github.com/johngarciasre/aranya.git` and full published commit SHA containing `manifests/nginx` |

Do not confuse Kubespray `cluster_name` (which also influences the DNS domain) with
the clusterdOS display name. Leave Kubespray DNS defaults unless intentionally
changing the metrics-server chart's `tls.clusterDomain` too.

No shared VIP/LB is needed for the selected whole-node failure criterion. All
three nodes are control planes: Kubespray's default nginx localhost proxies for
non-control-plane nodes are not installed here. Host clients use their local API;
the generated external admin config initially selects one node. It does not
switch automatically, so use the endpoint-switching instructions below.

Keep `loadbalancer_apiserver_localhost: true` and
`kube_apiserver_bind_address: "::"` despite having no proxy pods on these control
planes. Kubespray uses these settings to point Cilium and kube-proxy at node-local
APIs. Setting localhost loadbalancing to false would instead pin some clients to
the first control plane. `kubeconfig_localhost_ansible_host: true` makes the exported
admin config select the first node's public SSH address, covered by the SAN list.

Verify SSH host-key fingerprints with the assignment owner or provider console,
then add only those verified keys to your workstation's `known_hosts`. `ssh-keyscan`
alone does not authenticate a host. Kubespray's stock config disables host-key
checking; explicitly override it and its `/dev/null` known-hosts setting:

```bash
export ANSIBLE_CONFIG="$ROOT/.cache/kubespray/ansible.cfg"
export ANSIBLE_HOST_KEY_CHECKING=True
export ANSIBLE_SSH_ARGS='-o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=yes'
export ANSIBLE_CACHE_PLUGIN_CONNECTION="$ROOT/.local/facts"
mkdir -p "$ANSIBLE_CACHE_PLUGIN_CONNECTION"
"$ROOT/.venv/bin/ansible-inventory" -i "$INVENTORY" --graph
# Inspect privately; --list contains infrastructure details and potentially future secrets.
"$ROOT/.venv/bin/ansible-inventory" -i "$INVENTORY" --list
(cd "$ROOT/.cache/kubespray" && "$ROOT/.venv/bin/ansible" \
  -i "$INVENTORY" all --private-key "$SSH_KEY" --become -m ping)
```

If sudo requires a password, add `--ask-become-pass` to Ansible commands in an
interactive terminal. Never store it in inventory, shell history or Git. Confirm
private interfaces, routes, node OS and resources over SSH before proceeding.
If verified keys are stored separately, add a `UserKnownHostsFile` option to
`ANSIBLE_SSH_ARGS` including both that file and the workstation's `known_hosts`.

Validate the real inventory before provisioning:

```bash
"$ROOT/.venv/bin/python" "$ROOT/tests/check_private_inventory.py" "$INVENTORY"
"$ROOT/.venv/bin/python" "$ROOT/tests/check_firewall.py" "$INVENTORY"
```

The deployment entry point in section 4 imports `firewall.yaml` first, then the
pinned Kubespray playbook. `nft` and `systemd-run` must already be installed. The
firewall play validates rules and operates one host at a time. Before activation,
it installs `/etc/aranya-firewall-rollback.sh` and arms a 180-second timer. On a
rerun it first saves `/etc/aranya-firewall.nft` as
`/etc/aranya-firewall.previous.nft`; if reconnection is not verified, the script
restores that configuration, reloads its rules and enables its persistent service.
On first installation, the script disables the Aranya firewall service instead to
recover SSH. The play closes the existing SSH connection, verifies a new one and
only then cancels the timer. A firewall failure stops deployment. A rerun atomically
replaces only its own table; review existing firewall policy before using it on
non-fresh nodes. The revised rollback behavior is prepared locally; do not assume
it has been deployed to the nodes without verification.

Optionally, with approval on fresh nodes, apply the firewall separately and test
ports before the combined deployment. The combined deployment will apply it again.

```bash
"$ROOT/.venv/bin/ansible-playbook" -i "$INVENTORY" "$ROOT/firewall.yaml" --become
# Preinstall only: briefly binds service-port listeners, then closes them.
"$ROOT/.venv/bin/python" "$ROOT/tests/check_live_firewall.py" "$INVENTORY" --preinstall
```

Do not run the listener test after Kubernetes installation; its ports will be in
use. If the operator IP changes, update the allowlist through an authorized source
or the provider console. Reviewer SSH/public API grants require separate approval;
do not open 6443 globally to work around connectivity.

## 4. Provision the fresh nodes

**This changes all selected servers**, installing packages, replacing networking,
configuring Kubernetes/etcd and disabling swap as required. Obtain authorization
and verify the inventory before running. Syntax-checking is not a safe dry-run of
the eventual infrastructure changes; Kubespray `--check` is not a deployment test.

Run from the Kubespray checkout with its Ansible configuration so its relative
role and library paths resolve. The wrapper imports the local firewall playbook
first and then the unmodified pinned Kubespray playbook.

```bash
(cd "$ROOT/.cache/kubespray" && "$ROOT/.venv/bin/ansible-playbook" \
  -i "$INVENTORY" "$ROOT/deploy.yaml" --syntax-check)
(cd "$ROOT/.cache/kubespray" && "$ROOT/.venv/bin/ansible-playbook" \
  -i "$INVENTORY" "$ROOT/deploy.yaml" --private-key "$SSH_KEY" --become)
```

Do not use `--skip-tags`, `--start-at-task` or a partial `--limit` to bypass the
firewall on hosts being provisioned. Require a successful recap for all three nodes.
If a run fails, diagnose the first failure, correct its cause and rerun the same
pinned playbook; do not erase etcd
or bypass checks as a shortcut. Keep logs private and redact credentials.

Kubespray writes `inventory/production/artifacts/admin.conf` and a matching kubectl
binary. Both are ignored by Git. Use that kubectl rather than an unrelated client:

```bash
export KUBECONFIG="$ROOT/inventory/production/artifacts/admin.conf"
export KUBECTL="$ROOT/inventory/production/artifacts/kubectl"
chmod 600 "$KUBECONFIG"
chmod 700 "$KUBECTL"
```

### Workstation API connectivity and manual switching

Keep the generated config private and create an operator copy. All three public
API addresses are in the certificate SAN list. After confirming source-restricted
TCP 6443 access, select a node:

```bash
cp "$KUBECONFIG" "$ROOT/.local/operator.kubeconfig"
export KUBECONFIG="$ROOT/.local/operator.kubeconfig"
export API_CLUSTER=$("$KUBECTL" config view --minify -o jsonpath='{.contexts[0].context.cluster}')
export NODE_PUBLIC_IP=REPLACE_WITH_NODE1_PUBLIC_IP
"$KUBECTL" config set-cluster "$API_CLUSTER" --server="https://$NODE_PUBLIC_IP:6443"
"$KUBECTL" get --raw='/readyz?verbose'
```

If that node is unavailable, set `NODE_PUBLIC_IP` to node2 or node3 and rerun
`config set-cluster` and `/readyz`. Test all three addresses while healthy. Do not
use `insecure-skip-tls-verify` or assume kubeconfig tries alternate endpoints.

For SSH-only access instead, keep public TCP 6443 closed and select a node-local
tunnel. Replace the SSH username/key and node address from the private inventory:

```bash
"$KUBECTL" config set-cluster "$API_CLUSTER" --server=https://127.0.0.1:16443
ssh -i "$SSH_KEY" \
  -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes \
  -N -L 127.0.0.1:16443:127.0.0.1:6443 root@REPLACE_WITH_NODE1_PUBLIC_IP
```

Keep the tunnel running in a separate terminal; restart it through node2/node3 if
needed. Certificates include `127.0.0.1`. Reviewers need their own authorized SSH
access for this option: the admin kubeconfig does not grant SSH access.

```bash
"$KUBECTL" get --raw='/readyz?verbose'
"$KUBECTL" wait nodes --all --for=condition=Ready --timeout=300s
"$KUBECTL" get nodes -o wide
"$KUBECTL" get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints
"$KUBECTL" -n kube-system get daemonset cilium
"$KUBECTL" -n kube-system rollout status daemonset/cilium --timeout=300s
"$KUBECTL" -n kube-system get deployment cilium-operator
```

Require three Ready nodes with private InternalIPs, no blocking control-plane
`NoSchedule` taints, Cilium ready on every node and two available operator replicas.
If expected taints remain, investigate Kubespray's result before removing them.

## 5. Validate and approve kubelet serving certificates

Inventory sets `kubelet_rotate_server_certificates: true`, so kubelets request
CA-signed serving certificates. The automatic CSR approver is explicitly disabled:
unknown provider addressing/DNS is not a safe basis for automatic approval.

```bash
"$KUBECTL" get csr -o wide
export CSR=REPLACE_WITH_ONE_KUBELET_SERVING_CSR
"$KUBECTL" get csr "$CSR" -o yaml
"$KUBECTL" get csr "$CSR" -o jsonpath='{.spec.request}' \
  | base64 --decode | openssl req -noout -text
```

Before approving **each** request, confirm:
- signer `kubernetes.io/kubelet-serving`, requester `system:node:NODE_NAME`;
- subject `CN=system:node:NODE_NAME`, organization `system:nodes`;
- DNS/IP SANs match only that verified node (in particular its private InternalIP);
- usages are server authentication and required signing/key encipherment, not an
  unrelated identity. Compare against `kubectl get node NODE_NAME -o yaml` and
  the provider's authoritative inventory.

After this validation only:

```bash
"$KUBECTL" certificate approve "$CSR"
"$KUBECTL" get csr "$CSR" -o jsonpath='{.status.certificate}' \
  | base64 --decode | openssl x509 -noout -subject -issuer -dates
```

Repeat for all three serving CSRs. Do not bulk-approve all pending requests. For
longer-lived operation, monitor renewal requests and repeat verification before
expiry, or configure a separately reviewed restricted approver policy. Client
certificate rotation is distinct and remains enabled by Kubespray.

## 6. Install Argo CD and clusterdOS

Use server-side apply for the large Argo CRDs. Initial installation is into a fresh
namespace; this does not authorize overwriting an existing Argo installation.
The current cluster already has Argo CD and clusterdOS installed. For a fresh
install, publish and pin the config chart as described in section 7 before applying
`install.yaml`; its placeholder SHA cannot be resolved by Argo.

```bash
"$KUBECTL" create namespace argocd --dry-run=client -o yaml | "$KUBECTL" apply -f -
"$KUBECTL" apply --server-side -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.6.1/manifests/ha/install.yaml
"$KUBECTL" wait --for=condition=Established --timeout=120s \
  crd/applications.argoproj.io crd/appprojects.argoproj.io crd/applicationsets.argoproj.io
"$KUBECTL" -n argocd rollout status deployment/argocd-server --timeout=600s
"$KUBECTL" -n argocd rollout status deployment/argocd-repo-server --timeout=600s
"$KUBECTL" -n argocd rollout status statefulset/argocd-application-controller --timeout=600s
"$KUBECTL" -n argocd get pods -o wide
"$KUBECTL" apply --server-side --dry-run=server -f "$ROOT/install.yaml"
"$KUBECTL" apply --server-side -f "$ROOT/install.yaml"
```

Argo's HA Redis pods need three schedulable nodes; inspect Pending pods/events
instead of reducing redundancy silently. clusterdOS creates child Applications:
`clusterdos-config`, `clusterdos-certmanager`, `clusterdos-metricsserver`,
`clusterdos-nfd`. All must become Synced/Healthy; inspect their status and logs
if not. On the current cluster, `clusterdos-config` is Unknown/ComparisonError
because upstream's chart uses `apiVersion: v3`, which Helm does not support.

`charts/config` vendors the v0.4.8 config chart at upstream commit
`92a191cdb1ecda83c25d9d218fce3dd3f7b2ba1c`; its only chart change is
`Chart.yaml`'s `apiVersion: v3` to `v2`. The upstream values and template are
unchanged; the directory also contains the upstream GPLv3 license and provenance
README. `install.yaml` redirects only `gitapps.config` with `internal: false` to
`https://github.com/johngarciasre/aranya.git`, path `charts/config`, at a pinned
commit. Publish that commit and apply the override using section 7. Remove the
override only after verifying a later upstream release fixes and renders the chart.

```bash
"$KUBECTL" -n argocd get applications
"$KUBECTL" -n clusterdos-cert-manager get deployments,pods
"$KUBECTL" -n clusterdos-cert-manager rollout status deployment/clusterdos-certmanager-cert-manager --timeout=300s
"$KUBECTL" wait --for=condition=Established --timeout=180s crd/certificates.cert-manager.io
```

metrics-server's chart renders cert-manager Issuer/Certificate resources, uses
CA injection for its aggregated APIService, and trusts the Kubernetes CA when
scraping kubelets. Neither connection disables TLS verification. Argo ignores the
injected APIService CA bundle so it does not continually overwrite cainjector's
work. Its later child sync wave is useful ordering metadata, but do **not** assume
parent waves wait for child Application health: if cert-manager CRDs are not yet
installed, inspect the metrics-server Application and retry its sync once the
prerequisites are Ready. An automated failed sync at the same revision is not
necessarily retried. After the cert-manager controller/webhook are Ready, use
Argo's UI to sync `clusterdos-metricsserver`, or request that sync explicitly:

```bash
"$KUBECTL" -n argocd patch application clusterdos-metricsserver --type=merge \
  -p '{"operation":{"sync":{"prune":true}}}'
"$KUBECTL" -n kube-system wait --for=condition=Ready --timeout=300s \
  certificate/clusterdos-metricsserver-metrics-server
"$KUBECTL" wait --for=condition=Available --timeout=300s apiservice/v1beta1.metrics.k8s.io
"$KUBECTL" get apiservice v1beta1.metrics.k8s.io -o yaml
"$KUBECTL" top nodes
"$KUBECTL" top pods -A
"$KUBECTL" -n clusterdos-nfd get deployment,daemonset,pods
"$KUBECTL" get nodes -L feature.node.kubernetes.io/cpu-model.vendor_id
```

Require an APIService CA bundle, verification not disabled, working `kubectl top`
and NFD feature labels on each node. NFD worker pods inspect host hardware by
design. On failures inspect Application conditions, pod logs and events; certificate
or network errors are not a reason to add `--kubelet-insecure-tls`.

For Argo UI access, keep a local-only port-forward:
`"$KUBECTL" -n argocd port-forward svc/argocd-server 8443:443`.
Read the initial password privately, change it on login and remove the initial
password Secret after confirming the change. Do not publish the UI/password.

## 7. Publish code and deploy nginx

Before publication, inspect `.gitignore`, scan files for secrets and review the
exact file list. Do not add `.local`, `.cache`, `.venv`, `inventory/production`,
SSH/GPG keys, plaintext/encrypted kubeconfigs or credential attachments. The
public `inventory/example` is intentionally non-secret. You may publish sanitized
real inventory/network values only if authorized; never embed SSH passwords.

Publication to `https://github.com/johngarciasre/aranya` on the **takehome**
feature branch is approved, but the repository is not yet published. Start the
sequence below only after the files are ready for review. For this currently
uninitialized checkout, initialize Git on `takehome`; do not reinitialize an
existing repository or commit to `main`.

```bash
git init -b takehome
git add .gitignore README.md RUNBOOK.md requirements.lock install.yaml deploy.yaml \
  inventory/example charts/config argocd/nginx.yaml manifests/nginx/resources.yaml \
  firewall.yaml templates tests/check.py tests/check_private_inventory.py \
  tests/check_firewall.py tests/check_live_firewall.py tests/check_acceptance.py
git diff --cached --check
git diff --cached
# Check the entire staged diff for keys, tokens and confidential data before committing.
git commit -m "Add reproducible Aranya Kubespray take-home"
# Create the approved public repository, then add its SSH remote.
git remote add origin git@github.com:johngarciasre/aranya.git
git push -u origin takehome
git rev-parse HEAD
```

Record that **initial published commit's full SHA**. It contains both `charts/config`
and `manifests/nginx`; the initial Application placeholders are not deployable.
Replace `REPLACE_WITH_CONFIG_COMMIT_SHA` in `install.yaml` and
`REPLACE_WITH_COMMIT_SHA` in `argocd/nginx.yaml` with that SHA. Ensure nginx's
`repoURL` is `https://github.com/johngarciasre/aranya.git`. Review, commit and push
these pointer changes on `takehome` before applying either Application. The pointer
commit references the preceding published commit rather than itself. Future chart or
nginx changes similarly require a published content commit and a pointer update;
fixed SHAs do not follow the branch. Set the remote default branch to the submission
branch as appropriate; never commit to `main` without express orders.

Apply the pinned config override and verify that the formerly failing Application
becomes Synced/Healthy:

```bash
"$KUBECTL" apply --server-side --dry-run=server -f "$ROOT/install.yaml"
"$KUBECTL" apply --server-side -f "$ROOT/install.yaml"
"$KUBECTL" -n argocd get applications
"$KUBECTL" -n argocd describe application clusterdos-config
```

Inspect Application conditions and repo-server logs if it does not reconcile;
do not claim the upstream error is resolved until the child is Synced/Healthy.

Deploy nginx next. Create the namespace first: a server dry-run of a multi-document
manifest does not persist its Namespace for the subsequent objects. Argo will
adopt/manage it.

```bash
"$KUBECTL" create namespace hello-aranya --dry-run=client -o yaml | "$KUBECTL" apply -f -
"$KUBECTL" apply --server-side --dry-run=server -f "$ROOT/manifests/nginx/resources.yaml"
"$KUBECTL" apply --server-side --dry-run=server -f "$ROOT/argocd/nginx.yaml"
"$KUBECTL" apply --server-side -f "$ROOT/argocd/nginx.yaml"
"$KUBECTL" -n argocd get application hello-aranya
"$KUBECTL" -n hello-aranya rollout status deployment/hello-aranya --timeout=300s
"$KUBECTL" -n hello-aranya get pods -o wide
"$KUBECTL" -n hello-aranya get service,endpointslices,pdb
curl --fail --show-error --connect-timeout 10 http://REPLACE_WITH_NODE1_PUBLIC_IP:30080/
curl --fail --show-error --connect-timeout 10 http://REPLACE_WITH_NODE2_PUBLIC_IP:30080/
curl --fail --show-error --connect-timeout 10 http://REPLACE_WITH_NODE3_PUBLIC_IP:30080/
```

Run curl from outside the provider's private network. Require HTTP 200 and
`hello aranya` from each intended reachable public address, three Ready pods on
separate nodes, and an Argo Application that is Synced/Healthy. Verify provider
NAT/rules if the public IP is not directly assigned to an interface. Port 30080 is
intentional; if reviewers require port 80, provision provider forwarding/LB from
80 to 30080 and include its exact settings/URL in the final runbook.

ConfigMap file mounts use `subPath`; after changing its HTML or nginx.conf via
Git, also change the Deployment pod-template annotation in the same manifest
commit (e.g. `content-revision`) so a new rollout reloads the mounted files.

## 8. HA acceptance and evidence

Record non-secret results in the final README/submission email: UTC time, node
count/versions, Application status, `kubectl top`, NFD labels, the tested public
URL(s), and the Git commit. Never include raw admin kubeconfig or secrets.

Set up the SSH tunnel in section 4 and run the read-only live checks from the
repository root. The script uses the production inventory's `artifacts/admin.conf`
and `artifacts/kubectl`; the default excludes `clusterdos-config` and nginx.

```bash
.venv/bin/python tests/check_acceptance.py inventory/production/hosts.yaml --server https://127.0.0.1:16443
# Full acceptance, after nginx is deployed:
.venv/bin/python tests/check_acceptance.py inventory/production/hosts.yaml --server https://127.0.0.1:16443 --nginx
```

`--nginx` also checks both Applications and public HTTP. Run the HTTP checks from
outside the provider's private network. These read-only checks do not replace the
disruptive failure tests below; do not infer failover from healthy replicas alone.

With explicit permission for a disruptive test, stop **one** node via the provider
console (not two). Manually select each surviving API endpoint; require `/readyz`
and a successful API write (create/delete a temporary ConfigMap in `default`).
From each surviving node, check its local API using `/etc/kubernetes/admin.conf`
and require its kubelet/Cilium to remain healthy. Inspect the `default/kubernetes`
EndpointSlice, pod API connectivity, Argo recovery and nginx curls through surviving
public addresses. There is no VIP takeover to test in this design.

Restore the node and require it to return Ready before any next test. A cordon/drain
does not simulate losing etcd/apiserver and is not proof of control-plane failover.
Record recovery time and any outage; do not assert zero downtime without measurements.
Separately test local apiserver-only failure with approval: document that its host
clients wait for static-pod recovery rather than falling back to another node.
Skip disruptive tests without permission and state failover is unverified.

## 9. Encrypt admin kubeconfig for all three reviewers

First agree a reachable API path. Copy the generated admin kubeconfig to
`.local/share-admin.kubeconfig`, select a tested public node endpoint using the
instructions in section 4, and list all three endpoints and switching steps in
the handoff. The API SANs must cover each public address. For SSH-only access,
select the localhost tunnel endpoint and supply tunnel instructions and verified
SSH access to all reviewers; do not send that config without those instructions.

Use the email's three **public** key attachments; private keys are never needed.
Authenticate each full primary fingerprint with Christian, Yoofi and Sasi over a
trusted independent channel. A UID/name/email in a key is not proof of identity.
The same ciphertext must contain three recipients, not only the last one.

```bash
umask 077
export GNUPGHOME="$ROOT/.local/gpg"
mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"
export CHRISTIAN_KEY=/absolute/path/to/christian-public-key.asc
export YOOFI_KEY=/absolute/path/to/yoofi-public-key.asc
export SASI_KEY=/absolute/path/to/sasi-public-key.asc
gpg --show-keys --with-fingerprint --with-subkey-fingerprint \
  "$CHRISTIAN_KEY" "$YOOFI_KEY" "$SASI_KEY"
# Stop here until every full fingerprint has been independently authenticated.
gpg --import "$CHRISTIAN_KEY" "$YOOFI_KEY" "$SASI_KEY"
export CHRISTIAN_FPR=REPLACE_WITH_VERIFIED_FULL_FINGERPRINT
export YOOFI_FPR=REPLACE_WITH_VERIFIED_FULL_FINGERPRINT
export SASI_FPR=REPLACE_WITH_VERIFIED_FULL_FINGERPRINT
gpg --list-keys --with-fingerprint --with-subkey-fingerprint \
  "$CHRISTIAN_FPR" "$YOOFI_FPR" "$SASI_FPR"
export SHARE_CONFIG="$ROOT/.local/share-admin.kubeconfig"
# Prepare and test this copy's agreed API endpoint before encryption.
chmod 600 "$SHARE_CONFIG"
gpg --batch --armor --encrypt --trust-model always \
  --recipient "$CHRISTIAN_FPR" --recipient "$YOOFI_FPR" --recipient "$SASI_FPR" \
  --output "$ROOT/.local/aranya-admin.kubeconfig.asc" "$SHARE_CONFIG"
gpg --list-packets "$ROOT/.local/aranya-admin.kubeconfig.asc"
```

`--trust-model always` is appropriate **only after** manually authenticating the
three fingerprints; it is not a shortcut around verification. GPG must select an
unexpired encryption-capable subkey for each recipient. Inspect the three public
key encrypted-session packets and compare their key IDs to the authenticated
keys' encryption subkeys. No recipient private key is present locally, so local
packet inspection does not prove decryption; ask **each** reviewer to decrypt and
confirm `kubectl get nodes` works:

```bash
umask 077
gpg --output aranya-admin.kubeconfig --decrypt aranya-admin.kubeconfig.asc
chmod 600 aranya-admin.kubeconfig
KUBECONFIG="$PWD/aranya-admin.kubeconfig" kubectl get nodes
```

Never run `kubectl config view --raw` in shared logs or attach plaintext kubeconfig.
These credentials are cluster-admin, not read-only. If leaked, treat it as an
incident; replacing a file does not revoke a issued client certificate. Rebuild
this disposable cluster or follow a deliberate CA rotation/revocation strategy.

## 10. Final email checklist

After live validation, reply manually in the original thread with:
- public repository link and exact submission commit;
- public nginx URL and brief verification summary;
- `RUNBOOK.md` attached (replace remaining inputs with authorized non-secret
  values or reference the separately supplied inventory/private data);
- `.local/aranya-admin.kubeconfig.asc` attached, encrypted to all three reviewers;
- API connectivity instructions and any remaining availability/security limitations.

Do not publish the encrypted kubeconfig in Git. Publication to the specified
`takehome` branch is approved; do not send email or change reviewer access without
explicit approval. With placeholders still present and acceptance outstanding,
this is **not yet a completed take-home submission**.

## Operations and troubleshooting

- **Kubespray failure:** check OS/Python/sudo/host-key and private routes first;
  read the failed task, fix the cause, rerun pinned `cluster.yml`. Do not run reset
  to paper over a failed deployment.
- **Pending Argo/nginx pods:** check taints, node memory, spread/anti-affinity and
  `kubectl describe pod`; all three nodes must be schedulable for HA Redis.
- **Argo OutOfSync/Error:** inspect `kubectl -n argocd describe application NAME`
  and repo-server/application-controller logs; verify public repo/ref/path and
  reachable Helm repositories. Avoid manual changes that auto-sync will undo.
- **Metrics unavailable:** check serving CSR approvals, Certificate/Issuer status,
  APIService CA injection and private kubelet/CNI connectivity. Do not turn off TLS
  verification as a first response.
- **Public HTTP fails:** check Ready endpoints, NodePort rules, provider NAT and
  external curl. Keep `externalTrafficPolicy: Cluster` so nodes can forward to
  surviving backends.
- **Backup:** before upgrades, take and verify an etcd snapshot using the
  [etcd recovery guidance](https://etcd.io/docs/v3.6/op-guide/recovery/), with the
  deployed etcd endpoint and TLS credentials, then store an encrypted copy off-node.
  Etcd backups contain Secrets. Schedule and test restores before describing a
  longer-lived cluster as production ready. Kubespray's
  [control-plane recovery playbook](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/operations/recover-control-plane.md)
  is disruptive and is not a substitute for a tested backup.
- **Upgrade:** review supported Kubespray/Kubernetes versions, back up etcd, test
  in disposable infrastructure, then use the pinned upstream upgrade playbook.
  Separate cluster upgrades from clusterdOS/chart upgrades.
- **Rebuild/teardown:** reset destroys cluster state. Prefer fresh provider nodes
  for reproduction. Only after explicit approval, correct inventory verification
  and any needed backup use Kubespray `reset.yml`; never put a destructive reset
  in an automatic bootstrap path. Revoke take-home network/SSH access with the
  owner after evaluation.

### References

- [Kubespray inventory](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/ansible/inventory.md)
- [Kubespray kube-vip](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/ingress/kube-vip.md)
- [Kubespray HA modes](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/operations/ha-mode.md)
- [clusterdOS pinned install](https://gitlab.com/aranya-tech/public/clusterdos/-/blob/v0.4.8/install.yaml)
- [clusterdOS chart values](https://gitlab.com/aranya-tech/public/clusterdos/-/blob/v0.4.8/metadeployment/values.yaml)
- [Argo CD installation](https://argo-cd.readthedocs.io/en/release-3.6/operator-manual/installation/)
