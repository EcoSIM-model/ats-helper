# ATS helper

Download and build ATS in Ubuntu 24.04 using a compiler-container workflow,
adapted from [EcoSIM's Docker launcher](https://github.com/EcoSIM-model/EcoSIM/blob/main/run_docker.sh).

## Start the Ubuntu environment

Install and start Docker Desktop (macOS) or Docker Engine (Linux), then run on
the host:

```bash
cd ats-helper
./run_docker.sh --ubuntu
```

The launcher builds the compiler image and opens a shell. Your repository is
mounted at `/ats-helper`. An architecture-specific Docker volume is mounted at
`/ats-build` for compiled dependencies, build files, downloads, and installed ATS.
Both persist after you exit; the container itself is removed.

Docker assigns a full container ID when the launcher creates the container; the
shell prompt normally shows its first 12 characters. Before attaching the
terminal, the launcher prints the ID and saves a JSON record at
`.docker/container.json`. The record includes the full ID, Docker context, image,
architecture, workspace path, mounts, and a state snapshot. It is refreshed when
the launcher finishes. Inspect Docker for live status before reusing the ID.

Each launch also has its own record at `.docker/containers/<full-id>.json`.
The latest record points to the most recently recorded launch, so an older
terminal exiting cannot replace a newer session's record. These files are
ignored by Git. After a normal exit, the stopped container is removed and its
record is historical; detaching from a running shell leaves the container
available for reuse. Existing terminals started before this launcher change
do not have a generated record.

The download and build skills read this record, verify the running container
and workspace mount, and use `docker exec` with that exact full ID. If you open
the environment first, then request "download ats-ecosim" followed by "build
ats-ecosim", both operations run inside that container. Git downloads and source
preparation use `/ats-helper/repos`; compiled products use `/ats-build`. An
explicitly supplied container ID takes precedence. A stale record must be checked
before reuse; it does not authorize silently downloading on the host instead.

Use the native architecture for local development: ARM64 on Apple Silicon and
AMD64 on x86 machines. If you specifically need an x86 Linux build:

```bash
./run_docker.sh --ubuntu --platform linux/amd64
```

Emulated compilation on Apple Silicon can be substantially slower. ARM64 and
AMD64 use separate build volumes. See [Docker's architecture guidance](https://docs.docker.com/build/building/multi-platform/).
Start with two build jobs and increase only if Docker has enough RAM; the
dependency build also needs substantial disk space.

## Download Amanzi and ATS

Run inside the container. The login shell sets the paths used below.
For a new checkout:

```bash
mkdir -p /ats-helper/repos
git clone https://github.com/amanzi/amanzi.git "$AMANZI_SRC_DIR"

# Optional: select a known Amanzi branch, tag, or commit before initializing ATS.
# git -C "$AMANZI_SRC_DIR" checkout --detach YOUR_AMANZI_REVISION

git -C "$AMANZI_SRC_DIR" submodule update --init --recursive
git -C "$AMANZI_SRC_DIR" rev-parse HEAD
git -C "$AMANZI_SRC_DIR" submodule status --recursive
```

If you already used the `download-ats` skill with its default destination,
reuse that checkout and initialize any missing submodules; skip `git clone`.
ATS lives at `$ATS_SRC_DIR`, under Amanzi's `src/physics/ats` directory.
For repeatable source selection, choose an Amanzi commit and retain its recorded
ATS submodule commit. Using the default branch means future fresh downloads may
select different source. The Ubuntu packages and base image are also mutable.

## Build ATS with EcoSIM

For the coupled process kernel, use Amanzi `master` with ATS
`agraus/ecosim_pk`. With clean source checkouts, select the ATS revision on the
host, then build:

```bash
git -C repos/amanzi/src/physics/ats checkout --detach origin/agraus/ecosim_pk
git -C repos/amanzi/src/physics/ats submodule update --init --recursive
./run_docker.sh --ubuntu --build-ats-ecosim
```

The build command enables EcoSIM and builds third-party libraries and ATS in
the persistent volume. It mirrors the committed source in `repos/ecosim` locally
and directs Amanzi's EcoSIM download to that mirror, so it builds the downloaded
EcoSIM commit. Other dependencies are downloaded normally. Tracked edits in
EcoSIM must be committed before this build. `JOBS=4 ./run_docker.sh
--build-ats-ecosim` selects four parallel jobs; the default is two.

Build logs and source revisions are saved inside the container volume at
`/ats-build/logs/ats-ecosim-build.log` and
`/ats-build/logs/source-revisions.txt`. Open the environment with
`./run_docker.sh`, then run `ats --version` or `ats --help` after installation.
The ATS executable is `/ats-build/install/amanzi/bin/ats`.

The Ubuntu 24.04 ARM64 build was verified with GCC 13.3 and CMake 3.28.3.
The executable loads and registers `EcoSIM for ATS`. A one-day coupling check
passed after removing the obsolete constant evaluators `surface-ecosim_source`
and `surface-ecosim_water_source` from a copy of the upstream freeze–thaw input;
the current PK owns these fields. The full 200-day case with the same input
correction failed with `TimeStepManager: manually prescribed timestep failed`
after temperatures exceeded the constitutive model's bounds. The full regression
has not passed. Reference checkpoints and the source test input were not changed.
Saved build and validation diagnostics are in the local, ignored `build-logs/`
directory and the Docker volume's `/ats-build/logs` and `/ats-build/validation`.

The selected ATS revision intentionally differs from the parent repository's
recorded submodule commit. Running a parent-level
`git submodule update --recursive` afterward would restore that recorded
revision; keep the EcoSIM PK revision selected for this build.

## Build dependencies and ATS without EcoSIM

Still inside the container:

```bash
mkdir -p "$ATS_BASE"
cd "$ATS_BASE"

bash "$AMANZI_SRC_DIR/bootstrap.sh" \
    --opt \
    --parallel=2 \
    --with-mpi="$OPENMPI_DIR" \
    --with-cmake=/usr/bin/cmake \
    --with-ctest=/usr/bin/ctest \
    --with-python=/usr/bin/python3 \
    --enable-shared \
    --enable-clm \
    --disable-structured \
    --enable-unstructured \
    --enable-mesh_mstk \
    --disable-mesh_moab \
    --enable-hypre \
    --enable-silo \
    --disable-petsc \
    --disable-amanzi_physics \
    --enable-ats_physics \
    --disable-geochemistry \
    --disable-build_user_guide \
    --ats_dev \
    --amanzi-install-prefix="$AMANZI_DIR" \
    --amanzi-build-dir="$AMANZI_BUILD_DIR" \
    --tpl-install-prefix="$AMANZI_TPLS_DIR" \
    --tpl-build-dir="$AMANZI_TPLS_BUILD_DIR" \
    --tpl-download-dir="$ATS_BASE/downloads" \
    --tools-download-dir="$ATS_BASE/downloads" \
    --tools-build-dir="$ATS_BASE/build/tools" \
    --tools-install-prefix="$ATS_BASE/install/tools"
```

This follows the ATS options in Amanzi's
[generic build script](https://github.com/amanzi/amanzi/blob/master/build_ATS_generic.sh),
using `--ats_dev` to keep the already initialized ATS checkout. The first build
downloads and compiles third-party libraries, then installs ATS. Network access
is needed during this stage as well as during cloning. EcoSIM physics is not
enabled by this configuration.

After a successful build:

```bash
source /etc/profile.d/ats.sh
command -v ats
ldd "$(command -v ats)"
```

The executable should be `/ats-build/install/amanzi/bin/ats`, and `ldd` should
show no missing libraries. This checks installation and library discovery;
running an ATS example is the next functional check.

To run your own input, place the input and supporting data in the mounted
repository, then run from its directory:

```bash
cd /ats-helper/path/to/case
ats --xml_file=input.xml
```

For an MPI run in this container's default root shell, use:

```bash
mpiexec --allow-run-as-root -n 2 ats --xml_file=input.xml
```

Outputs under `/ats-helper` are visible on the host. On Linux, files written by
this default root container are owned by root.

Exit and run `./run_docker.sh` again to return to the installed environment.
The login shell automatically finds libraries under the persistent install tree.
For an ATS-only rebuild with unchanged compiler and dependency options, replace
the `--tpl-install-prefix`, `--tpl-build-dir`, and `--tpl-download-dir` arguments
with `--tpl-config-file="$AMANZI_TPLS_DIR/share/cmake/amanzi-tpl-config.cmake"`.
Retain the other arguments.

`./run_docker.sh --fresh` rebuilds the compiler image; it does not erase the
build volume or redownload source. Changing compilers or incompatible build
options requires a separate or clean build tree and matching TPLs.

The configuration uses Ubuntu 24.04's compiler toolchain. A complete ATS build
must be tested for the chosen Amanzi revision; older revisions may require
dependency or compiler adjustments. Consult the checked-out
[`INSTALL_ATS.md`](https://github.com/amanzi/amanzi/blob/master/INSTALL_ATS.md)
and build logs when diagnosing failures.
