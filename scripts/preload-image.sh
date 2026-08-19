#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 ghcr.io/f5-sales-demo/self-hosted-runner@sha256:<digest> <standard|container-build>" >&2
  exit 2
fi

image="$1"
profile="$2"
if [[ ! "$image" =~ ^ghcr.io/f5-sales-demo/self-hosted-runner@sha256:[0-9a-f]{64}$ ]]; then
  echo "image must be the approved immutable GHCR digest" >&2
  exit 2
fi
case "$profile" in standard|container-build) ;; *) echo "unknown profile" >&2; exit 2 ;; esac

docker pull "$image"
repo_digests="$(docker image inspect --format '{{json .RepoDigests}}' "$image")"
if ! grep -Fq "\"$image\"" <<<"$repo_digests"; then
  echo "local image does not retain the requested digest identity" >&2
  exit 1
fi
source_label="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.source"}}' "$image")"
profile_label="$(docker image inspect --format '{{index .Config.Labels "f5.sales-demo.runner.profile"}}' "$image")"
if [[ "$source_label" != "https://github.com/f5-sales-demo/self-hosted-runner" || "$profile_label" != "$profile" ]]; then
  echo "image labels do not identify the approved source and profile" >&2
  exit 1
fi

docker run --rm --entrypoint verify-runner-tools "$image" "$profile"
printf "preloaded and verified %s profile=%s\n" "$image" "$profile"
