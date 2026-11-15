#!/usr/bin/env bash
set -euo pipefail
# The case volume contains its own sources and install, separate from development.
export AMANZI_SRC_DIR=/ats-build/sources/amanzi
export ATS_SRC_DIR="$AMANZI_SRC_DIR/src/physics/ats"
export ECOSIM_SRC_DIR=/ats-build/sources/ecosim
source /ats-helper/docker/ats-env.sh
exec python3 /ats-helper/docker/reproduce_case.py "$@"
