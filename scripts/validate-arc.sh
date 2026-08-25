#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
command -v helm >/dev/null
test "$(helm version --short)" = "v3.21.3+g1ad6e68"

chart_version=0.14.2
controller_chart_digest=sha256:3081ba15c41f0aa791058dedd2a7406fece24c9aeaa94956c268e5099427a452
scale_set_chart_digest=sha256:579e3a1bdf4032b3c3de3e9b0880a4a6d3c1989a67c06010f680c1cc49524d11
runner_image=ghcr.io/f5-sales-demo/self-hosted-runner@sha256:0000000000000000000000000000000000000000000000000000000000000000
dind_image=docker.io/library/docker@sha256:12e683a161823b2a839aeea999b9d960e6e1f9a97b1679ad6b441982e2d9cf07
github_url=https://github.com/example/example

tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT

pull_chart() {
  local chart=$1 expected=$2 output actual
  output=$(helm pull "oci://ghcr.io/actions/actions-runner-controller-charts/$chart"     --version "$chart_version" --destination "$tmpdir" 2>&1)
  actual=$(sed -n 's/^Digest: //p' <<<"$output")
  if [[ "$actual" != "$expected" ]]; then
    echo "unexpected $chart digest: $actual" >&2
    return 1
  fi
}

pull_chart gha-runner-scale-set-controller "$controller_chart_digest"
pull_chart gha-runner-scale-set "$scale_set_chart_digest"
controller_chart="$tmpdir/gha-runner-scale-set-controller-$chart_version.tgz"
scale_set_chart="$tmpdir/gha-runner-scale-set-$chart_version.tgz"
sed "s|RUNNER_IMAGE_REQUIRED|$runner_image|g" arc/socketless-values.yaml >"$tmpdir/socketless-values.yaml"
sed "s|RUNNER_IMAGE_REQUIRED|$runner_image|g" arc/container-build-values.yaml >"$tmpdir/container-build-values.yaml"

helm lint "$controller_chart" -f arc/controller-values.yaml >/dev/null
helm template arc "$controller_chart"   --namespace arc-systems   -f arc/controller-values.yaml   --post-renderer scripts/arc-controller-post-renderer.py >"$tmpdir/controller.yaml"
grep -Fq 'ghcr.io/actions/gha-runner-scale-set-controller@sha256:1b4c7f62e971ab259a4b8798e48e2adaad4af747f45990f474ea5feefa03531d' "$tmpdir/controller.yaml"

helm lint "$scale_set_chart" -f "$tmpdir/socketless-values.yaml" --set-string githubConfigUrl="$github_url" >/dev/null
helm template self-hosted-runner-socketless "$scale_set_chart" --namespace arc-runners-socketless -f "$tmpdir/socketless-values.yaml" --set-string githubConfigUrl="$github_url" >"$tmpdir/socketless.yaml"

helm lint "$scale_set_chart" -f "$tmpdir/container-build-values.yaml" --set-string githubConfigUrl="$github_url" >/dev/null
helm template self-hosted-runner-container-build "$scale_set_chart" --namespace arc-runners-container-build -f "$tmpdir/container-build-values.yaml" --set-string githubConfigUrl="$github_url" >"$tmpdir/container-build.yaml"

! grep -Fq RUNNER_IMAGE_REQUIRED "$tmpdir/socketless.yaml"
! grep -Fq RUNNER_IMAGE_REQUIRED "$tmpdir/container-build.yaml"
grep -Fq "$dind_image" "$tmpdir/container-build.yaml"
grep -Fq 'runner-profile: socketless' "$tmpdir/socketless.yaml"
grep -Fq 'runner-profile: container-build' "$tmpdir/container-build.yaml"
grep -Fq 'name: ghcr-pull' "$tmpdir/socketless.yaml"
grep -Fq 'name: ghcr-pull' "$tmpdir/container-build.yaml"

helm lint arc/prepull   --set-string profile=socketless   --set-string image="$runner_image"   --set-string 'imagePullSecrets[0]'=ghcr-pull >/dev/null
helm template runner-image-cache arc/prepull   --namespace arc-runners-socketless   --set-string profile=socketless   --set-string image="$runner_image"   --set-string 'imagePullSecrets[0]'=ghcr-pull >"$tmpdir/prepull-socketless.yaml"
helm lint arc/prepull   --set-string profile=container-build   --set-string image="$runner_image"   --set-string 'additionalImages[0]'="$dind_image"   --set-string 'imagePullSecrets[0]'=ghcr-pull >/dev/null
helm template runner-image-cache arc/prepull   --namespace arc-runners-container-build   --set-string profile=container-build   --set-string image="$runner_image"   --set-string 'additionalImages[0]'="$dind_image"   --set-string 'imagePullSecrets[0]'=ghcr-pull >"$tmpdir/prepull-container-build.yaml"
grep -Fq "$dind_image" "$tmpdir/prepull-container-build.yaml"
grep -Fq 'name: "ghcr-pull"' "$tmpdir/prepull-socketless.yaml"
grep -Fq 'name: "ghcr-pull"' "$tmpdir/prepull-container-build.yaml"
