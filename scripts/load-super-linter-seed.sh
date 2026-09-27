#!/bin/sh
set -u

archive=${SUPER_LINTER_SEED_ARCHIVE:-/seed/super-linter.tar.zst}
metadata=${SUPER_LINTER_SEED_METADATA:-/seed/metadata.env}
output=${SUPER_LINTER_SEED_RESULT:-/runner-runtime/_temp/super-linter-seed.json}
seed_image=${SUPER_LINTER_SEED_IMAGE:-}
expected_action=${SUPER_LINTER_EXPECTED_ACTION_COMMIT:-}
expected_index=${SUPER_LINTER_EXPECTED_INDEX_DIGEST:-}
expected_manifest=${SUPER_LINTER_EXPECTED_MANIFEST_DIGEST:-}
started=$(date +%s)
result=fallback
qualified=false
reason=seed_unavailable
action_commit=
index_digest=
manifest_digest=
image_id=
archive_sha256=
image_reference=ghcr.io/super-linter/super-linter:v8.7.0
seed_digest=${seed_image##*@}

field() {
  sed -n "s/^$1=//p" "$metadata" | tail -n 1
}
valid_digest() {
  printf '%s\n' "$1" | grep -Eq '^sha256:[0-9a-f]{64}$'
}
remove_rejected() {
  docker image rm --force "$image_reference" ${1:+"$1"} >/dev/null 2>&1 || true
}
# shellcheck disable=SC2329 # invoked by the EXIT trap
write_result() {
  completed=$(date +%s)
  duration=$((completed - started))
  directory=${output%/*}
  [ "$directory" != "$output" ] || directory=.
  mkdir -p "$directory"
  temporary="$output.$$"
  printf '{"schema_version":1,"result":"%s","qualified":%s,"reason":"%s","action_commit":"%s","index_digest":"%s","manifest_digest":"%s","seed_image_digest":"%s","image_id":"%s","archive_sha256":"%s","load_duration_seconds":%s}\n' \
    "$result" "$qualified" "$reason" "$action_commit" "$index_digest" \
    "$manifest_digest" "$seed_digest" "$image_id" "$archive_sha256" "$duration" \
    >"$temporary" && mv -f "$temporary" "$output"
}
trap write_result EXIT

if [ ! -r "$archive" ] || [ ! -r "$metadata" ]; then
  reason=seed_files_unavailable
  exit 0
fi
action_commit=$(field ACTION_COMMIT)
index_digest=$(field INDEX_DIGEST)
manifest_digest=$(field MANIFEST_DIGEST)
image_id=$(field IMAGE_ID)
archive_sha256=$(field ARCHIVE_SHA256)
image_reference=$(field IMAGE_REFERENCE)
if [ "$action_commit" != "$expected_action" ] ||
  [ "$index_digest" != "$expected_index" ] ||
  [ "$manifest_digest" != "$expected_manifest" ] ||
  ! valid_digest "$index_digest" || ! valid_digest "$manifest_digest" ||
  ! valid_digest "$image_id" || ! valid_digest "$seed_digest" ||
  ! printf '%s\n' "$archive_sha256" | grep -Eq '^[0-9a-f]{64}$' ||
  [ "$image_reference" != ghcr.io/super-linter/super-linter:v8.7.0 ]; then
  result=rejected
  reason=immutable_identity_mismatch
  remove_rejected ""
  exit 0
fi
actual_archive=$(sha256sum "$archive" 2>/dev/null | cut -d' ' -f1)
if [ "$actual_archive" != "$archive_sha256" ]; then
  result=rejected
  reason=archive_digest_mismatch
  remove_rejected ""
  exit 0
fi
if ! docker info >/dev/null 2>&1; then
  reason=dind_unavailable
  exit 0
fi
if ! docker load --input "$archive" >/dev/null 2>&1; then
  reason=archive_load_failed
  remove_rejected ""
  exit 0
fi
loaded_id=$(docker image inspect --format '{{.Id}}' "$image_reference" 2>/dev/null || true)
if [ "$loaded_id" != "$image_id" ]; then
  result=rejected
  reason=loaded_image_identity_mismatch
  remove_rejected "$loaded_id"
  exit 0
fi
result=hit
qualified=true
reason=verified_archive_loaded
exit 0
