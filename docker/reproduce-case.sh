#!/usr/bin/env bash
set -euo pipefail
# The case volume contains its own sources and install, separate from development.
export AMANZI_SRC_DIR=/ats-build/sources/amanzi
export ATS_SRC_DIR="$AMANZI_SRC_DIR/src/physics/ats"
export ECOSIM_SRC_DIR=/ats-build/sources/ecosim
# reproduce_case.py prepares these sources itself: the build must not fetch the default set into repos/.
export ATS_HELPER_SOURCES=case
source /ats-helper/docker/ats-env.sh
exec python3 /ats-helper/docker/reproduce_case.py "$@"
