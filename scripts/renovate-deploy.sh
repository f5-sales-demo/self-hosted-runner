#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 renovate-system/image-lock.json" >&2; exit 2; }
lock=$1
: "${KUBECONFIG:?KUBECONFIG must point to the protected AKS administrator config}"
case "$(stat -c '%a' "$KUBECONFIG")" in 400|600) ;; *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1;; esac
for command in base64 git helm jq kubectl; do command -v "$command" >/dev/null; done
[[ "$(helm version --short)" == v3.21.3+g1ad6e68 ]]
deploy_timeout=15m
kubectl get secret ghcr-pull -n arc-runner-cache -o json |
  jq -e '.type == "kubernetes.io/dockerconfigjson" and (.data | keys == [".dockerconfigjson"])' >/dev/null || {
    echo "required GHCR pull secret is invalid or missing in arc-runner-cache" >&2
    exit 1
  }
kubectl get secret ghcr-pull -n arc-runner-cache -o jsonpath='{.data.\.dockerconfigjson}' |
  base64 --decode | jq -e '.auths | keys == ["ghcr.io"]' >/dev/null || {
    echo "GHCR pull secret must contain exactly the ghcr.io credential" >&2
    exit 1
  }
image=$(jq -er .derived.acr "$lock")
source_image=$(jq -er .derived.ghcr "$lock")
receipt=$(jq -er .derived.manifest_receipt "$lock")
commit=$(jq -er .derived.source_commit "$lock")
pattern='^f5salesdemoarcca\.azurecr\.io/renovate@(sha256:[0-9a-f]{64})$'
[[ "$image" =~ $pattern ]] || { echo "lock must name the promoted immutable Renovate ACR digest" >&2; exit 2; }
digest=${BASH_REMATCH[1]}
[[ "$source_image" == "ghcr.io/f5-sales-demo/renovate@$digest" && "$receipt" == "$digest" && "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo "Renovate image receipt is inconsistent" >&2; exit 2; }
[[ "$(jq -er .upstream renovate-system/image-source.json | jq -cS .)" == "$(jq -er .upstream "$lock" | jq -cS .)" ]] || { echo "upstream source receipt differs from lock" >&2; exit 2; }
runtime_inputs=(
  renovate-system/Dockerfile
  renovate-system/app-token-init.mjs
  renovate-system/github-app.mjs
  renovate-system/token-entrypoint.mjs
  renovate-system/image-source.json
)
git cat-file -e "$commit^{commit}" 2>/dev/null || { echo "Renovate image source commit is unavailable" >&2; exit 2; }
git diff --quiet "$commit" -- "${runtime_inputs[@]}" || {
  echo "Renovate image lock is stale for the current runtime inputs" >&2
  exit 2
}
verification_receipt=$(mktemp)
trap 'rm -f -- "$verification_receipt"' EXIT
scripts/promote-renovate-image.sh "$source_image" "$commit" "$verification_receipt" >/dev/null
kubectl auth can-i '*' '*' --all-namespaces | grep -qx yes
kubectl get secret renovate-github-app -n renovate-system -o json | jq -e '.data | keys == ["private-key.pem"]' >/dev/null
app_id=$(jq -er .github_app.app_id "$lock")
installation_id=$(jq -er .github_app.installation_id "$lock")
bot_id=$(jq -er .github_app.bot_id "$lock")
bot_login=$(jq -er .github_app.bot_login "$lock")
helm upgrade --install renovate renovate-system --namespace renovate-system --create-namespace \
  --set-string image="$image" --set-string githubApp.appId="$app_id" \
  --set-string githubApp.installationId="$installation_id" --set-string githubApp.botId="$bot_id" \
  --set-string githubApp.botLogin="$bot_login" --wait --timeout "$deploy_timeout"
helm upgrade --install runner-image-cache-socketless arc/prepull --namespace arc-runner-cache \
  --set-string profile=socketless --set-string image="${SOCKETLESS_IMAGE:?SOCKETLESS_IMAGE is required}" \
  --set-string nodeProfiles[0]=socketless --set-string nodeProfiles[1]=compute \
  --set-string renovateImage="$image" --set-string 'imagePullSecrets[0]=ghcr-pull' --wait --timeout "$deploy_timeout"
kubectl rollout status daemonset/runner-image-prepull-socketless -n arc-runner-cache --timeout="$deploy_timeout"
kubectl get cronjob renovate -n renovate-system -o json | jq -e --arg image "$image" '
  .spec.suspend == true and
  .spec.jobTemplate.spec.template.spec.containers[0].image == $image and
  .spec.jobTemplate.spec.template.spec.containers[0].resources.requests.memory == "4Gi" and
  .spec.jobTemplate.spec.template.spec.containers[0].resources.limits.memory == "8Gi" and
  .spec.jobTemplate.spec.template.spec.initContainers[0].image == $image
' >/dev/null
printf 'deployed suspended Renovate CronJob and pre-pull at %s\n' "$image"
