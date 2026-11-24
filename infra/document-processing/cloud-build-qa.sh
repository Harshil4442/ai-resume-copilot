#!/bin/bash
# Trusted public-source Build controller. The final candidate image is unchanged.
set -euo pipefail
[[ "$#" -ge 2 ]]
mode="$1"; shift
[[ "${HOSTNAME:-}" =~ ^[a-f0-9]{12,64}$ ]]
builder='gcr.io/cloud-builders/docker@sha256:40c2fb4fcd0ad51376eef166c2e7b2b40a3508d5776e2bf33db3783ab39d0f2e'
[[ "$(/usr/bin/docker inspect --format '{{.Image}}' "$HOSTNAME")" == "$(/usr/bin/docker image inspect --format '{{.Id}}' "$builder")" ]]
native="$(/usr/bin/docker info --format '{{.Architecture}} {{.OSType}} {{.CgroupVersion}}')"
[[ "$native" == 'x86_64 linux 2' || "$native" == 'amd64 linux 2' ]]
if [[ "$mode" == build ]]; then
  [[ "$1" == build ]]
  exec /usr/bin/docker "$@"
fi
[[ "$mode" == qa && "$#" -eq 4 ]]
tag="$1"; nonce="$2"; commit="$3"; qualification="$4"
[[ "$nonce" =~ ^[a-f0-9]{32}$ && "$commit" =~ ^[a-f0-9]{40}$ && "$qualification" =~ ^[a-f0-9]{64}$ ]]
[[ "$tag" == "us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-document-inspector:source-revision-$nonce" ]]
workspace_source="$(/usr/bin/docker inspect --format '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{println .Source}}{{end}}{{end}}' "$HOSTNAME")"
[[ "$workspace_source" =~ ^/[a-zA-Z0-9_./-]+$ && "$workspace_source" != '/' && "$workspace_source" != *'/../'* ]]
image_id="$(/usr/bin/docker image inspect --format '{{.Id}}' "$tag")"
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]]
mkdir -m 755 /workspace/.document-build-qa
install -m 555 /usr/bin/docker /workspace/.document-build-qa/docker
mkdir -m 755 /workspace/.document-build-qa/tmp
name="hirewiz-cloudbuild-qa-$nonce"
created=false
cleanup() { if [[ "$created" == true ]]; then /usr/bin/docker rm --force "$name" >/dev/null; fi; }
trap cleanup EXIT
run_controller() {
  local mode="$1"
  local mounts=(--mount "type=bind,source=$workspace_source,target=/workspace")
  if [[ "$workspace_source" != /workspace ]]; then mounts+=(--mount "type=bind,source=$workspace_source,target=$workspace_source"); fi
  /usr/bin/docker create --name "$name" --pull=never --platform=linux/amd64 \
    --user=0:0 --network=none --read-only --cpus=1 --memory=1g --memory-swap=1g --pids-limit=128 \
    --cap-drop=ALL --security-opt=no-new-privileges \
    "${mounts[@]}" \
    --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777 \
    --tmpfs /root:rw,noexec,nosuid,nodev,size=4m,mode=700 \
    --env PATH=/workspace/.document-build-qa:/usr/local/bin:/usr/bin:/bin \
    --env "TMPDIR=$workspace_source/.document-build-qa/tmp" \
    --entrypoint /usr/local/bin/python3 "$image_id" -I -B /workspace/infra/gcp/document_cloud_build_qa.py \
    "$mode" --image-id "$image_id" --tag "$tag" --nonce "$nonce" --commit "$commit" \
    --qualification "$qualification" --workspace-source "$workspace_source" >/dev/null
  created=true
  /usr/bin/docker start --attach "$name"
  /usr/bin/docker rm --force "$name" >/dev/null
  created=false
}
run_controller check
[[ "$(/usr/bin/docker image inspect --format '{{.Id}}' "$tag")" == "$image_id" ]]
/usr/bin/docker push "$tag"
[[ "$(/usr/bin/docker image inspect --format '{{.Id}}' "$tag")" == "$image_id" ]]
run_controller emit
