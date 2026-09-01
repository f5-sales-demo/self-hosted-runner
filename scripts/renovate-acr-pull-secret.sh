#!/usr/bin/env bash
set -euo pipefail

# Creates a renewable, repository-scoped ACR credential for the two workloads
# that pull the immutable Renovate image. This is a least-privilege fallback
# when the deployment principal cannot grant AcrPull to the AKS kubelet.
: "${KUBECONFIG:?KUBECONFIG must point to the protected AKS administrator config}"

case "$(stat -c '%a' "$KUBECONFIG")" in 400|600) ;; *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1;; esac
for command in az base64 jq kubectl python3; do command -v "$command" >/dev/null; done

registry=${RENOVATE_ACR_REGISTRY:-f5salesdemoarcca}
login_server=${RENOVATE_ACR_LOGIN_SERVER:-f5salesdemoarcca.azurecr.io}
secret_name=${RENOVATE_ACR_PULL_SECRET:-renovate-acr-pull}
scope_map=${RENOVATE_ACR_SCOPE_MAP:-renovate-pull-aks}
token_name=${RENOVATE_ACR_TOKEN_NAME:-renovate-pull-aks}
expiry_days=${RENOVATE_ACR_TOKEN_EXPIRY_DAYS:-30}
[[ "$registry" =~ ^[a-z0-9]+$ && "$login_server" == "$registry.azurecr.io" ]] || { echo "ACR registry configuration is invalid" >&2; exit 2; }
[[ "$secret_name" =~ ^[a-z0-9]([-.a-z0-9]*[a-z0-9])?$ && "$scope_map" =~ ^[A-Za-z0-9-]+$ && "$token_name" =~ ^[A-Za-z0-9-]+$ ]] || { echo "ACR token or Kubernetes secret name is invalid" >&2; exit 2; }
[[ "$expiry_days" =~ ^[1-9][0-9]?$ && "$expiry_days" -le 90 ]] || { echo "RENOVATE_ACR_TOKEN_EXPIRY_DAYS must be 1 through 90" >&2; exit 2; }

expected_actions='["repositories/renovate/content/read"]'
if az acr scope-map show --registry "$registry" --name "$scope_map" >/dev/null 2>&1; then
  actual_actions=$(az acr scope-map show --registry "$registry" --name "$scope_map" --query actions -o json | jq -cS .)
  [[ "$actual_actions" == "$expected_actions" ]] || { echo "existing scope map is not renovate content-read only" >&2; exit 1; }
else
  az acr scope-map create --registry "$registry" --name "$scope_map" \
    --repository renovate content/read --description "Read-only immutable Renovate pulls for AKS" --output none
fi

if az acr token show --registry "$registry" --name "$token_name" >/dev/null 2>&1; then
  actual_scope=$(az acr token show --registry "$registry" --name "$token_name" --query scopeMapId -o tsv)
  [[ "$actual_scope" == */scopeMaps/$scope_map ]] || { echo "existing ACR token has an unexpected scope map" >&2; exit 1; }
  [[ "$(az acr token show --registry "$registry" --name "$token_name" --query status -o tsv)" == enabled ]] || { echo "existing ACR token is not enabled" >&2; exit 1; }
else
  az acr token create --registry "$registry" --name "$token_name" --scope-map "$scope_map" --no-passwords --output none
fi

tmpdir=$(mktemp -d)
cleanup() {
  unset ACR_TOKEN_PASSWORD
  [[ ! -e "$tmpdir/config.json" ]] || /usr/bin/unlink "$tmpdir/config.json"
  /usr/bin/rmdir "$tmpdir"
}
trap cleanup EXIT
chmod 0700 "$tmpdir"
ACR_TOKEN_PASSWORD=$(az acr token credential generate --registry "$registry" --name "$token_name" --password1 --expiration-in-days "$expiry_days" -o json | jq -er '.passwords[] | select(.name == "password1") | .value')
export ACR_TOKEN_PASSWORD login_server token_name
python3 - "$tmpdir/config.json" <<'PY'
import base64
import json
import os
import pathlib
import sys

credential = f"{os.environ['token_name']}:{os.environ['ACR_TOKEN_PASSWORD']}".encode()
payload = {"auths": {os.environ['login_server']: {"auth": base64.b64encode(credential).decode()}}}
pathlib.Path(sys.argv[1]).write_text(json.dumps(payload), encoding="utf-8")
PY
chmod 0600 "$tmpdir/config.json"
unset ACR_TOKEN_PASSWORD

for namespace in renovate-system arc-runner-cache; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n "$namespace" create secret generic "$secret_name" --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="$tmpdir/config.json" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl get secret "$secret_name" -n "$namespace" -o json |
    jq -e '.type == "kubernetes.io/dockerconfigjson" and (.data | keys == [".dockerconfigjson"])' >/dev/null
done
printf 'updated repository-scoped Renovate ACR pull secret in renovate-system and arc-runner-cache\n'
