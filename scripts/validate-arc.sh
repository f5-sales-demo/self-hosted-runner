#!/usr/bin/env bash
set -euo pipefail

(($# > 0)) || { echo "usage: scripts/validate-arc.sh <repository-config> [...]" >&2; exit 2; }
repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
command -v helm >/dev/null
test "$(helm version --short)" = "v3.21.3+g1ad6e68"
python3 scripts/arc-config.py --validate-set "$@" >/dev/null

chart_version=0.14.2
controller_chart_digest=sha256:3081ba15c41f0aa791058dedd2a7406fece24c9aeaa94956c268e5099427a452
scale_set_chart_digest=sha256:579e3a1bdf4032b3c3de3e9b0880a4a6d3c1989a67c06010f680c1cc49524d11
runner_image=ghcr.io/f5-sales-demo/self-hosted-runner@sha256:0000000000000000000000000000000000000000000000000000000000000000
dind_image=docker.io/library/docker@sha256:12e683a161823b2a839aeea999b9d960e6e1f9a97b1679ad6b441982e2d9cf07

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
  if [[ "$actual" != "$expected" ]]; then
    echo "unexpected $chart digest: $actual" >&2
    return 1
  fi
}

pull_chart gha-runner-scale-set-controller "$controller_chart_digest"
pull_chart gha-runner-scale-set "$scale_set_chart_digest"
controller_chart="$tmpdir/gha-runner-scale-set-controller-$chart_version.tgz"
scale_set_chart="$tmpdir/gha-runner-scale-set-$chart_version.tgz"
helm lint "$controller_chart" -f arc/controller-values.yaml >/dev/null
helm template arc "$controller_chart" \
  --namespace arc-systems \
  -f arc/controller-values.yaml \
  --post-renderer scripts/arc-controller-post-renderer.py >"$tmpdir/controller.yaml"
grep -Fq 'ghcr.io/actions/gha-runner-scale-set-controller@sha256:1b4c7f62e971ab259a4b8798e48e2adaad4af747f45990f474ea5feefa03531d' "$tmpdir/controller.yaml"

for config in "$@"; do
  config_json=$(python3 scripts/arc-config.py "$config")
  github_url=$(jq -er .repository <<<"$config_json")
  repository_name=${github_url##*/}
  for profile in socketless container-build; do
    spec=$(jq -cer --arg profile "$profile" '.scale_sets[] | select(.profile == $profile)' <<<"$config_json")
    namespace=$(jq -er .namespace <<<"$spec")
    release=$(jq -er .release <<<"$spec")
    scale_set_name=$(jq -er .runner_scale_set_name <<<"$spec")
    values=$(jq -er .values <<<"$spec")
    min_runners=$(jq -er .min_runners <<<"$spec")
    max_runners=$(jq -er .max_runners <<<"$spec")
    rendered_values="$tmpdir/$repository_name-$profile-values.yaml"
    rendered_manifest="$tmpdir/$repository_name-$profile.yaml"
    sed "s|RUNNER_IMAGE_REQUIRED|$runner_image|g" "$values" >"$rendered_values"
    helm lint "$scale_set_chart" \
      -f "$rendered_values" \
      --set-string githubConfigUrl="$github_url" \
      --set-string runnerScaleSetName="$scale_set_name" \
      --set minRunners="$min_runners" \
      --set maxRunners="$max_runners" >/dev/null
    helm template "$release" "$scale_set_chart" \
      --namespace "$namespace" \
      -f "$rendered_values" \
      --set-string githubConfigUrl="$github_url" \
      --set-string runnerScaleSetName="$scale_set_name" \
      --set minRunners="$min_runners" \
      --set maxRunners="$max_runners" >"$rendered_manifest"
    if grep -Fq RUNNER_IMAGE_REQUIRED "$rendered_manifest"; then
      echo "$config $profile retained the runner image placeholder" >&2
      exit 1
    fi
    grep -Fq "$github_url" "$rendered_manifest"
    grep -Fq "$scale_set_name" "$rendered_manifest"
    grep -Fq "runner-profile: $profile" "$rendered_manifest"
    grep -Fq 'name: ghcr-pull' "$rendered_manifest"
    if [[ "$profile" == socketless ]]; then
      if grep -Fq 'privileged: true' "$rendered_manifest"; then
        echo "$config socketless rendered a privileged container" >&2
        exit 1
      fi
      if grep -Fq 'DOCKER_HOST' "$rendered_manifest"; then
        echo "$config socketless rendered Docker access" >&2
        exit 1
      fi
    else
      grep -Fq "$dind_image" "$rendered_manifest"
      grep -Fq 'privileged: true' "$rendered_manifest"
      grep -Fq 'DOCKER_HOST' "$rendered_manifest"
    fi

    prepull_args=(
      --namespace "$namespace"
      --set-string "profile=$profile"
      --set-string "image=$runner_image"
      --set-string 'imagePullSecrets[0]=ghcr-pull'
    )
    if [[ "$profile" == container-build ]]; then
      prepull_args+=(--set-string "additionalImages[0]=$dind_image")
    fi
    helm lint arc/prepull "${prepull_args[@]}" >/dev/null
    helm template runner-image-cache arc/prepull "${prepull_args[@]}" \
      >"$tmpdir/$repository_name-prepull-$profile.yaml"
    grep -Fq 'name: "ghcr-pull"' "$tmpdir/$repository_name-prepull-$profile.yaml"
  done
done
