#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
umask 077
ENV_FILE=${1:-}
OUTPUT_DIR=${2:-"$ROOT/inventory/production"}

if [[ -n "$ENV_FILE" && ! -f "$ENV_FILE" ]]; then
  printf 'Missing input file: %s\n' "$ENV_FILE" >&2
  exit 1
fi
if [[ -e "$OUTPUT_DIR" || -L "$OUTPUT_DIR" ]]; then
  printf 'Refusing to overwrite existing inventory: %s\n' "$OUTPUT_DIR" >&2
  exit 1
fi
command -v envsubst >/dev/null || { printf 'envsubst is required (gettext-base on Ubuntu).\n' >&2; exit 1; }
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
if [[ ! -x "$PYTHON" ]]; then
  printf 'Python with PyYAML is required; expected %s.\n' "$PYTHON" >&2
  exit 1
fi

if [[ -n "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi
export KUBE_PODS_SUBNET=${KUBE_PODS_SUBNET:-10.233.64.0/18}
export KUBE_SERVICE_ADDRESSES=${KUBE_SERVICE_ADDRESSES:-10.233.0.0/18}

required=(
  NODE1_PUBLIC_IP NODE2_PUBLIC_IP NODE3_PUBLIC_IP
  NODE1_PRIVATE_IP NODE2_PRIVATE_IP NODE3_PRIVATE_IP
  ANSIBLE_USER SSH_KEY FIREWALL_ADMIN_SOURCES
  FIREWALL_PRIVATE_INTERFACE FIREWALL_PUBLIC_INTERFACE
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    printf 'Missing required setting: %s\n' "$name" >&2
    exit 1
  fi
done
if [[ "$SSH_KEY" != /* || ! -f "$SSH_KEY" || ! -r "$SSH_KEY" ]]; then
  printf 'SSH_KEY must be an absolute path to a readable private-key file: %s\n' "$SSH_KEY" >&2
  exit 1
fi
if [[ "$SSH_KEY" == *'{{'* || "$SSH_KEY" == *'{%'* || "$SSH_KEY" == *'${'* ]]; then
  printf 'SSH_KEY must not contain template expressions.\n' >&2
  exit 1
fi
if [[ ! "$ANSIBLE_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*\$?$ ]]; then
  printf 'Invalid ANSIBLE_USER.\n' >&2
  exit 1
fi
for name in FIREWALL_PRIVATE_INTERFACE FIREWALL_PUBLIC_INTERFACE; do
  if [[ ! "${!name}" =~ ^[a-zA-Z0-9_.:-]+$ ]]; then
    printf 'Invalid interface name in %s.\n' "$name" >&2
    exit 1
  fi
done
if [[ "$FIREWALL_PRIVATE_INTERFACE" == "$FIREWALL_PUBLIC_INTERFACE" ]]; then
  printf 'Private and public interface names must differ.\n' >&2
  exit 1
fi
export "${required[@]}"

export FIREWALL_ADMIN_SOURCES_YAML
FIREWALL_ADMIN_SOURCES_YAML=$("$PYTHON" - <<'PY'
import ipaddress
import json
import os

names = [f"NODE{i}_{kind}_IP" for i in range(1, 4) for kind in ("PUBLIC", "PRIVATE")]
addresses = {name: ipaddress.ip_address(os.environ[name]) for name in names}
if any(address.version != 4 for address in addresses.values()):
    raise SystemExit("Node public and private addresses must be IPv4.")
public = [addresses[f"NODE{i}_PUBLIC_IP"] for i in range(1, 4)]
private = [addresses[f"NODE{i}_PRIVATE_IP"] for i in range(1, 4)]
rfc1918 = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
if len(set(public)) != 3 or len(set(private)) != 3 or set(public) & set(private):
    raise SystemExit("Node public/private addresses must be unique and non-overlapping.")
if any(not any(address in network for network in rfc1918) for address in private):
    raise SystemExit("Each node private address must be in an RFC 1918 range.")
pods = ipaddress.ip_network(os.environ["KUBE_PODS_SUBNET"])
services = ipaddress.ip_network(os.environ["KUBE_SERVICE_ADDRESSES"])
if pods.version != 4 or services.version != 4 or pods.overlaps(services):
    raise SystemExit("Pod and service subnets must be non-overlapping IPv4 networks.")
if any(address in pods or address in services for address in private + public):
    raise SystemExit("Node addresses must not overlap pod or service subnets.")
admin_sources = [value.strip() for value in os.environ["FIREWALL_ADMIN_SOURCES"].split(",")]
if not admin_sources or any(not value for value in admin_sources):
    raise SystemExit("FIREWALL_ADMIN_SOURCES must be a comma-separated IPv4 list.")
try:
    parsed_sources = [ipaddress.ip_address(value) for value in admin_sources]
except ValueError as error:
    raise SystemExit(f"Invalid firewall admin source: {error}")
if any(address.version != 4 for address in parsed_sources):
    raise SystemExit("Firewall admin sources must be IPv4.")
print(json.dumps([str(address) for address in parsed_sources]))
PY
)
export SSH_KEY_YAML
SSH_KEY_YAML=$("$PYTHON" -c 'import json, os; print(json.dumps(os.environ["SSH_KEY"]))')

parent=$(dirname -- "$OUTPUT_DIR")
mkdir -p "$parent"
work=$(mktemp -d "${TMPDIR:-/tmp}/aranya-inventory.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/group_vars"
envsubst '${NODE1_PUBLIC_IP} ${NODE2_PUBLIC_IP} ${NODE3_PUBLIC_IP} ${NODE1_PRIVATE_IP} ${NODE2_PRIVATE_IP} ${NODE3_PRIVATE_IP}' \
  < "$ROOT/inventory/templates/hosts.yaml.tmpl" > "$work/hosts.yaml"
envsubst '${ANSIBLE_USER} ${SSH_KEY_YAML} ${FIREWALL_PRIVATE_INTERFACE} ${FIREWALL_PUBLIC_INTERFACE} ${FIREWALL_ADMIN_SOURCES_YAML}' \
  < "$ROOT/inventory/templates/group_vars/all.yaml.tmpl" > "$work/group_vars/all.yaml"
envsubst '${NODE1_PUBLIC_IP} ${NODE2_PUBLIC_IP} ${NODE3_PUBLIC_IP} ${KUBE_PODS_SUBNET} ${KUBE_SERVICE_ADDRESSES}' \
  < "$ROOT/inventory/templates/group_vars/k8s_cluster.yaml.tmpl" > "$work/group_vars/k8s_cluster.yaml"
chmod 700 "$work"
chmod 600 "$work/hosts.yaml" "$work/group_vars/all.yaml" "$work/group_vars/k8s_cluster.yaml"

"$PYTHON" - "$work" <<'PY'
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1])
for path in root.rglob("*.yaml"):
    text = path.read_text()
    if "${" in text:
        raise SystemExit(f"Unresolved template variable in {path}")
    yaml.safe_load(text)
PY

if ! mkdir -- "$OUTPUT_DIR"; then
  printf 'Refusing to overwrite existing inventory: %s\n' "$OUTPUT_DIR" >&2
  exit 1
fi
cp -R -- "$work/." "$OUTPUT_DIR/"
printf 'Rendered private production inventory: %s\n' "$OUTPUT_DIR"
