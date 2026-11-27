#!/usr/bin/env bash
set -euo pipefail

config=${1:-}
mode=${2:-all}
case "$mode" in
controller | cache | runners | toolchain-canary | all) ;;
*)
  echo "usage: scripts/arc-deploy.sh <repository-config> [controller|cache|runners|toolchain-canary|all]" >&2
  exit 2
  ;;
esac
[[ -n "$config" ]] || {
  echo "repository configuration is required" >&2
  exit 2
}

: "${KUBECONFIG:?KUBECONFIG must point to the protected cluster administrator config}"
case "$(stat -c '%a' "$KUBECONFIG")" in
400 | 600) ;;
*)
  echo "KUBECONFIG must have mode 0400 or 0600" >&2
  exit 1
  ;;
esac

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
export HELM_PLUGINS="$repo_root/arc/helm-plugins"
candidate_enabled=${TF_VAR_enable_compute_32_vcpu_candidate:-false}
[[ "$candidate_enabled" =~ ^(true|false)$ ]] || {
  echo "TF_VAR_enable_compute_32_vcpu_candidate must be true or false" >&2
  exit 2
}
config_args=(--enabled-only)
[[ "$candidate_enabled" != true ]] || config_args+=(--enable-compute-32-vcpu-candidate)
[[ "$mode" != toolchain-canary ]] || config_args+=(--enable-toolchain-canary)
config_json=$(python3 scripts/arc-config.py "${config_args[@]}" "$config")
if [[ "$mode" == toolchain-canary ]]; then
  config_json=$(jq -ce '.scale_sets |= map(select(.profile == "compute-toolchain-canary")) | select(.scale_sets | length == 1)' <<<"$config_json")
fi
github_config_url=$(jq -er .repository <<<"$config_json")
chart_version=0.15.1
controller_chart_digest=sha256:c2497a6dedaaa723322abfe9ea2f5a81b9b8fbb90b38e45d7835b6c7629a3aae
scale_set_chart_digest=sha256:2ae7e43db61047aa3c4182e73c8f58ace35e796fa07d41b64fb3c39e81e1f365
dind_image=docker.io/library/docker@sha256:12e683a161823b2a839aeea999b9d960e6e1f9a97b1679ad6b441982e2d9cf07

test "$(helm version --short)" = "v4.3.2+g8346aaa"
kubectl auth can-i '*' '*' --all-namespaces | grep -qx yes

tmpdir=$(mktemp -d)
trap 'rm -rf -- "$tmpdir"' EXIT
export HELM_REGISTRY_CONFIG="$tmpdir/helm-registry.json"
printf '{}\n' >"$HELM_REGISTRY_CONFIG"
chmod 0600 "$HELM_REGISTRY_CONFIG"
scripts/arc-registry-context.sh "$HELM_REGISTRY_CONFIG"
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
    --post-renderer arc-controller-image \
    --wait --timeout 10m
fi

