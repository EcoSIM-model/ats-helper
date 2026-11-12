# Sourced by the Ubuntu container's login shell, and again after installation.
export ATS_BASE=/ats-build
export AMANZI_SRC_DIR=/ats-helper/repos/amanzi
export ATS_SRC_DIR="$AMANZI_SRC_DIR/src/physics/ats"
export AMANZI_BUILD_DIR="$ATS_BASE/build/amanzi"
export AMANZI_TPLS_BUILD_DIR="$ATS_BASE/build/tpls"
export AMANZI_DIR="$ATS_BASE/install/amanzi"
export AMANZI_TPLS_DIR="$ATS_BASE/install/tpls"
export ATS_DIR="$AMANZI_DIR"
export OPENMPI_DIR=/usr
export PATH="$AMANZI_DIR/bin:$AMANZI_TPLS_DIR/bin:$PATH"

# TPLs such as Trilinos may install libraries in versioned subdirectories.
if [ -d "$ATS_BASE/install" ]; then
    ats_library_dirs=$(find "$ATS_BASE/install" -type d \( -name lib -o -name lib64 \) -print | sort | paste -sd: -)
    if [ -n "$ats_library_dirs" ]; then
        export LD_LIBRARY_PATH="$ats_library_dirs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
    unset ats_library_dirs
fi
