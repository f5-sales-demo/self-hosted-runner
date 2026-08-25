#!/usr/bin/env bash
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG must point to the protected AKS administrator config}"
: "${GHCR_USERNAME:?GHCR_USERNAME is required}"
: "${GHCR_TOKEN:?GHCR_TOKEN must be a read-only package token}"

case "$(stat -c '%a' "$KUBECONFIG")" in
  400|600) ;;
  *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1 ;;
esac
[[ "$GHCR_USERNAME" =~ ^[A-Za-z0-9-]+$ ]]

tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT
chmod 0700 "$tmpdir"
config="$tmpdir/config.json"
manifest="$tmpdir/secret.yaml"
export GHCR_USERNAME GHCR_TOKEN
python3 - "$config" <<'PY'
import base64
import json
import os
import pathlib
import sys

credential = f"{os.environ['GHCR_USERNAME']}:{os.environ['GHCR_TOKEN']}".encode()
payload = {"auths": {"ghcr.io": {"auth": base64.b64encode(credential).decode()}}}
pathlib.Path(sys.argv[1]).write_text(json.dumps(payload), encoding="utf-8")
PY
chmod 0600 "$config"
unset GHCR_TOKEN

for namespace in arc-runners-socketless arc-runners-container-build; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n "$namespace" create secret generic ghcr-pull     --type=kubernetes.io/dockerconfigjson     --from-file=.dockerconfigjson="$config"     --dry-run=client -o yaml >"$manifest"
  chmod 0600 "$manifest"
  kubectl apply -f "$manifest" >/dev/null
  : >"$manifest"
  printf 'updated GHCR pull secret in %s\n' "$namespace"
done
