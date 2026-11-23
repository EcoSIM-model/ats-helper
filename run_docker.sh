#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./run_docker.sh [--ubuntu] [--fresh] [--platform linux/amd64|linux/arm64] [--build-ats-ecosim | --reproduce CASE | --fetch-sources [SET]]

Build an Ubuntu 24.04 ATS compiler image and open a container shell.
The latest container record is saved to .docker/container.json.
  --ubuntu       Use Ubuntu 24.04 (the default and only OS).
  --fresh        Rebuild the compiler image without cached layers.
                 Existing source and the ATS build volume are preserved.
  --platform     Select the container architecture; defaults to Docker's native one.
  --build-ats-ecosim
                 Build the ATS–EcoSIM sources in repos/ instead of opening a shell. If repos/amanzi and
                 repos/ecosim are missing, the pinned default set of sources.json is fetched first
                 (ATS_HELPER_SOURCES=local skips that and builds what you have).
                 Set JOBS to control parallel compilation (default 2).
  --fetch-sources [SET]
                 Put the pinned Amanzi, ATS and EcoSIM sources (the default set of sources.json, or SET)
                 in repos/amanzi and repos/ecosim; existing checkouts are never changed.
  --reproduce CASE
                 Fetch, build, run, and verify a bundled case in its own volume.
  --prepare-only Only fetch/verify sources and inputs for --reproduce.
  -h, --help     Show this message.
EOF
}

build_options=()
platform_options=()
build_ats_ecosim=false
fetch_sources=false
fetch_set=
reproduce_case=
prepare_only=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ubuntu) shift ;;
        --fresh) build_options+=(--no-cache); shift ;;
        --build-ats-ecosim) build_ats_ecosim=true; shift ;;
        --fetch-sources)
            fetch_sources=true; shift
            if [[ $# -ge 1 && "$1" != -* ]]; then fetch_set=$1; shift; fi ;;
        --reproduce)
            [[ $# -ge 2 ]] || { echo '--reproduce requires a case name.' >&2; exit 2; }
            reproduce_case=$2; shift 2 ;;
        --prepare-only) prepare_only=true; shift ;;
        --platform)
            if [[ $# -lt 2 ]]; then
                echo 'Error: --platform requires linux/amd64 or linux/arm64.' >&2
                exit 2
            fi
            case "$2" in
                linux/amd64|linux/arm64) platform_options=(--platform "$2") ;;
                *) echo "Unsupported platform: $2" >&2; exit 2 ;;
            esac
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ -n "$reproduce_case" ]]; then
    if [[ "$build_ats_ecosim" == true || "$fetch_sources" == true || ! "$reproduce_case" =~ ^[a-z0-9][a-z0-9_-]*$ ||
          ! -f "$project_dir/cases/$reproduce_case/case.json" ]]; then
        echo 'Select one valid reproduction case, without --build-ats-ecosim.' >&2; exit 2
    fi
elif [[ "$prepare_only" == true ]]; then
    echo '--prepare-only requires --reproduce CASE.' >&2; exit 2
elif [[ "$fetch_sources" == true && "$build_ats_ecosim" == true ]]; then
    echo '--fetch-sources cannot be combined with --build-ats-ecosim (the build fetches by itself).' >&2; exit 2
