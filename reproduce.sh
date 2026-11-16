#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
usage() {
    cat <<'EOF'
Usage: ./reproduce.sh CASE [--platform linux/arm64|linux/amd64] [--prepare-only]

Download exact source revisions recursively, build in Ubuntu 24.04 Docker,
run the bundled case, compare its checkpoint, and write a report with plots.
Set JOBS to control compilation (default 2). The first build can take hours.
--prepare-only downloads and verifies sources/inputs without building or running.

Available cases:
EOF
    for manifest in "$project_dir"/cases/*/case.json; do
        [[ -f "$manifest" ]] || continue
        basename "$(dirname "$manifest")"
    done
}
if [[ $# -eq 0 || "$1" == --help || "$1" == -h ]]; then
    usage
    exit 0
fi
case_name=$1
shift
if [[ ! "$case_name" =~ ^[a-z0-9][a-z0-9-]*$ || ! -f "$project_dir/cases/$case_name/case.json" ]]; then
    echo "Unknown case: $case_name" >&2
    usage >&2
    exit 2
fi
exec bash "$project_dir/run_docker.sh" --reproduce "$case_name" "$@"
