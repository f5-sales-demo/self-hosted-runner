#!/usr/bin/env bash
set -euo pipefail

output=${1:-.super-linter-seed}
action_commit=4ce20838b8ab83717e78138c5b3a1407148e0918
index_digest=sha256:c05768164eed53bac7c82aade7a14a76955206d4962cd41be97118db96fa5996
manifest_digest=sha256:a38987de6efa8b7286ef98233eb8454cd1370ab58eeab8190ddd74fe0c7ca849
repository=ghcr.io/super-linter/super-linter
tag=$repository:v8.7.0

for command in docker jq sha256sum zstd; do
  command -v "$command" >/dev/null
done
install -d -m 0700 "$output"
rm -f -- "$output/super-linter.tar" "$output/super-linter.tar.zst" "$output/metadata.env"

reported_index=$(docker buildx imagetools inspect --format '{{json .Manifest}}' "$tag" | jq -er .digest)
[[ "$reported_index" == "$index_digest" ]] || {
  echo "Super-Linter tag no longer resolves to the approved OCI index" >&2
  exit 1
}
index_json=$(docker buildx imagetools inspect --raw "$repository@$index_digest")
actual_index=sha256:$(printf '%s' "$index_json" | sha256sum | cut -d' ' -f1)
[[ "$actual_index" == "$index_digest" ]] || {
  echo "downloaded OCI index is not byte-identical to the approved index" >&2
  exit 1
}
selected_manifest=$(jq -er '
  [.manifests[] | select(.platform.os == "linux" and .platform.architecture == "amd64" and (.platform.variant // "") == "") | .digest]
  | if length == 1 then .[0] else error("expected exactly one linux/amd64 manifest") end
' <<<"$index_json")
[[ "$selected_manifest" == "$manifest_digest" ]] || {
  echo "approved OCI index does not select the approved amd64 manifest" >&2
  exit 1
}

docker image pull --platform linux/amd64 "$repository@$manifest_digest" >/dev/null
image_id=$(docker image inspect --format '{{.Id}}' "$repository@$manifest_digest")
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "pulled Super-Linter image has no immutable image ID" >&2
  exit 1
}
docker image tag "$repository@$manifest_digest" "$tag"
docker image save --output "$output/super-linter.tar" "$tag"
zstd --threads=0 --ultra -19 --rm "$output/super-linter.tar" --output "$output/super-linter.tar.zst"
archive_sha256=$(sha256sum "$output/super-linter.tar.zst" | cut -d' ' -f1)

printf '%s\n' \
  "ACTION_COMMIT=$action_commit" \
  "INDEX_DIGEST=$index_digest" \
  "MANIFEST_DIGEST=$manifest_digest" \
  "IMAGE_ID=$image_id" \
  "ARCHIVE_SHA256=$archive_sha256" \
  "IMAGE_REFERENCE=$tag" \
  >"$output/metadata.env"
chmod 0400 "$output/super-linter.tar.zst" "$output/metadata.env"
printf 'prepared %s (%s) for action %s\n' "$tag" "$manifest_digest" "$action_commit"