fi
image_name=ats-helper-ubuntu:24.04
# --mount uses CSV syntax as well as shell argument parsing.
mount_source=${project_dir//\"/\"\"}
workspace_mount="type=bind,\"source=$mount_source\",target=/ats-helper"

docker build ${build_options[@]+"${build_options[@]}"} \
    ${platform_options[@]+"${platform_options[@]}"} \
    -f "$project_dir/docker/ubuntu-compiler.dockerfile" \
    -t "$image_name" "$project_dir"

image_arch=$(docker image inspect --format '{{.Architecture}}' "$image_name")
image_id=$(docker image inspect --format '{{.Id}}' "$image_name")
build_volume="ats-helper-build-$image_arch"

docker_context=$(docker context show)
session_mode=shell
create_options=()
container_command=()
if [[ -n "$reproduce_case" ]]; then
    session_mode=reproduce
    # The volume is named after what identifies the case: its case.json and, when it names a source set,
    # the resolved set (so a changed pin never reuses a volume holding other sources). Cases with inline
    # sources keep the plain `git hash-object case.json`. Without python3 the files are hashed directly.
    case_file="$project_dir/cases/$reproduce_case/case.json"
    if ! case_hash=$(python3 "$project_dir/docker/reproduce_case.py" --identity "$reproduce_case" 2>/dev/null); then
        if grep -Eq '"sources"[[:space:]]*:[[:space:]]*"' "$case_file"; then
            case_hash=$(cat "$case_file" "$project_dir/sources.json" | git hash-object --stdin)
        else
            case_hash=$(git hash-object "$case_file")
        fi
    fi
    compiler_hash=${image_id#sha256:}
    build_volume="ats-helper-reproduce-$reproduce_case-${case_hash:0:12}-$image_arch-${compiler_hash:0:12}"
    create_options+=(-e "JOBS=${JOBS:-2}" -e "ATS_REPRODUCE_IMAGE_ID=$image_id")
    container_command=(bash --login /ats-helper/docker/reproduce-case.sh "$reproduce_case")
    if [[ "$prepare_only" == true ]]; then container_command+=(--prepare-only); fi
elif [[ "$build_ats_ecosim" == true ]]; then
    session_mode=build
    create_options+=(-e "JOBS=${JOBS:-2}" -e "ATS_HELPER_SOURCES=${ATS_HELPER_SOURCES:-pinned}")
    container_command=(bash --login /ats-helper/docker/build-ats-ecosim.sh)
elif [[ "$fetch_sources" == true ]]; then
    session_mode=fetch
    container_command=(python3 /ats-helper/docker/fetch_sources.py)
    if [[ -n "$fetch_set" ]]; then container_command+=(--set "$fetch_set"); fi
else
    create_options+=(-it)
fi

record_dir="$project_dir/.docker"
mkdir -p "$record_dir/containers"
record_path="$record_dir/container.json"

# Serialize Docker's own values as JSON; paths and names may contain special characters.
record_template=$(cat <<'EOF'
{
  "schema_version": 1,
  "container_id": {{json .Id}},
  "container_name": {{json .Name}},
  "docker_context": {{json (index .Config.Labels "ats-helper.context")}},
  "image": {{json .Config.Image}},
  "image_id": {{json .Image}},
  "architecture": {{json (index .Config.Labels "ats-helper.architecture")}},
  "mode": {{json (index .Config.Labels "ats-helper.mode")}},
  "project_dir": {{json (index .Config.Labels "ats-helper.project_dir")}},
  "working_directory": {{json .Config.WorkingDir}},
  "created_at": {{json .Created}},
  "state_at_recording": {
    "status": {{json .State.Status}},
    "running": {{json .State.Running}},
    "exit_code": {{json .State.ExitCode}}
  },
  "mounts": {{json .Mounts}}
}
EOF
)

write_container_record() {
    local record_tmp
    record_tmp=$(mktemp "$record_dir/.record.XXXXXX")
    if ! docker inspect --format "$record_template" "$container_id" > "$record_tmp"; then
        rm -f "$record_tmp"
        return 1
    fi
    mv -f "$record_tmp" "$record_dir/containers/$container_id.json"
}

finish_session() {
    local command_status=$?
    local container_state
    trap - EXIT
    if container_state=$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null); then
        write_container_record || echo 'Warning: could not refresh the container record.' >&2
        # A detached shell may still be running. Preserve it for subsequent docker exec calls.
        case "$container_state" in
            created|exited|dead)
                docker rm "$container_id" >/dev/null || \
                    echo "Warning: could not remove stopped container $container_id." >&2
                ;;
        esac
    fi
    return "$command_status"
}

# Create first so the exact ID can be recorded before the interactive terminal attaches.
container_id=$(docker create ${create_options[@]+"${create_options[@]}"} ${platform_options[@]+"${platform_options[@]}"} \
    --label "ats-helper.project_dir=$project_dir" \
    --label "ats-helper.context=$docker_context" \
    --label "ats-helper.architecture=$image_arch" \
    --label "ats-helper.mode=$session_mode" \
    --mount "$workspace_mount" \
    --mount "type=volume,source=$build_volume,target=/ats-build" \
    -w /ats-helper \
    "$image_name" ${container_command[@]+"${container_command[@]}"})
if [[ ! "$container_id" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Docker returned an invalid container ID: $container_id" >&2
    exit 1
fi
trap finish_session EXIT
write_container_record

# The latest pointer follows one per-container file. Finishing an older session
# updates only its own record and cannot overwrite a newer session's pointer.
latest_tmp=$(mktemp "$record_dir/.latest.XXXXXX")
rm -f "$latest_tmp"
ln -s "containers/$container_id.json" "$latest_tmp"
mv -f "$latest_tmp" "$record_path"
echo "Container: $container_id"
echo "Container record: $record_path"

attach_status=0
if [[ "$session_mode" == shell ]]; then
    docker start --attach --interactive "$container_id" || attach_status=$?
else
    docker start --attach "$container_id" || attach_status=$?
fi

# Preserve the workload's exit code even if the attach command reports success.
if container_state=$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null); then
    if [[ "$container_state" == exited || "$container_state" == dead ]]; then
        exit "$(docker inspect --format '{{.State.ExitCode}}' "$container_id")"
    fi
fi
exit "$attach_status"
