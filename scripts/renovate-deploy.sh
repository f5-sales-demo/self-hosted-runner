#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 renovate-system/image-lock.json" >&2; exit 2; }
lock=$1
: "${KUBECONFIG:?KUBECONFIG must point to the protected cluster administrator config}"
case "$(stat -c '%a' "$KUBECONFIG")" in 400|600) ;; *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1;; esac
for command in base64 git helm jq kubectl; do command -v "$command" >/dev/null; done
[[ "$(helm version --short)" == v3.21.3+g1ad6e68 ]]
deploy_timeout=15m
: "${SOCKETLESS_IMAGE:?SOCKETLESS_IMAGE is required}"
image=${RENOVATE_DEPLOY_IMAGE:-$(jq -er .derived.acr "$lock")}
source_image=$(jq -er .derived.ghcr "$lock")
receipt=$(jq -er .derived.manifest_receipt "$lock")
commit=$(jq -er .derived.source_commit "$lock")
pattern='^(ghcr\.io/f5-sales-demo|f5salesdemoarcca\.azurecr\.io|[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com)/renovate@(sha256:[0-9a-f]{64})$'
[[ "$image" =~ $pattern ]] || { echo "deployment must name an approved immutable Renovate registry digest" >&2; exit 2; }
digest=${BASH_REMATCH[2]}
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
gh attestation verify "oci://${source_image}" \
  --repo f5-sales-demo/self-hosted-runner \
  --signer-workflow f5-sales-demo/self-hosted-runner/.github/workflows/publish-renovate.yml@refs/heads/main \
  --source-digest "$commit" \
  --deny-self-hosted-runners >/dev/null
[[ "$image" == "$source_image" ]] || scripts/mirror-runner-image.sh verify "$source_image" "$image" >/dev/null
kubectl auth can-i '*' '*' --all-namespaces | grep -qx yes
kubectl get secret renovate-github-app -n renovate-system -o json | jq -e '.data | keys == ["private-key.pem"]' >/dev/null
app_id=$(jq -er .github_app.app_id "$lock")
installation_id=$(jq -er .github_app.installation_id "$lock")
bot_id=$(jq -er .github_app.bot_id "$lock")
bot_login=$(jq -er .github_app.bot_login "$lock")
renovate_args=()
if [[ "$image" == ghcr.io/* ]]; then
  kubectl get secret ghcr-pull -n renovate-system >/dev/null
  renovate_args+=(--set-string 'imagePullSecrets[0]=ghcr-pull')
fi
helm upgrade --install renovate renovate-system --namespace renovate-system --create-namespace \
  --set-string image="$image" --set-string githubApp.appId="$app_id" \
  --set-string githubApp.installationId="$installation_id" --set-string githubApp.botId="$bot_id" \
  --set-string githubApp.botLogin="$bot_login" "${renovate_args[@]}" --wait --timeout "$deploy_timeout"
prepull_args=()
if [[ "$image" == ghcr.io/* || "$SOCKETLESS_IMAGE" == ghcr.io/* ]]; then
  kubectl get secret ghcr-pull -n arc-runner-cache >/dev/null
  prepull_args+=(--set-string 'imagePullSecrets[0]=ghcr-pull')
fi
helm upgrade --install runner-image-cache-socketless arc/prepull --namespace arc-runner-cache \
  --set-string profile=socketless --set-string image="$SOCKETLESS_IMAGE" \
  --set-string nodeProfiles[0]=socketless --set-string nodeProfiles[1]=compute \
  --set-string renovateImage="$image" "${prepull_args[@]}" --wait --timeout "$deploy_timeout"
kubectl rollout status daemonset/runner-image-prepull-socketless -n arc-runner-cache --timeout="$deploy_timeout"
kubectl get cronjob renovate -n renovate-system -o json | jq -e --arg image "$image" '
  .spec.suspend == false and
  .spec.jobTemplate.spec.template.spec.containers[0].image == $image and
  .spec.jobTemplate.spec.template.spec.containers[0].resources.requests.memory == "6Gi" and
  .spec.jobTemplate.spec.template.spec.containers[0].resources.limits.memory == "12Gi" and
  (.spec.jobTemplate.spec.template.spec.volumes[] |
    select(.name == "cache") | .emptyDir.medium == "Memory" and .emptyDir.sizeLimit == "2Gi") and
  (.spec.jobTemplate.spec.template.spec.volumes[] |
    select(.name == "tmp") | .emptyDir.medium == "Memory" and .emptyDir.sizeLimit == "4Gi") and
  (.spec.jobTemplate.spec.template.spec.volumes[] |
    select(.name == "containerbase") | .emptyDir.medium == "Memory" and .emptyDir.sizeLimit == "2Gi") and
  .spec.jobTemplate.spec.template.spec.initContainers[0].image == $image
' >/dev/null
printf 'deployed active Renovate CronJob and pre-pull at %s\n' "$image"
