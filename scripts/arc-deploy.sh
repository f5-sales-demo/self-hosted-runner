#!/usr/bin/env bash
set -euo pipefail

config=${1:-}
mode=${2:-all}
case "$mode" in
  controller|runners|all) ;;
  *) echo "usage: scripts/arc-deploy.sh <repository-config> [controller|runners|all]" >&2; exit 2 ;;
esac
[[ -n "$config" ]] || { echo "repository configuration is required" >&2; exit 2; }

: "${KUBECONFIG:?KUBECONFIG must point to the protected AKS administrator config}"
case "$(stat -c '%a' "$KUBECONFIG")" in
  400|600) ;;
  *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1 ;;
esac

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
config_json=$(python3 scripts/arc-config.py "$config")
github_config_url=$(jq -er .repository <<<"$config_json")
chart_version=0.14.2
controller_chart_digest=sha256:3081ba15c41f0aa791058dedd2a7406fece24c9aeaa94956c268e5099427a452
scale_set_chart_digest=sha256:579e3a1bdf4032b3c3de3e9b0880a4a6d3c1989a67c06010f680c1cc49524d11
dind_image=docker.io/library/docker@sha256:12e683a161823b2a839aeea999b9d960e6e1f9a97b1679ad6b441982e2d9cf07

test "$(helm version --short)" = "v3.21.3+g1ad6e68"
kubectl auth can-i '*' '*' --all-namespaces | grep -qx yes

tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT
export HELM_REGISTRY_CONFIG="$tmpdir/helm-registry.json"
printf '{}\n' >"$HELM_REGISTRY_CONFIG"
chmod 0600 "$HELM_REGISTRY_CONFIG"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  gh auth token | helm registry login ghcr.io \
    --username "$(gh api user --jq .login)" --password-stdin >/dev/null
fi
pull_chart() {
  local chart=$1 expected=$2 output actual
  output=$(helm pull "oci://ghcr.io/actions/actions-runner-controller-charts/$chart" \
    --version "$chart_version" --destination "$tmpdir" 2>&1)
  actual=$(sed -n 's/^Digest: //p' <<<"$output")
  [[ "$actual" == "$expected" ]]
}

if [[ "$mode" == controller || "$mode" == all ]]; then
  pull_chart gha-runner-scale-set-controller "$controller_chart_digest"
  controller_chart="$tmpdir/gha-runner-scale-set-controller-$chart_version.tgz"
  helm upgrade --install arc "$controller_chart" \
    --namespace arc-systems --create-namespace \
    --values arc/controller-values.yaml \
    --post-renderer scripts/arc-controller-post-renderer.py \
    --wait --timeout 10m
fi

if [[ "$mode" == runners || "$mode" == all ]]; then
  : "${SOCKETLESS_IMAGE:?SOCKETLESS_IMAGE must be an immutable GHCR reference}"
  : "${CONTAINER_BUILD_IMAGE:?CONTAINER_BUILD_IMAGE must be an immutable GHCR reference}"
  image_pattern='^ghcr\.io/f5-sales-demo/self-hosted-runner@sha256:[0-9a-f]{64}$'
  [[ "$SOCKETLESS_IMAGE" =~ $image_pattern ]]
  [[ "$CONTAINER_BUILD_IMAGE" =~ $image_pattern ]]
  pull_chart gha-runner-scale-set "$scale_set_chart_digest"
  scale_set_chart="$tmpdir/gha-runner-scale-set-$chart_version.tgz"

  for profile in socketless container-build; do
    spec=$(jq -cer --arg profile "$profile" '.scale_sets[] | select(.profile == $profile)' <<<"$config_json")
    namespace=$(jq -er .namespace <<<"$spec")
    release=$(jq -er .release <<<"$spec")
    scale_set_name=$(jq -er .runner_scale_set_name <<<"$spec")
    values=$(jq -er .values <<<"$spec")
    min_runners=$(jq -er .min_runners <<<"$spec")
    max_runners=$(jq -er .max_runners <<<"$spec")
    image=$SOCKETLESS_IMAGE
    [[ "$profile" == socketless ]] || image=$CONTAINER_BUILD_IMAGE

    kubectl get secret arc-github-app -n "$namespace" >/dev/null
    kubectl get secret ghcr-pull -n "$namespace" >/dev/null
    rendered_values="$tmpdir/$profile-values.yaml"
    sed "s|RUNNER_IMAGE_REQUIRED|$image|g" "$values" >"$rendered_values"
    helm upgrade --install "$release" "$scale_set_chart" \
      --namespace "$namespace" \
      --values "$rendered_values" \
      --set-string githubConfigUrl="$github_config_url" \
      --set-string runnerScaleSetName="$scale_set_name" \
      --set minRunners="$min_runners" \
      --set maxRunners="$max_runners" \
      --wait --timeout 10m

    prepull_args=(
      upgrade --install runner-image-cache arc/prepull
      --namespace "$namespace"
      --set-string "profile=$profile"
      --set-string "image=$image"
      --set-string 'imagePullSecrets[0]=ghcr-pull'
      --wait --timeout 10m
    )
    if [[ "$profile" == container-build ]]; then
      prepull_args+=(--set-string "additionalImages[0]=$dind_image")
    fi
    helm "${prepull_args[@]}"
  done
fi
