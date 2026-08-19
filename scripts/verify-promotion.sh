#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 ghcr.io/f5-sales-demo/self-hosted-runner@sha256:<digest> <source-commit>" >&2
  exit 2
fi

image="$1"
revision="$2"
if [[ ! "$image" =~ ^ghcr.io/f5-sales-demo/self-hosted-runner@sha256:[0-9a-f]{64}$ ]]; then
  echo "image must be the approved immutable GHCR digest" >&2
  exit 2
fi
if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo "source revision must be a full commit SHA" >&2
  exit 2
fi


gh attestation verify "oci://${image}" \
  --repo f5-sales-demo/self-hosted-runner \
  --signer-workflow f5-sales-demo/self-hosted-runner/.github/workflows/publish.yml@refs/heads/main \
  --source-digest "$revision" \
  --deny-self-hosted-runners \
  --bundle-from-oci
printf "verified GitHub provenance for %s from %s\n" "$image" "$revision"

registry="${image%@*}"
index="$(mktemp)"
trap 'rm -f "$index"' EXIT
docker buildx imagetools inspect --raw "$image" > "$index"
mapfile -t attestations < <(jq -r '.manifests[] | select(.annotations["vnd.docker.reference.type"] == "attestation-manifest") | .digest' "$index")
found_spdx=0
for attestation in "${attestations[@]}"; do
  if docker buildx imagetools inspect --raw "${registry}@${attestation}" | jq -e 'any(.layers[]; ((.annotations["in-toto.io/predicate-type"] // "") | test("^https://spdx\.dev/")))' >/dev/null; then
    found_spdx=1
    break
  fi
done
if [[ "$found_spdx" -ne 1 ]]; then
  echo "published image is missing an OCI SPDX SBOM attestation" >&2
  exit 1
fi
printf "verified BuildKit SPDX SBOM attestation for %s\n" "$image"