image_pattern='^(ghcr\.io/f5-sales-demo|f5salesdemoarcca\.azurecr\.io|[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com)/self-hosted-runner@sha256:[0-9a-f]{64}$'
if [[ "$mode" == toolchain-canary ]]; then
  : "${TOOLCHAIN_CANARY_IMAGE:?Exact published canary image is required}"
  : "${TOOLCHAIN_CANARY_SOURCE_SHA:?Verified merged image source is required}"
  [[ "$TOOLCHAIN_CANARY_IMAGE" =~ ^ghcr\.io/f5-sales-demo/self-hosted-runner@sha256:[0-9a-f]{64}$ ]]
  [[ "$TOOLCHAIN_CANARY_SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]
  scripts/verify-promotion.sh "$TOOLCHAIN_CANARY_IMAGE" "$TOOLCHAIN_CANARY_SOURCE_SHA"
fi

if [[ "$mode" == cache || "$mode" == runners || "$mode" == all ]]; then
  : "${SOCKETLESS_IMAGE:?SOCKETLESS_IMAGE must be an immutable approved registry reference}"
  : "${CONTAINER_BUILD_IMAGE:?CONTAINER_BUILD_IMAGE must be an immutable approved registry reference}"
  : "${SUPER_LINTER_SEED_IMAGE:?SUPER_LINTER_SEED_IMAGE must be an immutable approved registry reference}"
  [[ "$SOCKETLESS_IMAGE" =~ $image_pattern ]]
  [[ "$CONTAINER_BUILD_IMAGE" =~ $image_pattern ]]
  [[ "$SUPER_LINTER_SEED_IMAGE" =~ $image_pattern ]]
  [[ "${SUPER_LINTER_SEED_IMAGE%%/*}" == "${CONTAINER_BUILD_IMAGE%%/*}" ]] || {
    echo "SUPER_LINTER_SEED_IMAGE and CONTAINER_BUILD_IMAGE must use the same registry" >&2
    exit 2
  }
  candidate_required=$(jq -r 'any(.scale_sets[]; (.profile | endswith("-candidate")))' <<<"$config_json")
  if [[ "$candidate_required" == true ]]; then
    : "${COMPUTE_CANDIDATE_IMAGE:?COMPUTE_CANDIDATE_IMAGE must be an immutable candidate reference}"
    [[ "$COMPUTE_CANDIDATE_IMAGE" =~ $image_pattern ]]
  fi
  if [[ "$SOCKETLESS_IMAGE" != ghcr.io/* ]]; then
    : "${SOCKETLESS_SOURCE_IMAGE:?SOCKETLESS_SOURCE_IMAGE must identify the equal GHCR digest}"
    scripts/mirror-runner-image.sh verify "$SOCKETLESS_SOURCE_IMAGE" "$SOCKETLESS_IMAGE" >/dev/null
  fi
  if [[ "$CONTAINER_BUILD_IMAGE" != ghcr.io/* ]]; then
    : "${CONTAINER_BUILD_SOURCE_IMAGE:?CONTAINER_BUILD_SOURCE_IMAGE must identify the equal GHCR digest}"
    scripts/mirror-runner-image.sh verify "$CONTAINER_BUILD_SOURCE_IMAGE" "$CONTAINER_BUILD_IMAGE" >/dev/null
  fi
  if [[ "$SUPER_LINTER_SEED_IMAGE" != ghcr.io/* ]]; then
    : "${SUPER_LINTER_SEED_SOURCE_IMAGE:?SUPER_LINTER_SEED_SOURCE_IMAGE must identify the equal GHCR digest}"
    scripts/mirror-runner-image.sh verify "$SUPER_LINTER_SEED_SOURCE_IMAGE" "$SUPER_LINTER_SEED_IMAGE" >/dev/null
  fi
  if [[ -n "${COMPUTE_CANDIDATE_IMAGE:-}" && "$COMPUTE_CANDIDATE_IMAGE" != ghcr.io/* ]]; then
    : "${COMPUTE_CANDIDATE_SOURCE_IMAGE:?COMPUTE_CANDIDATE_SOURCE_IMAGE must identify the equal GHCR digest}"
    scripts/mirror-runner-image.sh verify "$COMPUTE_CANDIDATE_SOURCE_IMAGE" "$COMPUTE_CANDIDATE_IMAGE" >/dev/null
  fi
fi

if [[ "$mode" == cache || "$mode" == all ]]; then
  cache_namespace=arc-runner-cache
  cache_profiles=(socketless container-build)
  [[ "$candidate_required" != true ]] || cache_profiles+=(compute-candidate)
  for profile in "${cache_profiles[@]}"; do
    image=$SOCKETLESS_IMAGE
    [[ "$profile" != container-build ]] || image=$CONTAINER_BUILD_IMAGE
    [[ "$profile" != compute-candidate ]] || image=${COMPUTE_CANDIDATE_IMAGE:-$SOCKETLESS_IMAGE}
    cache_args=(
      upgrade --install "runner-image-cache-$profile" arc/prepull
      --namespace "$cache_namespace"
      --create-namespace
      --set-string "profile=$profile"
      --set-string "image=$image"
      --set-string "seedImage="
      --set-string "nodeProfiles[0]=$profile"
      --wait --timeout 10m
    )
    if [[ "$profile" == socketless ]]; then
      cache_args+=(--set-string "nodeProfiles[1]=compute")
    elif [[ "$profile" == compute-candidate ]]; then
      cache_args+=(--set-string "nodeProfiles[0]=compute-16-vcpu-candidate")
      cache_args+=(--set-string "nodeProfiles[1]=compute-32-vcpu-density-candidate")
    elif [[ "$profile" == container-build ]]; then
      cache_args+=(--set-string "additionalImages[0]=$dind_image")
      cache_args+=(--set-string "seedImage=$SUPER_LINTER_SEED_IMAGE")
    fi
    if [[ "$image" == ghcr.io/* ]]; then
      kubectl get secret ghcr-pull -n "$cache_namespace" >/dev/null
      cache_args+=(--set-string 'imagePullSecrets[0]=ghcr-pull')
    fi
    helm "${cache_args[@]}"
    kubectl rollout status "daemonset/runner-image-prepull-$profile" -n "$cache_namespace" --timeout=10m
  done
fi

if [[ "$mode" == runners || "$mode" == toolchain-canary || "$mode" == all ]]; then
  if [[ "$mode" != toolchain-canary ]]; then
    kubectl apply -f arc/candidate-priority-class.yaml
    kubectl apply -f arc/compute-16-vcpu-candidate-priority-class.yaml
  fi
  pull_chart gha-runner-scale-set "$scale_set_chart_digest"
  scale_set_chart="$tmpdir/gha-runner-scale-set-$chart_version.tgz"

  while IFS= read -r spec; do
    profile=$(jq -er .profile <<<"$spec")
    namespace=$(jq -er .namespace <<<"$spec")
    release=$(jq -er .release <<<"$spec")
    scale_set_name=$(jq -er .runner_scale_set_name <<<"$spec")
    values=$(jq -er .values <<<"$spec")
    min_runners=$(jq -er .min_runners <<<"$spec")
    max_runners=$(jq -er .max_runners <<<"$spec")
    if [[ "$profile" == compute-toolchain-canary ]]; then
      image=$TOOLCHAIN_CANARY_IMAGE
    else
      image=$SOCKETLESS_IMAGE
      [[ "$profile" != container-build ]] || image=$CONTAINER_BUILD_IMAGE
      [[ "$profile" != *-candidate ]] || image=$COMPUTE_CANDIDATE_IMAGE
    fi
    seed_image=${SUPER_LINTER_SEED_IMAGE:-}

    kubectl get secret arc-github-app -n "$namespace" >/dev/null
    [[ "$image" != ghcr.io/* ]] || kubectl get secret ghcr-pull -n "$namespace" >/dev/null
    rendered_values="$tmpdir/$profile-values.yaml"
    if [[ "$image" == ghcr.io/* ]]; then
      sed -e "s|RUNNER_IMAGE_REQUIRED|$image|g" -e "s|SUPER_LINTER_SEED_IMAGE_REQUIRED|$seed_image|g" "$values" >"$rendered_values"
    else
      sed -e "s|RUNNER_IMAGE_REQUIRED|$image|g" -e "s|SUPER_LINTER_SEED_IMAGE_REQUIRED|$seed_image|g" "$values" | sed '/imagePullSecrets:/,+1d' >"$rendered_values"
    fi
    helm upgrade --install "$release" "$scale_set_chart" \
      --namespace "$namespace" \
      --values "$rendered_values" \
      --set-string githubConfigUrl="$github_config_url" \
      --set-string runnerScaleSetName="$scale_set_name" \
      --set minRunners="$min_runners" \
      --set maxRunners="$max_runners" \
      --wait --timeout 10m
  done < <(jq -c '.scale_sets[]' <<<"$config_json")

  runner_namespaces=()
  while IFS= read -r namespace; do
    runner_namespaces+=("$namespace")
  done < <(jq -er '.scale_sets[].namespace' <<<"$config_json")
  python3 scripts/reconcile-arc-listeners.py "${runner_namespaces[@]}"
fi
