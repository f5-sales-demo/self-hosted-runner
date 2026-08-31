#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 3 ]] || {
  echo "usage: $0 ghcr.io/f5-sales-demo/renovate@sha256:<digest> <40-char-source-commit> <receipt-output>" >&2
  exit 2
}
source_image=$1
source_commit=$2
output=$3
source_pattern='^ghcr\.io/f5-sales-demo/renovate@(sha256:[0-9a-f]{64})$'
[[ "$source_image" =~ $source_pattern ]] || { echo "source must be the immutable derived GHCR Renovate digest" >&2; exit 2; }
digest=${BASH_REMATCH[1]}
[[ "$source_commit" =~ ^[0-9a-f]{40}$ ]] || { echo "source commit must be a full SHA" >&2; exit 2; }
[[ "$output" != renovate-system/image-source.json ]] || { echo "refusing to overwrite the upstream source receipt" >&2; exit 2; }
: "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID must identify the target subscription}"
for command in az docker jq gh; do command -v "$command" >/dev/null; done
[[ "$(az account show --query id -o tsv)" == "$AZURE_SUBSCRIPTION_ID" ]]
[[ "$(az acr show --name f5salesdemoarcca --query location -o tsv)" == canadacentral ]]
[[ "$(az acr show --name f5salesdemoarcca --query sku.name -o tsv)" == Premium ]]
registry=f5salesdemoarcca.azurecr.io
mirror_image="$registry/renovate@$digest"
config=$(mktemp -d)
trap 'rm -rf -- "$config"' EXIT
chmod 0700 "$config"
export DOCKER_CONFIG="$config"
gh auth token | docker login ghcr.io --username "$(gh api user --jq .login)" --password-stdin >/dev/null
az acr login --name f5salesdemoarcca --expose-token --output json | jq -r .accessToken |
  docker login "$registry" --username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null
gh attestation verify "oci://${source_image}" \
  --repo f5-sales-demo/self-hosted-runner \
  --signer-workflow f5-sales-demo/self-hosted-runner/.github/workflows/publish-renovate.yml@refs/heads/main \
  --source-digest "$source_commit" \
  --deny-self-hosted-runners >/dev/null
tag="$registry/renovate:approved-${digest:7:16}"
docker buildx imagetools create --tag "$tag" "$source_image" >/dev/null
observed=$(docker buildx imagetools inspect "$tag" --format '{{json .Manifest}}' | jq -er .digest)
[[ "$observed" == "$digest" ]] || { echo "ACR promotion changed the manifest digest" >&2; exit 1; }
source_manifest=$(docker buildx imagetools inspect --raw "$source_image")
mirror_manifest=$(docker buildx imagetools inspect --raw "$mirror_image")
[[ "$source_manifest" == "$mirror_manifest" ]] || { echo "GHCR and ACR manifests are not byte-identical" >&2; exit 1; }
manifest_receipt=$(printf %s "$source_manifest" | sha256sum | cut -d' ' -f1)
[[ "sha256:$manifest_receipt" == "$digest" ]] || { echo "raw manifest receipt differs from digest" >&2; exit 1; }
upstream_version=$(jq -er .upstream.version renovate-system/image-source.json)
upstream_image=$(jq -er .upstream.image renovate-system/image-source.json)
jq -n --arg version "$upstream_version" --arg upstream "$upstream_image" --arg ghcr "$source_image" --arg acr "$mirror_image" --arg commit "$source_commit" --arg receipt "sha256:$manifest_receipt" \
  '{schema_version:1,upstream:{version:$version,image:$upstream},derived:{ghcr:$ghcr,acr:$acr,source_commit:$commit,manifest_receipt:$receipt}}' >"$output"
printf 'promoted %s byte-identically to %s; receipt %s\n' "$source_image" "$mirror_image" "sha256:$manifest_receipt"
