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
printf "verified provenance for %s from %s\n" "$image" "$revision"
