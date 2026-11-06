#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if (( $# > 1 )) || { (( $# == 1 )) && [[ $1 != --ask-become-pass ]]; }; then
  printf 'Usage: %s [--ask-become-pass]\n' "$0" >&2
  exit 1
fi
exec "$ROOT/.venv/bin/ansible-playbook" -i localhost, "$ROOT/scripts/preflight.yaml" "$@"
