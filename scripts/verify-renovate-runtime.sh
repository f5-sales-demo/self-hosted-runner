#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: $0 <derived-renovate-image>" >&2; exit 2; }
image=$1
evidence=$(mktemp -d)
trap 'rm -rf -- "$evidence"' EXIT
chmod 0777 "$evidence"
printf '%s' 'ghs_structured.token-0123456789' >"$evidence/installation-token"
chmod 0666 "$evidence/installation-token"

output=$(docker run --rm --read-only \
  --volume "$evidence:/token" \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --tmpfs /work:rw,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --tmpfs /cache:rw,nosuid,nodev,uid=12021,gid=0,mode=0770 \
  --env RENOVATE_TOKEN_FILE=/token/installation-token \
  "$image" --version 2>&1)
grep -Fq '44.52.1' <<<"$output"
[[ ! -e "$evidence/installation-token" ]]
if grep -Fq 'Initializing tools all' <<<"$output"; then
  echo "Renovate unexpectedly initialized the hidden upstream tool cache" >&2
  exit 1
fi
printf 'verified read-only non-root Renovate runtime and token deletion\n'
