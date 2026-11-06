# Aranya: build and operate the cluster

Follow the numbered steps in order from a Bash shell on the operator workstation.
Steps 1–4 prepare local files and verify the servers; step 5 is the first step
that changes server configuration. Do not configure networking, SSH or firewall
rules manually while preparing the inputs. Ansible applies those settings.

For context, see the [scope](README.md#scope),
[architecture](README.md#architecture-and-availability),
[known limits](README.md#known-limits) and [status](README.md#status).

## 1. Confirm permission and target servers

1. Obtain approval to build on the three assigned servers.
2. Confirm they are fresh, disposable nodes with no existing cluster or valuable
   workloads. Stop if that is not true; do not reset or repurpose them.
3. Obtain their public SSH addresses, SSH username/key, interface names and
   independently verified SSH host-key fingerprints from the assignment owner.
   Do not worry about private IPs; preflight discovers them. Keep inputs private.

**Continue only when the target nodes and authorization are confirmed.**

## 2. Set up the workstation

On the Linux workstation, check that Python 3.12 with venv/pip, Git, curl, tar,
sha256sum, OpenSSH, OpenSSL, GnuPG and `envsubst` are available. Install missing
prerequisites locally before continuing. On Ubuntu, after reviewing the impact:
`sudo apt-get install python3-venv python3-pip git curl openssh-client openssl gnupg gettext-base`.
Install Podman only if using the optional container smoke test below.

Use the [pinned versions](README.md#versions). Do not use upstream `master`,
`main`, `stable`, `latest` or unpinned chart upgrades for dependencies during
reproduction. Verify the nginx digest for your architecture before using ARM nodes.

Clone the submission's `main` branch at its current head:

```bash
git clone --branch main https://github.com/johngarciasre/aranya.git
cd aranya
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

Run directory/clone creation commands only on a fresh checkout. On a repeat run,
reuse and verify existing clones rather than overwriting them. For local rendering
checks on linux/amd64, download and verify Helm:

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
"$ROOT/.venv/bin/python" "$ROOT/tests/check_inventory_template.py"
"$ROOT/.venv/bin/python" "$ROOT/tests/check_preflight.py"
"$ROOT/.venv/bin/python" "$ROOT/tests/check_firewall.py"
"$ROOT/.venv/bin/python" -m pip check
# Optional: pulls nginx and runs/removes only a test container on this workstation.
"$ROOT/.venv/bin/python" "$ROOT/tests/check.py" --container
```

Compare the results with [validation coverage](README.md#validation-coverage).
Do not treat local syntax/rendering checks as proof of deployment.

**Continue only when dependency verification and the local checks pass.**

## 3. Supply environment-specific inventory inputs

Provide **8 required values** as exported environment variables or in a private
Bash file. Do not worry about private IPs: preflight discovers them in step 4.
The steps below use a file. Review [inventory rendering](README.md#inventory-rendering)
for validation rules.

If production inventory already exists, stop before rendering. Preserve any
needed artifacts before deliberately replacing generated inventory; never delete
or overwrite it just to get past the renderer's check.

1. Copy the sample only if a private input file does not already exist:

   ```bash
   test ! -e "$ROOT/.local/production.env" && cp "$ROOT/inventory/production.env.example" "$ROOT/.local/production.env"
   chmod 600 "$ROOT/.local/production.env"
   ```

2. Edit `.local/production.env`. Replace every sample address and fill in these
   values from the assigned servers; this changes a local file, not the nodes:

   | Variable(s) | Required input |
   | --- | --- |
   | `NODE1_PUBLIC_IP`, `NODE2_PUBLIC_IP`, `NODE3_PUBLIC_IP` | Public SSH addresses and API certificate SANs |
   | `ANSIBLE_USER`, `SSH_KEY` | SSH login and readable private key path |
   | `FIREWALL_ADMIN_SOURCES` | Comma-separated authorized operator IPv4 addresses; include the current connection source |
   | `FIREWALL_PRIVATE_INTERFACE`, `FIREWALL_PUBLIC_INTERFACE` | Actual private and public node interface names |

   Set `SSH_KEY` to an absolute path (use `$HOME` if needed). Verify the optional
   pod/service CIDRs, defaulting to `10.233.64.0/18` and `10.233.0.0/18`, do not
   overlap node, VPN, corporate or routed networks. Change only the corresponding
   environment values if necessary. Leave the other checked-in deployment
   settings unchanged; see [architecture](README.md#architecture-and-availability)
   and the [config workaround](README.md#clusterdos-config-workaround) for context.

3. Export the inputs from the Bash file you created and trust:

   ```bash
   set -a
   source "$ROOT/.local/production.env"
   set +a
   ```

   Alternatively, export all eight values directly; no input file is required.
   Do not set `NODE1_PRIVATE_IP`, `NODE2_PRIVATE_IP` or `NODE3_PRIVATE_IP`.

**Continue only when the inputs are exported.**

## 4. Verify access and deployment prerequisites

1. Authenticate SSH host-key fingerprints with the assignment owner and add only
   verified keys to your workstation's `known_hosts`. Do not rely on `ssh-keyscan`
   alone. If using a separate verified file, export `ANSIBLE_SSH_COMMON_ARGS` with
   its `UserKnownHostsFile` option; do not disable host-key checking.
2. Keep the eight inputs from step 3 exported and run the Ansible preflight:

   ```bash
   "$ROOT/.venv/bin/ansible-playbook" -i localhost, "$ROOT/scripts/preflight.yaml"
   ```

   Alternatively, run `bash "$ROOT/scripts/preflight.sh"` for the same playbook.
   Do not use check mode, task skipping or a partial node limit for preflight.
   For password-based sudo, add `--ask-become-pass` in an interactive terminal;
   never put the password in inventory, shell history or Git. Stop on failure;
   do not source a discovery file left over from an earlier run.
3. After preflight succeeds on all three nodes, export the discovered addresses,
   then render and validate the production inventory:

   ```bash
   set -a
   source "$ROOT/.local/discovered.env"
   set +a
   bash "$ROOT/scripts/render_inventory.sh"
   export INVENTORY="$ROOT/inventory/production/hosts.yaml"
   "$ROOT/.venv/bin/python" "$ROOT/tests/check_private_inventory.py" "$INVENTORY"
   "$ROOT/.venv/bin/python" "$ROOT/tests/check_firewall.py" "$INVENTORY"
   ```

   In a new shell, re-export the step 3 inputs, source `discovered.env` with
   `set -a`, then export `INVENTORY` as above. If the private interface has
   multiple global IPv4 addresses, stop and resolve the ambiguity with the
   provider rather than selecting an address yourself. See
   [preflight coverage](README.md#validation-coverage) for checks and limits.

Before continuing, manually confirm package-repository access, no overlap with
VPN/corporate/other routed networks and provider firewall support for the required
private TCP/UDP paths. Pings and HTTPS probes do not prove those conditions. Have
the provider resolve blocking policy; do not modify nodes to fit sample inputs.

**Stop on any failed check.** Proceed to step 5 only when the automated and manual
checks are complete. Do not disable firewalls, open 6443 globally or bypass
host-key checks. See [firewall and SSH design](README.md#firewall-and-ssh-design)
for rollback behavior.

## 5. Apply the firewall and provision Kubernetes

**This changes all selected servers**, installing packages, replacing networking,
configuring Kubernetes/etcd and disabling swap. Obtain authorization and verify
the inventory before running. Do not treat syntax-checking or Kubespray `--check`
as a deployment test or a safe dry-run of infrastructure changes.

Export the deployment connection settings for this shell. Keep any verified
`UserKnownHostsFile` option in `ANSIBLE_SSH_COMMON_ARGS`:

```bash
export ANSIBLE_CONFIG="$ROOT/.cache/kubespray/ansible.cfg"
export ANSIBLE_HOST_KEY_CHECKING=True
export ANSIBLE_SSH_ARGS='-o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=yes'
export ANSIBLE_CACHE_PLUGIN_CONNECTION="$ROOT/.local/facts"
```

First syntax-check the wrapper from the Kubespray checkout:

```bash
(cd "$ROOT/.cache/kubespray" && "$ROOT/.venv/bin/ansible-playbook" \
  -i "$INVENTORY" "$ROOT/deploy.yaml" --syntax-check)
```

Only after the syntax check succeeds, run the deployment. The wrapper applies
the firewall first, then provisions Kubernetes:

```bash
(cd "$ROOT/.cache/kubespray" && "$ROOT/.venv/bin/ansible-playbook" \
  -i "$INVENTORY" "$ROOT/deploy.yaml" --private-key "$SSH_KEY" --become)
```

### Default firewall rules applied by Ansible

**These are the default rules that the command above will set. Do not add them
manually.** Ansible derives source addresses and interface names from the rendered
inventory. Use this table to verify the result, not as a separate setup procedure.

| Path | Allow | Exposure | Default restriction |
| --- | --- | --- | --- |
| Operator -> node SSH | TCP 22 | Restricted | Sources in `FIREWALL_ADMIN_SOURCES`; authorized SSH key required |
| Internet -> node SSH | TCP 2026 | Public | Authorized key required; password/keyboard-interactive authentication disabled |
| Internet -> nginx | TCP 30080 | Public | NodePort forwarding only to pod TCP 8080 |
| Private nodes -> API | TCP 6443 | Private | Inventory node IPs on the private interface; public API access blocked |
| Private nodes -> etcd | TCP 2379, 2380 | Private | Inventory node IPs on the private interface |
| Private nodes -> kubelet/Cilium health | TCP 10250, 4240 | Private | Inventory node IPs on the private interface |
| Private nodes -> Cilium overlay | UDP 8472 | Private | Inventory node IPs on the private interface |
| Pods -> node DNS/API/kubelet/Cilium health | TCP 53, 6443, 10250, 4240; UDP 53 | Private | Pod CIDR arriving on a non-public interface |
| Node-local traffic | All | Private | Loopback interface |
| ICMP/IPv6 neighbor discovery | ICMP, IPv6 ICMP | Public | Allowed for network diagnostics and discovery |
| Replies to existing connections | Established/related traffic | Restricted | Allowed by connection tracking |

Verify public TCP 6443, etcd and kubelet remain blocked. Do not add reviewer API
or SSH grants without separate approval. Other unsolicited host input and public
forwarding are dropped; outbound traffic and private/pod forwarding remain
allowed. Review [firewall and SSH design](README.md#firewall-and-ssh-design) for
implementation details. Use the live checks in step 9 to verify filtering and
public HTTP after installation.

Do not use `--skip-tags`, `--start-at-task` or a partial `--limit` to bypass the
firewall. Require a successful recap for all three nodes. On failure, diagnose
the first failed task, correct its cause and rerun the same pinned playbook.
Do not erase etcd or bypass checks. Keep logs private and redact credentials.

Use the generated admin config and matching kubectl; restrict their permissions:

```bash
export KUBECONFIG="$ROOT/inventory/production/artifacts/admin.conf"
export KUBECTL="$ROOT/inventory/production/artifacts/kubectl"
chmod 600 "$KUBECONFIG"
chmod 700 "$KUBECTL"
```

### Workstation API connectivity and manual switching

Create a private operator copy for either direct or SSH-only access:

```bash
cp "$KUBECONFIG" "$ROOT/.local/operator.kubeconfig"
export KUBECONFIG="$ROOT/.local/operator.kubeconfig"
export API_CLUSTER=$("$KUBECTL" config view --minify -o jsonpath='{.contexts[0].context.cluster}')
```

For direct access, only after confirming source-restricted TCP 6443 access:

```bash
export NODE_PUBLIC_IP="$NODE1_PUBLIC_IP"
"$KUBECTL" config set-cluster "$API_CLUSTER" --server="https://$NODE_PUBLIC_IP:6443"
"$KUBECTL" get --raw='/readyz?verbose'
```

If that node is unavailable, set `NODE_PUBLIC_IP` to node2 or node3 and rerun
`config set-cluster` and `/readyz`. Test all three addresses while healthy. Do not
use `insecure-skip-tls-verify` or assume kubeconfig tries alternate endpoints.

For SSH-only access, keep public TCP 6443 closed and use the exported inventory
inputs to select a node-local tunnel:

```bash
"$KUBECTL" config set-cluster "$API_CLUSTER" --server=https://127.0.0.1:16443
ssh -p 2026 -i "$SSH_KEY" \
  -o StrictHostKeyChecking=yes -o HostKeyAlias="$NODE1_PUBLIC_IP" \
  -o ExitOnForwardFailure=yes \
  -N -L 127.0.0.1:16443:127.0.0.1:6443 "$ANSIBLE_USER@$NODE1_PUBLIC_IP"
```

Use only independently verified host keys. Keep the tunnel running in a separate
terminal; restart it through node2/node3 if needed. Use port 22 only from an
allowlisted source. Provision and verify authorized SSH access for each reviewer
before handing off a tunnel-based config. See
[firewall and SSH design](README.md#firewall-and-ssh-design) for listener and
host-key alias details.

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
Investigate unexpected taints before removing them.

## 6. Validate and approve kubelet serving certificates

Inspect each kubelet serving CSR separately. See
[architecture](README.md#architecture-and-availability) for the certificate
approval policy; do not enable blanket automatic approval.

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
- DNS/IP SANs match only that verified node, including its private InternalIP;
- usages are server authentication and required signing/key encipherment, not an
  unrelated identity. Compare against `kubectl get node NODE_NAME -o yaml` and
  the provider's authoritative inventory.

Only after validation:

```bash
"$KUBECTL" certificate approve "$CSR"
"$KUBECTL" get csr "$CSR" -o jsonpath='{.status.certificate}' \
  | base64 --decode | openssl x509 -noout -subject -issuer -dates
```

Repeat for all three serving CSRs. Do not bulk-approve pending requests. For
longer-lived operation, monitor renewal requests and repeat verification before
expiry, or configure a separately reviewed restricted approver policy.

## 7. Install Argo CD and clusterdOS

Use server-side apply for the large Argo CRDs. Install into a fresh namespace;
do not overwrite an existing Argo installation without authorization. Confirm
the pinned config chart commit is accessible before applying `install.yaml`.
For a fork, publish and pin its chart using [section 8](#8-publish-code-and-deploy-nginx).
See the [config workaround](README.md#clusterdos-config-workaround) for provenance
and the override's purpose.

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

Check Pending pods/events rather than silently reducing Redis redundancy. Require
`clusterdos-config`, `clusterdos-certmanager`, `clusterdos-metricsserver` and
`clusterdos-nfd` to become Synced/Healthy; inspect status and logs on failure.
Keep both cluster-name values equal. Remove the config override only after
verifying a later upstream release fixes and renders the chart.

```bash
"$KUBECTL" -n argocd get applications
"$KUBECTL" -n clusterdos-cert-manager get deployments,pods
"$KUBECTL" -n clusterdos-cert-manager rollout status deployment/clusterdos-certmanager-cert-manager --timeout=300s
"$KUBECTL" wait --for=condition=Established --timeout=180s crd/certificates.cert-manager.io
```

Do not assume parent sync waves wait for child Application health. If cert-manager
CRDs are missing, inspect the metrics-server Application and retry its sync once
the cert-manager controller/webhook are Ready. Use Argo's UI or request the sync:

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

Require an APIService CA bundle, TLS verification enabled, working `kubectl top`
and NFD feature labels on each node. On failure inspect Application conditions,
pod logs and events; do not add `--kubelet-insecure-tls` to mask certificate or
network errors. See [architecture](README.md#architecture-and-availability) for
the metrics TLS and CA-injection configuration.

For Argo UI access, use a local-only port-forward:
`"$KUBECTL" -n argocd port-forward svc/argocd-server 8443:443`.
Read the initial password privately, change it on login and remove the initial
password Secret after confirming the change. Do not publish the UI/password.

## 8. Publish code and deploy nginx

Inspect `.gitignore`, scan files for secrets and review the exact file list before
publication. Do not add `.local`, `.cache`, `.venv`, `inventory/production`,
SSH/GPG keys, plaintext/encrypted kubeconfigs or credential attachments. Publish
sanitized real inventory/network values only with authorization; never embed SSH
passwords. See [publication](README.md#publication) for repository and release
status, and content-pinning context.

Run the following initial-publication example only in a separate fresh checkout.
Do not reinitialize an existing repository. Obtain authorization for the target
repository and branch before committing or pushing:

```bash
git init -b takehome
git add .gitignore README.md RUNBOOK.md requirements.lock install.yaml deploy.yaml \
  inventory/example inventory/production.env.example inventory/templates \
  scripts/render_inventory.sh scripts/preflight.sh scripts/preflight.yaml \
  charts/config argocd/nginx.yaml manifests/nginx/resources.yaml \
  firewall.yaml templates tests/check.py tests/check_private_inventory.py \
  tests/check_inventory_template.py tests/check_preflight.py tests/check_firewall.py \
  tests/check_live_firewall.py tests/check_acceptance.py
git diff --cached --check
git diff --cached
# Check the entire staged diff for keys, tokens and confidential data before committing.
git commit -m "Add reproducible Aranya Kubespray take-home"
# Create the approved public repository, then add its SSH remote.
git remote add origin git@github.com:johngarciasre/aranya.git
git push -u origin takehome
git rev-parse HEAD
```

For a fork or new chart/nginx revision:
1. Publish a content commit containing `charts/config` and `manifests/nginx`.
2. Update each Application's repository URL and full content SHA.
3. Review, commit and push the pointer changes before applying either Application.

Apply the pinned config override and require the child Application to become
Synced/Healthy:

```bash
"$KUBECTL" apply --server-side --dry-run=server -f "$ROOT/install.yaml"
"$KUBECTL" apply --server-side -f "$ROOT/install.yaml"
"$KUBECTL" -n argocd get applications
"$KUBECTL" -n argocd describe application clusterdos-config
```

Inspect Application conditions and repo-server logs on failure. Create the nginx
namespace before server dry-runs: a multi-document dry-run does not persist its
Namespace for subsequent objects.

```bash
"$KUBECTL" create namespace hello-aranya --dry-run=client -o yaml | "$KUBECTL" apply -f -
"$KUBECTL" apply --server-side --dry-run=server -f "$ROOT/manifests/nginx/resources.yaml"
"$KUBECTL" apply --server-side --dry-run=server -f "$ROOT/argocd/nginx.yaml"
"$KUBECTL" apply --server-side -f "$ROOT/argocd/nginx.yaml"
"$KUBECTL" -n argocd get application hello-aranya
"$KUBECTL" -n hello-aranya rollout status deployment/hello-aranya --timeout=300s
"$KUBECTL" -n hello-aranya get pods -o wide
"$KUBECTL" -n hello-aranya get service,endpointslices,pdb
for ip in "$NODE1_PUBLIC_IP" "$NODE2_PUBLIC_IP" "$NODE3_PUBLIC_IP"; do
  curl --fail --show-error --connect-timeout 10 "http://$ip:30080/"
done
```

Run curl from outside the provider's private network. Require HTTP 200 and
`hello aranya` from each intended public address, three Ready pods on separate
nodes, and a Synced/Healthy Application. Verify provider NAT/rules if the public
IP is not directly assigned to an interface. If reviewers require port 80,
provision approved provider forwarding/LB from 80 to 30080 and record its exact
settings/URL in the handoff.

After changing ConfigMap-mounted HTML or nginx.conf via Git, also change the
Deployment pod-template annotation (e.g. `content-revision`) in the same content
commit so a new rollout reloads the `subPath` mounts.

## 9. Validate availability and record evidence

Record non-secret results in the README/submission email: UTC time, node
count/versions, Application status, `kubectl top`, NFD labels, tested public
URL(s), and Git commit. Never include raw admin kubeconfig or secrets. Review
[validation coverage](README.md#validation-coverage) and
[known limits](README.md#known-limits); record results under [status](README.md#status).

Set up the [SSH tunnel](#workstation-api-connectivity-and-manual-switching) and
run the read-only live checks from the repository root:

```bash
.venv/bin/python tests/check_acceptance.py inventory/production/hosts.yaml --server https://127.0.0.1:16443
# Full acceptance, after nginx is deployed:
.venv/bin/python tests/check_acceptance.py inventory/production/hosts.yaml --server https://127.0.0.1:16443 --nginx
```

Run HTTP checks from outside the provider's private network. Do not infer
failover from healthy replicas alone.

Only with explicit permission for a disruptive test and a verified provider
console recovery path, stop **one** node (not two). Manually select each surviving
API endpoint; require `/readyz` and a successful API write by creating/deleting a
temporary ConfigMap in `default`. From each surviving node, check its local API
using `/etc/kubernetes/admin.conf` and require kubelet/Cilium to remain healthy.
Inspect the `default/kubernetes` EndpointSlice, pod API connectivity, Argo recovery
and nginx curls through surviving public addresses.

Restore the node and require it to return Ready before any next test. Do not use
cordon/drain as proof of control-plane failover. Record recovery time and any
outage; do not assert zero downtime without measurements. With separate approval,
test local apiserver-only failure and measure host-client recovery. Skip disruptive
tests without permission or a recovery path, and record failover as unverified.
See [status](README.md#status) for the existing test decision.

## 10. Encrypt admin kubeconfig for all three reviewers

First agree a reachable API path. Copy the generated admin kubeconfig to
`.local/share-admin.kubeconfig`, select a tested public node endpoint using
[the API access procedure](#workstation-api-connectivity-and-manual-switching),
and list all three endpoints and switching steps in the handoff. Confirm API SANs
cover each public address. For SSH-only access, select the localhost tunnel
endpoint and supply tunnel instructions and verified SSH access to all reviewers.
Do not send the config without those instructions. Review
[remaining handoff](README.md#remaining-handoff) before proceeding.

Use the email's three **public** key attachments; never request private keys.
Authenticate each full primary fingerprint with Christian, Yoofi and Sasi over a
trusted independent channel. Do not trust identity based on a UID/name/email alone.
Encrypt the same ciphertext to all three recipients:

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

Use `--trust-model always` **only after** manually authenticating all fingerprints.
Verify GPG selects an unexpired encryption-capable subkey for each recipient.
Inspect all three public-key encrypted-session packets and compare their key IDs
to the authenticated encryption subkeys. Do not treat packet inspection as proof
of decryption. Ask **each** reviewer to decrypt and confirm `kubectl get nodes`:

```bash
umask 077
gpg --output aranya-admin.kubeconfig --decrypt aranya-admin.kubeconfig.asc
chmod 600 aranya-admin.kubeconfig
KUBECONFIG="$PWD/aranya-admin.kubeconfig" kubectl get nodes
```

Never run `kubectl config view --raw` in shared logs or attach plaintext kubeconfig.
Treat an admin credential leak as an incident; replacing a file does not revoke
an issued client certificate. Rebuild this disposable cluster or follow a deliberate
CA rotation/revocation strategy. See [known limits](README.md#known-limits) for
shared-admin identity implications.

## 11. Final email checklist

After live validation and approval to send, reply manually in the original thread
with:
- public repository link and exact submission commit;
- public nginx URL and brief verification summary;
- `RUNBOOK.md` attached, alongside the README for linked context; reference
  separately supplied authorized inventory/private inputs;
- `.local/aranya-admin.kubeconfig.asc` attached, encrypted to all three reviewers;
- API connectivity instructions and [remaining limitations](README.md#known-limits).

Do not publish the encrypted kubeconfig in Git. Obtain explicit approval before
sending email or changing reviewer access. Check
[remaining handoff](README.md#remaining-handoff) and [status](README.md#status)
before describing the assignment as complete.

## Operations and troubleshooting

- **Kubespray failure:** check OS/Python/sudo/host-key and private routes first;
  read the failed task, fix the cause, rerun the pinned deployment wrapper. Do not
  run reset to paper over a failed deployment.
- **Pending Argo/nginx pods:** check taints, node memory, spread/anti-affinity and
  `kubectl describe pod`; keep all three nodes schedulable for HA Redis.
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
  Protect backups as secrets; schedule and test restores before describing a
  longer-lived cluster as production ready. Do not use Kubespray's disruptive
  [control-plane recovery playbook](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/operations/recover-control-plane.md)
  as a substitute for a tested backup.
- **Upgrade:** review supported Kubespray/Kubernetes versions, back up etcd, test
  in disposable infrastructure, then use the pinned upstream upgrade playbook.
  Separate cluster upgrades from clusterdOS/chart upgrades.
- **Rebuild/teardown:** prefer fresh provider nodes for reproduction. Use Kubespray
  `reset.yml` only after explicit approval, correct inventory verification and any
  needed backup; never put destructive reset in an automatic bootstrap path.
  Revoke take-home network/SSH access with the owner after evaluation.

### References

- [Kubespray inventory](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/ansible/inventory.md)
- [Kubespray kube-vip](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/ingress/kube-vip.md)
- [Kubespray HA modes](https://github.com/kubernetes-sigs/kubespray/blob/v2.32.0/docs/operations/ha-mode.md)
- [clusterdOS pinned install](https://gitlab.com/aranya-tech/public/clusterdos/-/blob/v0.4.8/install.yaml)
- [clusterdOS chart values](https://gitlab.com/aranya-tech/public/clusterdos/-/blob/v0.4.8/metadeployment/values.yaml)
- [Argo CD installation](https://argo-cd.readthedocs.io/en/release-3.6/operator-manual/installation/)
