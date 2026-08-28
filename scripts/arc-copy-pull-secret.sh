#!/usr/bin/env bash
set -euo pipefail

source_namespace=${1:-}
shift || true
(($# > 0)) || {
  echo "usage: $0 <source-namespace> <repository-config> [...]" >&2
  exit 2
}
: "${KUBECONFIG:?KUBECONFIG must point to the protected AKS administrator config}"
case "$(stat -c '%a' "$KUBECONFIG")" in 400|600) ;; *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1;; esac
[[ "$source_namespace" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || exit 2
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT
chmod 0700 "$tmpdir"
manifest_dir="$tmpdir/manifests"
mkdir -m 0700 "$manifest_dir"
source_secret="$tmpdir/source.json"
kubectl get secret ghcr-pull -n "$source_namespace" -o json >"$source_secret"
chmod 0600 "$source_secret"
acr_expiry=$(python3 - "$source_secret" <<'PY'
import base64, datetime, json, pathlib, sys
secret = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if secret.get("type") != "kubernetes.io/dockerconfigjson":
    raise SystemExit("pull secret has the wrong type")
encoded = secret.get("data", {}).get(".dockerconfigjson")
config = json.loads(base64.b64decode(encoded, validate=True))
required = {"ghcr.io", "f5salesdemoarcca.azurecr.io"}
if not required.issubset(config.get("auths", {})):
    raise SystemExit("pull secret is not the approved combined GHCR/ACR credential")
expiry = secret.get("metadata", {}).get("annotations", {}).get("f5.sales-demo/acr-expires-at")
if not expiry:
    raise SystemExit("ACR token expiry annotation is required")
instant = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
if instant - datetime.datetime.now(datetime.timezone.utc) < datetime.timedelta(hours=24):
    raise SystemExit("ACR token expires within 24 hours; rotate it before deployment")
print(expiry)
PY
)

targets="$tmpdir/targets"
: >"$targets"
for config in "$@"; do
  python3 scripts/arc-config.py "$config" | jq -r '.scale_sets[].namespace' >>"$targets"
done
sort -u -o "$targets" "$targets"
while IFS= read -r namespace; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  jq --arg namespace "$namespace" --arg expiry "$acr_expiry" '
    .metadata = {
      name:"ghcr-pull",
      namespace:$namespace,
      annotations:{"f5.sales-demo/acr-expires-at":$expiry}
    }
  ' "$source_secret" >"$manifest_dir/$namespace.json"
  chmod 0600 "$manifest_dir/$namespace.json"
done <"$targets"
kubectl apply -f "$manifest_dir" >/dev/null
while IFS= read -r namespace; do
  kubectl get secret ghcr-pull -n "$namespace" -o jsonpath='{.metadata.namespace}{"/"}{.metadata.name}{"\n"}'
done <"$targets"
