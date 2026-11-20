# ATS helper

Download, build and run ATS with the EcoSIM process kernel in an Ubuntu 24.04 Docker container, reproduce
the bundled cases, and compare ATS-EcoSIM with standalone EcoSIM. The launcher is adapted from
[EcoSIM's Docker launcher](https://github.com/EcoSIM-model/EcoSIM/blob/main/run_docker.sh).

Requires Git and a running Docker (Docker Desktop on macOS, Docker Engine on Linux).

## Task skills

Ask the agent in plain words; each request below runs the matching skill (in
[`.agents/skills/`](.agents/skills), also exposed as `.claude/skills`).

| Skill | Example request |
| --- | --- |
| [Download ATS and EcoSIM](.agents/skills/download-ats/SKILL.md) | "download ats-ecosim" |
| [Build ATS with EcoSIM](.agents/skills/build-ats-ecosim/SKILL.md) | "build ats-ecosim with 4 jobs" |
| [Reproduce a bundled case](.agents/skills/reproduce-case/SKILL.md) | "reproduce 200day-adaptive" or "reproduce pft_tests" |
| [Configure a coupled case](.agents/skills/configure-ats-ecosim-case/SKILL.md) | "Set up prescribed phenology and map PFTs onto my surface mesh" |
| [Prepare hourly forcing](.agents/skills/prepare-ats-ecosim-forcing/SKILL.md) | "Convert my DayMet data to hourly ATS–EcoSIM forcing and validate it" |
| [Extend the interface](.agents/skills/extend-ats-ecosim-interface/SKILL.md) | "Pass a new variable between ATS and EcoSIM with matching C/Fortran layouts" |
| [Diagnose coupling behavior](.agents/skills/diagnose-ats-ecosim-coupling/SKILL.md) | "Investigate surface-temperature oscillations, snowmelt, or a failed timestep" |
| Paired-model [tools](#tools) (no skill) | "Run the pft_tests pairs and build the comparison report" |

Skills link to focused references and name the source revisions they were checked against; recheck them
before applying examples to another version.

## Start the environment

```bash
./run_docker.sh --ubuntu                 # add --platform linux/amd64 for an x86 build on Apple Silicon
```

This builds the compiler image and opens a shell. The repository is mounted at `/ats-helper`; a Docker
volume per architecture is mounted at `/ats-build` (dependencies, build files, installed ATS). Both persist
after you exit. Use the native architecture (emulated builds are much slower); ARM64 and AMD64 use separate
volumes. `--fresh` rebuilds the compiler image only.

The launcher saves the container's full ID, image and mounts in `.docker/container.json` (one record per
session under `.docker/containers/`, ignored by Git). The skills read it and run in that container with
`docker exec`.

## Download the sources

Inside the container (the login shell sets `AMANZI_SRC_DIR`, `ATS_SRC_DIR` and `ECOSIM_SRC_DIR`):

```bash
git clone https://github.com/amanzi/amanzi.git "$AMANZI_SRC_DIR"          # /ats-helper/repos/amanzi
git -C "$AMANZI_SRC_DIR" submodule update --init --recursive               # ATS: $ATS_SRC_DIR
git clone --recursive https://github.com/EcoSIM-model/EcoSIM.git "$ECOSIM_SRC_DIR"   # /ats-helper/repos/ecosim
```

Check out the Amanzi, ATS and EcoSIM revisions you want before building. For repeatable results pin
commits, not branches. The `download-ats` skill does this and verifies the result.

## Build ATS with EcoSIM

Select an ATS revision that contains the EcoSIM kernel (`src/pks/ecosim/EcoSIM_ATS_interface.cc`) and commit
any edits in the EcoSIM checkout, then build on the host:

```bash
git -C repos/amanzi/src/physics/ats checkout --detach <ats-commit>
./run_docker.sh --ubuntu --build-ats-ecosim            # JOBS=4 ./run_docker.sh ... for four compile jobs
```

The build compiles the third-party libraries, ATS and EcoSIM (from the committed `repos/ecosim`) into the
volume; the first one takes hours. A cached EcoSIM of another commit stops the build: use a separate volume.
Do not run `git submodule update` in Amanzi afterwards, it restores Amanzi's recorded ATS commit.

Results, inside the container: `ats` is `/ats-build/install/amanzi/bin/ats` (check
`ats --list_pks | grep EcoSIM`); logs and source revisions are in `/ats-build/logs/`.

## Build ATS without EcoSIM

Inside the container, with ATS checked out:

```bash
cd "$ATS_BASE"
bash "$AMANZI_SRC_DIR/bootstrap.sh" --opt --parallel=2 \
    --with-mpi="$OPENMPI_DIR" --with-cmake=/usr/bin/cmake --with-ctest=/usr/bin/ctest \
    --with-python=/usr/bin/python3 --enable-shared --enable-clm \
    --disable-structured --enable-unstructured --enable-mesh_mstk --disable-mesh_moab \
    --enable-hypre --enable-silo --disable-petsc \
    --disable-amanzi_physics --enable-ats_physics --disable-geochemistry \
    --disable-build_user_guide --ats_dev \
    --amanzi-install-prefix="$AMANZI_DIR" --amanzi-build-dir="$AMANZI_BUILD_DIR" \
    --tpl-install-prefix="$AMANZI_TPLS_DIR" --tpl-build-dir="$AMANZI_TPLS_BUILD_DIR" \
    --tpl-download-dir="$ATS_BASE/downloads" --tools-download-dir="$ATS_BASE/downloads" \
    --tools-build-dir="$ATS_BASE/build/tools" --tools-install-prefix="$ATS_BASE/install/tools"
```

These are the options of [`docker/build-ats-ecosim.sh`](docker/build-ats-ecosim.sh) without
`--enable-ecosim`. To rebuild only ATS with unchanged dependencies, replace the three `--tpl-*` arguments
by `--tpl-config-file="$AMANZI_TPLS_DIR/share/cmake/amanzi-tpl-config.cmake"`. Changing compilers or build
options needs a clean build tree.

## Run ATS

Put the input and its data in the mounted repository and run from that directory:

```bash
cd /ats-helper/path/to/case
ats --xml_file=input.xml
mpiexec --allow-run-as-root -n 2 ats --xml_file=input.xml     # MPI, in the default root shell
```

Outputs under `/ats-helper` appear on the host (owned by root on Linux).

## Reproduce a bundled case

```bash
./reproduce.sh 200day-adaptive      # one 200-day column, compared with a reference checkpoint
./reproduce.sh pft_tests            # the 14 ATS-EcoSIM / standalone EcoSIM pairs of the pft_tests inputs
```

Each case pins the Amanzi, ATS and EcoSIM commits and the checksums of its inputs, downloads the sources,
builds in its own volume (separate from your development build; the first build takes hours), runs, and
writes an HTML report under `build-logs/reproduce/<case>/<run>/`. A nonzero exit means preparation, build,
simulation or comparison failed; read the report and logs. Options: `JOBS=4` (compile jobs),
`--prepare-only` (download and verify sources and inputs only), `--platform linux/arm64|linux/amd64`.

The reference results are Ubuntu ARM64, single-rank; other platforms may fail the strict comparison. Ubuntu
packages and transitive downloads are not locked (the report records the image ID and package versions).
Details: [`cases/200day-adaptive/REPRODUCE.md`](cases/200day-adaptive/REPRODUCE.md) and
[`cases/pft_tests/REPRODUCE.md`](cases/pft_tests/REPRODUCE.md).

To add a case, create `cases/<name>/case.json` with full commit hashes and SHA-256 checksums (see the
existing two), commit the whole folder, and use a new name for a changed baseline. Readers need the helper
revision that contains the case; the pins do not follow later branch updates.

## Tools

| Tool | What it does |
| --- | --- |
| [`run_docker.sh`](run_docker.sh) | builds the compiler image, then opens a shell (`--ubuntu`), builds ATS-EcoSIM (`--build-ats-ecosim`) or runs a case (`--reproduce CASE`) |
| [`reproduce.sh`](reproduce.sh) `CASE` | shorthand for `run_docker.sh --reproduce CASE`; lists the available cases |
| [`docker/build-ats-ecosim.sh`](docker/build-ats-ecosim.sh) | the in-container build that `--build-ats-ecosim` and the reproduction run |
| [`docker/reproduce_case.py`](docker/reproduce_case.py) | fetches the pinned sources, builds, runs and verifies a case (`200day-adaptive`, `pft_tests`) |
| [`docker/lock_pairs_case.py`](docker/lock_pairs_case.py) | writes `<case folder>/case.json` from a finished run of a case folder; refuses unless every run passed |
| [`tools/ats_pairs.py`](tools/ats_pairs.py) | the paired-model tool below |

`tools/ats_pairs.py` turns a case folder into matched pairs and compares them. A case folder, for example
[`cases/pft_tests`](cases/pft_tests) (bare ground plus PFT columns over LAI), holds ATS-EcoSIM inputs
(`<test>.xml`, any number) and the data they refer to, and no scripts. For each input it writes a standalone
EcoSIM case and a revised ATS input on the same graded column, derived from the input itself.

| Command | What it does | Where |
| --- | --- | --- |
| `pair CASE` | writes `CASE/pairs/<test>/`: the standalone EcoSIM case and the revised ATS input of each `<test>.xml`, plus a README of what is and is not matched | host |
| `run CASE [standalone\|ats\|both]` | runs the pairs with a watchdog and writes `status.txt` per run and a summary file | in the container (`--container NAME`) |
| `summarize CASE` | re-evaluates finished runs from their logs, runs nothing | either |
| `report CASE` | comparison report `CASE/pairs/report/index.html` with figures | host |
| `all CASE` | `pair`, `run both` and `report` in turn | host + container |

A run passes only if it reached its end time, exited normally and has no EcoSIM `ENDRUN` (EcoSIM exits with
code 0 when it aborts); it is stopped on a collapsing timestep, at the wall-clock or at the log-size limit.

```bash
pip install -r tools/requirements.txt          # host packages for pair and report
python3 tools/ats_pairs.py pair cases/pft_tests --dry-run          # what would be matched, or why an input is refused
python3 tools/ats_pairs.py all  cases/pft_tests --container NAME --jobs 4
```

To compare your own input, put `my_input.xml` and its forcing data in `cases/my_case/` and run the same
commands on that folder. The pairs start both models at 274.15 K by default (`--start-temperature`); see
[`tools/README.md`](tools/README.md) for what is read from the input, what is changed and what is refused.
Tests (no model is run): `python3 -m unittest discover -s tests`.

## License

ATS helper is licensed under the [BSD 3-Clause License](LICENSE).
Downloaded Amanzi, ATS, EcoSIM, and other dependencies retain their own licenses.
