#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./run_docker.sh [--ubuntu] [--fresh] [--platform linux/amd64|linux/arm64] [--build-ats-ecosim]

Build an Ubuntu 24.04 ATS compiler image and open a container shell.
  --ubuntu       Use Ubuntu 24.04 (the default and only OS).
  --fresh        Rebuild the compiler image without cached layers.
                 Existing source and the ATS build volume are preserved.
  --platform     Select the container architecture; defaults to Docker's native one.
  --build-ats-ecosim
                 Build the selected ATS–EcoSIM sources instead of opening a shell.
                 Set JOBS to control parallel compilation (default 2).
  -h, --help     Show this message.
EOF
}

build_options=()
platform_options=()
build_ats_ecosim=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ubuntu) shift ;;
        --fresh) build_options+=(--no-cache); shift ;;
        --build-ats-ecosim) build_ats_ecosim=true; shift ;;
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
image_name=ats-helper-ubuntu:24.04

docker build ${build_options[@]+"${build_options[@]}"} \
    ${platform_options[@]+"${platform_options[@]}"} \
    -f "$project_dir/docker/ubuntu-compiler.dockerfile" \
    -t "$image_name" "$project_dir"

image_arch=$(docker image inspect --format '{{.Architecture}}' "$image_name")

run_options=(--rm)
container_command=()
if [[ "$build_ats_ecosim" == true ]]; then
    run_options+=(-e "JOBS=${JOBS:-2}")
    container_command=(bash --login /ats-helper/docker/build-ats-ecosim.sh)
else
    run_options+=(-it)
fi

docker run "${run_options[@]}" ${platform_options[@]+"${platform_options[@]}"} \
    --mount "type=bind,source=$project_dir,target=/ats-helper" \
    --mount "type=volume,source=ats-helper-build-$image_arch,target=/ats-build" \
    -w /ats-helper \
    "$image_name" ${container_command[@]+"${container_command[@]}"}
