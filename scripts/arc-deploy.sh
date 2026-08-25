#!/usr/bin/env bash
set -euo pipefail

: "${KUBECONFIG:?KUBECONFIG must point to the protected AKS administrator config}"
: "${GITHUB_CONFIG_URL:?GITHUB_CONFIG_URL is required}"
: "${SOCKETLESS_IMAGE:?SOCKETLESS_IMAGE must be an immutable GHCR reference}"
: "${CONTAINER_BUILD_IMAGE:?CONTAINER_BUILD_IMAGE must be an immutable GHCR reference}"

[[ "$GITHUB_CONFIG_URL" =~ ^https://github\\.com/[^/]+/[^/]+$ ]]
image_pattern='^ghcr\.io/f5-sales-demo/self-hosted-runner@sha256:[0-9a-f]{64}$'
[[ "$SOCKETLESS_IMAGE" =~ $image_pattern ]]
[[ "$CONTAINER_BUILD_IMAGE" =~ $image_pattern ]]
case "$(stat -c '%a' "$KUBECONFIG")" in
  400|600) ;;
  *) echo "KUBECONFIG must have mode 0400 or 0600" >&2; exit 1 ;;
esac

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
chart_version=0.14.2
controller_chart_digest=sha256:3081ba15c41f0aa791058dedd2a7406fece24c9aeaa94956c268e5099427a452
scale_set_chart_digest=sha256:579e3a1bdf4032b3c3de3e9b0880a4a6d3c1989a67c06010f680c1cc49524d11
dind_image=docker.io/library/docker@sha256:12e683a161823b2a839aeea999b9d960e6e1f9a97b1679ad6b441982e2d9cf07

test "$(helm version --short)" = "v3.21.3+g1ad6e68"
kubectl auth can-i '*' '*' --all-namespaces | grep -qx yes
for namespace in arc-runners-socketless arc-runners-container-build; do
  kubectl get secret arc-github-app -n "$namespace" >/dev/null
done

tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT
pull_chart() {
  local chart=$1 expected=$2 output actual
  output=$(helm pull "oci://ghcr.io/actions/actions-runner-controller-charts/$chart"     --version "$chart_version" --destination "$tmpdir" 2>&1)
  actual=$(sed -n 's/^Digest: //p' <<<"$output")
  [[ "$actual" == "$expected" ]]
}
pull_chart gha-runner-scale-set-controller "$controller_chart_digest"
pull_chart gha-runner-scale-set "$scale_set_chart_digest"
controller_chart="$tmpdir/gha-runner-scale-set-controller-$chart_version.tgz"
scale_set_chart="$tmpdir/gha-runner-scale-set-$chart_version.tgz"
sed "s|RUNNER_IMAGE_REQUIRED|$SOCKETLESS_IMAGE|g" arc/socketless-values.yaml >"$tmpdir/socketless-values.yaml"
sed "s|RUNNER_IMAGE_REQUIRED|$CONTAINER_BUILD_IMAGE|g" arc/container-build-values.yaml >"$tmpdir/container-build-values.yaml"

helm upgrade --install arc "$controller_chart" --namespace arc-systems --create-namespace   --values arc/controller-values.yaml --post-renderer scripts/arc-controller-post-renderer.py   --wait --timeout 10m
helm upgrade --install self-hosted-runner-socketless "$scale_set_chart" --namespace arc-runners-socketless --values "$tmpdir/socketless-values.yaml" --set-string githubConfigUrl="$GITHUB_CONFIG_URL" --wait --timeout 10m
helm upgrade --install self-hosted-runner-container-build "$scale_set_chart" --namespace arc-runners-container-build --values "$tmpdir/container-build-values.yaml" --set-string githubConfigUrl="$GITHUB_CONFIG_URL" --wait --timeout 10m
helm upgrade --install runner-image-cache arc/prepull   --namespace arc-runners-socketless --set-string profile=socketless   --set-string image="$SOCKETLESS_IMAGE" --wait --timeout 10m
helm upgrade --install runner-image-cache arc/prepull   --namespace arc-runners-container-build --set-string profile=container-build   --set-string image="$CONTAINER_BUILD_IMAGE"   --set-string 'additionalImages[0]'="$dind_image" --wait --timeout 10m
