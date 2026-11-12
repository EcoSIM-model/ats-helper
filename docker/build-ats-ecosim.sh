#!/usr/bin/env bash
set -euo pipefail

source /etc/profile.d/ats.sh
ECOSIM_SRC_DIR=/ats-helper/repos/ecosim
build_jobs=${JOBS:-2}
if [[ ! "$build_jobs" =~ ^[1-9][0-9]*$ ]]; then
    echo 'JOBS must be a positive integer.' >&2
    exit 2
fi
if [[ ! -f "$ATS_SRC_DIR/src/pks/ecosim/EcoSIM_ATS_interface.cc" ]]; then
    echo 'Select an ATS revision containing the EcoSIM PK before building.' >&2
    exit 2
fi

mkdir -p "$ATS_BASE/logs" "$ATS_BASE/source-mirrors"
exec > >(tee -a "$ATS_BASE/logs/ats-ecosim-build.log") 2>&1
echo "Starting ATS–EcoSIM build: $(date -u +%FT%TZ), jobs=$build_jobs"

# Docker bind mounts can have different ownership from the container's user.
for repo in "$AMANZI_SRC_DIR" "$ATS_SRC_DIR" "$ECOSIM_SRC_DIR"; do
    git config --global --add safe.directory "$repo"
done

if [[ -n "$(git -C "$ECOSIM_SRC_DIR" status --porcelain --untracked-files=no)" ]]; then
    echo 'EcoSIM has tracked changes. Commit them before building from its Git mirror.' >&2
    exit 2
fi

# Preserve the source checkout and use its exact commit for the TPL build.
# Redirect only EcoSIM's upstream URL; all other dependencies use their normal URLs.
mapfile -t ecosim_config < <(python3 - "$AMANZI_SRC_DIR/config/SuperBuild/TPLVersions.cmake" <<'PY'
import re
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
for key in ('ECOSIM_GIT_REPOSITORY', 'ECOSIM_GIT_TAG'):
    matches = re.findall(r'^\s*set\(' + key + r'\s+"([^"\n]+)"\s*\)', text, re.M)
    if len(matches) != 1 or '${' in matches[0]:
        raise SystemExit('Cannot resolve ' + key + ' from Amanzi configuration')
    print(matches[0])
PY
)
if [[ ${#ecosim_config[@]} -ne 2 ]]; then
    echo 'Could not read EcoSIM repository and revision.' >&2
    exit 2
fi
ecosim_url=${ecosim_config[0]}
ecosim_ref=${ecosim_config[1]}
ecosim_commit=$(git -C "$ECOSIM_SRC_DIR" rev-parse HEAD)
shopt -s nullglob
for tpl_source in "$AMANZI_TPLS_BUILD_DIR"/ecosim/ecosim-*-source; do
    if [[ -d "$tpl_source/.git" ]] && \
       [[ "$(git -C "$tpl_source" rev-parse HEAD)" != "$ecosim_commit" ]]; then
        echo "The cached EcoSIM source differs from $ecosim_commit: $tpl_source" >&2
        echo 'Use a separate build volume for a different EcoSIM commit.' >&2
        exit 2
    fi
done
shopt -u nullglob
mirror="$ATS_BASE/source-mirrors/EcoSIM.git"
if [[ ! -d "$mirror" ]]; then
    git clone --bare "$ECOSIM_SRC_DIR" "$mirror"
else
    git --git-dir="$mirror" fetch "$ECOSIM_SRC_DIR" "$ecosim_commit"
fi
if [[ ! "$ecosim_ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
    git check-ref-format "refs/heads/$ecosim_ref"
    git --git-dir="$mirror" update-ref "refs/heads/$ecosim_ref" "$ecosim_commit"
fi
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0="url.file://$mirror.insteadOf"
export GIT_CONFIG_VALUE_0="$ecosim_url"
export GIT_CONFIG_KEY_1=protocol.file.allow
export GIT_CONFIG_VALUE_1=always

{
    for repo in "$AMANZI_SRC_DIR" "$ATS_SRC_DIR" "$ECOSIM_SRC_DIR"; do
        printf '%s ' "$repo"
        git -C "$repo" rev-parse HEAD
    done
    cmake --version | head -1
    mpicc --version | head -1
    gfortran --version | head -1
} > "$ATS_BASE/logs/source-revisions.txt"
cat "$ATS_BASE/logs/source-revisions.txt"

# Open MPI's build-time checks run inside this container as root.
export OMPI_ALLOW_RUN_AS_ROOT=1
export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
cd "$ATS_BASE"
bash "$AMANZI_SRC_DIR/bootstrap.sh" \
    --opt --parallel="$build_jobs" \
    --with-mpi="$OPENMPI_DIR" \
    --with-cmake=/usr/bin/cmake --with-ctest=/usr/bin/ctest \
    --with-python=/usr/bin/python3 \
    --enable-shared --enable-clm \
    --disable-structured --enable-unstructured \
    --enable-mesh_mstk --disable-mesh_moab \
    --enable-hypre --enable-silo --disable-petsc \
    --disable-amanzi_physics --enable-ats_physics --enable-ecosim \
    --disable-geochemistry --disable-build_user_guide --ats_dev \
    --amanzi-install-prefix="$AMANZI_DIR" \
    --amanzi-build-dir="$AMANZI_BUILD_DIR" \
    --tpl-install-prefix="$AMANZI_TPLS_DIR" \
    --tpl-build-dir="$AMANZI_TPLS_BUILD_DIR" \
    --tpl-download-dir="$ATS_BASE/downloads" \
    --tools-download-dir="$ATS_BASE/downloads" \
    --tools-build-dir="$ATS_BASE/build/tools" \
    --tools-install-prefix="$ATS_BASE/install/tools"

source /etc/profile.d/ats.sh
test -x "$AMANZI_DIR/bin/ats"
ldd "$AMANZI_DIR/bin/ats" | tee "$ATS_BASE/logs/ats-libraries.txt"
if grep -q 'not found' "$ATS_BASE/logs/ats-libraries.txt"; then
    echo 'ATS was installed but has unresolved shared libraries.' >&2
    exit 1
fi
"$AMANZI_DIR/bin/ats" --print_version
"$AMANZI_DIR/bin/ats" --list_pks | tee "$ATS_BASE/logs/ats-pks.txt"
grep -Fq 'EcoSIM for ATS' "$ATS_BASE/logs/ats-pks.txt"
echo "ATS–EcoSIM installed: $AMANZI_DIR/bin/ats"
