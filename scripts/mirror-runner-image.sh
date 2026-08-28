#!/usr/bin/env bash
set -euo pipefail

mode=${1:-}
source_image=${2:-}
mirror_image=${3:-}
usage="usage: $0 <copy|verify> <ghcr digest> [ACR digest]"
[[ "$mode" == copy || "$mode" == verify ]] || { echo "$usage" >&2; exit 2; }
source_pattern='^ghcr\.io/f5-sales-demo/self-hosted-runner@(sha256:[0-9a-f]{64})$'
[[ "$source_image" =~ $source_pattern ]] || { echo "source must be an immutable approved GHCR digest" >&2; exit 2; }
digest=${BASH_REMATCH[1]}
registry=f5salesdemoarcca.azurecr.io
expected_mirror="$registry/self-hosted-runner@$digest"

command -v gh >/dev/null
command -v az >/dev/null
command -v docker >/dev/null
: "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID must identify the target subscription}"
test "$(az account show --query id -o tsv)" = "$AZURE_SUBSCRIPTION_ID"
test "$(az acr show --name f5salesdemoarcca --query location -o tsv)" = "canadacentral"
test "$(az acr show --name f5salesdemoarcca --query sku.name -o tsv)" = "Premium"
config=$(mktemp -d)
trap 'rm -rf -- "$config"' EXIT
chmod 0700 "$config"
export DOCKER_CONFIG="$config"
gh auth token | docker login ghcr.io --username "$(gh api user --jq .login)" --password-stdin >/dev/null
az acr login --name f5salesdemoarcca --expose-token --output json \
  | jq -r '.accessToken' \
  | docker login "$registry" --username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null

if [[ "$mode" == copy ]]; then
  [[ -z "$mirror_image" ]] || { echo "$usage" >&2; exit 2; }
  tag="$registry/self-hosted-runner:approved-${digest:7:16}"
  docker buildx imagetools create --tag "$tag" "$source_image" >/dev/null
  observed=$(docker buildx imagetools inspect "$tag" | sed -n 's/^Digest:[[:space:]]*//p' | head -1)
  [[ "$observed" == "$digest" ]] || { echo "ACR import changed digest: $observed" >&2; exit 1; }
  mirror_image=$expected_mirror
fi

[[ "$mirror_image" == "$expected_mirror" ]] || {
  echo "GHCR and ACR digest references are not equal" >&2
  exit 1
}
source_raw=$(docker buildx imagetools inspect --raw "$source_image" | sha256sum | cut -d' ' -f1)
mirror_raw=$(docker buildx imagetools inspect --raw "$mirror_image" | sha256sum | cut -d' ' -f1)
[[ "sha256:$source_raw" == "$digest" && "$source_raw" == "$mirror_raw" ]] || {
  echo "GHCR and ACR manifests are not byte-identical" >&2
  exit 1
}
printf '%s\n' "$mirror_image"
