# ATS helper

Download and build ATS in Ubuntu 24.04 using a compiler-container workflow,
adapted from [EcoSIM's Docker launcher](https://github.com/EcoSIM-model/EcoSIM/blob/main/run_docker.sh).

## Reproduce the pond–soil coupling case

With Git and Docker installed and Docker running, clone the branch containing
this case (or check out this helper commit after cloning), then run:

```bash
git clone --branch docker-container-session-reuse https://github.com/EcoSIM-model/ats-helper.git
cd ats-helper
./reproduce.sh 200day-adaptive
```

Or ask the agent: **“reproduce 200day-adaptive”**. The
[reproduce-case skill](.agents/skills/reproduce-case/SKILL.md) uses the same command.
It downloads the exact Amanzi, ATS, and EcoSIM commits with recursive submodules,
builds in Ubuntu 24.04, runs the single-rank 200-day case, compares the final
checkpoint, and writes an HTML report with PNG/PDF plots under
`build-logs/reproduce/200day-adaptive/<run>/`. A nonzero exit means preparation,
build, simulation, or comparison failed; consult the report and logs.

The first dependency build can take hours and needs network access and ample
Docker storage. Later runs reuse a dedicated case volume for the same manifest,
architecture, and compiler image, separate from your development checkouts and
build. Use `JOBS=4 ./reproduce.sh 200day-adaptive` to change compilation
concurrency when memory permits. `--prepare-only` verifies
inputs and recursively downloads sources without building or running;
`--platform linux/arm64` or `--platform linux/amd64` selects the target explicitly.

The self-contained [case definition](cases/200day-adaptive/case.json) pins all
three model commits and checksums for the input, forcing, timestep data, and
reference checkpoint. It preserves the historical EcoSIM clock issue documented
in that file. The baseline was produced on Ubuntu ARM64 with one MPI rank;
another architecture or toolchain may fail its strict numerical comparison.
Ubuntu packages and transitive dependency downloads are not fully locked; the
report records the actual image ID and installed package versions. The plots
show logged timesteps and final soil profiles, rather than a full time series
of every model field.

To use your own build, analysis, or plotting scripts, follow the
[independent reproduction and validation specification](cases/200day-adaptive/REPRODUCE.md).
It defines source selection, build options, expected output, units, flux signs,
balance equations, and tolerances without requiring the exploratory scripts.

This case selects Amanzi `f96df4bc`, ATS `aabc2fcb`, and EcoSIM `d23a54bf`.
The full hashes are in the case definition. These commits include the separate
pond/soil fluxes, shared layer-zero state, adaptive thermal timestep control, and
the standalone EcoSIM module-include fix. The saved reference predates only that
build configuration fix. The input replay matched all 168 reference datasets with the
existing coupled executable; the complete coupled build from an empty dependency
cache has not yet been validated for this packaged command.

To publish another case, add `cases/<name>/case.json`, its input/data and reference
checkpoint using the same schema, with full commit hashes and SHA-256 checksums.
Push the model commits first, then commit and push the helper's scripts, skill,
and entire case folder. Readers need the helper revision containing that case;
the case's model pins do not follow later branch updates. Use a new case name
when changing the scientific baseline.

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

## Semi-implicit EcoSIM surface heat exchange

ATS–EcoSIM uses separate temperature-linearized heat sources for the two
receiving balances in shared-layer mode:

```text
q_layer0(T0) = q0_reference + K0 * (T0 - T0_reference)
q_ground(Tsoil) = qg_reference + Kg * (Tsoil - Tsoil_reference)
```

References and slopes are frozen during each ATS solve and refreshed together
after EcoSIM advances. The native radiation and sensible-heat derivatives are
used for the corresponding exposed surface. Snow, evaporation and water/latent
responses remain lagged; this is not their full Jacobian.

The surface energy PK automatically uses `surface-ecosim_source_linearized` when
its source is `surface-ecosim_source`. Its analytic evaluator derivative enters
the preconditioner as well as the temperature-dependent residual. Custom source
sums should use `surface-ecosim_source_linearized` as their EcoSIM dependency.
The raw source remains available under its original key. Other ATS applications do not publish this source replacement
and retain their existing behavior.

To select the former explicit exchange, set this in the EcoSIM PK:

```xml
<Parameter name="semi-implicit surface heat exchange" type="bool" value="false"/>
```

Additional checkpointed fields (with the default source key) are:

- `surface-ecosim_source_dtemperature`: radiation+sensible slope [MW m⁻² K⁻¹].
- `surface-ecosim_source_temperature_ref`: paired surface temperature [K].
- `surface-ecosim_source_applied`: source actually used on the last accepted ATS
  step [MW m⁻²]; the raw EcoSIM source may already describe the next step.
- `surface-ecosim_source_correction_energy`: cumulative accepted atmospheric
  heat correction relative to the raw source [MJ m⁻²]. Only accepted steps add
  to this ledger; the linearization changes no snow/litter inventory or water
  transfer.
- `surface-ecosim_source_last_advance_time`: the EcoSIM refresh clock [s], kept
  with the references across restarts.

No custom checkpoint rejection guard is added. ATS still requires the new
fields when reading a restart, so checkpoints made before this change need
migration or a fresh run. Rebuild both ATS and EcoSIM because the C/Fortran
feedback layout has changed; their existing container-size check detects a
mismatched installation.

For the one-column freeze–thaw fixture, implement the checkpoint energy and
linearization checks from the
[heat and water balance specification](cases/200day-adaptive/REPRODUCE.md#independent-heat-and-water-balance-checks)
using your preferred HDF5 tools.


## Shared EcoSIM pond/litter layer zero

The EcoSIM PK now enables `shared surface layer` by default. ATS supplies the
combined surface temperature, liquid-water volume, physical ice volume, mobile
pond depth, and gauge pressure to EcoSIM's native layer 0. Snow liquid water
remains in EcoSIM's separate snow stores.

ATS owns the water and energy balances of both the surface pool and soil.
EcoSIM's layer 0 is an ATS-prescribed boundary used to derive fluxes: its water,
ice and temperature are installed before weather/canopy preparation and surface
exchange. Native layer-zero infiltration, phase change, temperature integration,
and litter–soil exchange (including under snow) are bypassed in shared mode.
Temporary donor budgets only limit flux availability. The four `litter_*` mirror
fields are diagnostic outputs and are neither checkpointed as independent stores
nor restored as EcoSIM-owned carry state. Snow retains its own native state;
retired snow transfers water and sensible/fusion energy to the ATS surface pool.

The C/Fortran interface exports separate, already area-weighted net sources:

| ATS field | Receiver | Units after transfer |
| --- | --- | --- |
| `surface-ecosim_layer0_water_source` | Pond/litter pool | m H₂O s⁻¹ |
| `surface-ecosim_layer0_energy_source` | Pond/litter pool | MW m⁻² |
| `surface-ecosim_ground_water_source` | Top soil cell | m H₂O s⁻¹ per column area |
| `surface-ecosim_ground_energy_source` | Top soil cell | MW m⁻² per column area |

Positive sources enter their receiver. The ground path includes direct bare-soil
exchange and snow-to-soil transfers over uncovered soil; it excludes native
layer-zero-to-soil transfers. Its temperature is the actual top ATS soil-cell
temperature, distinct from the supplied pond/litter temperature.

The existing surface physics keys, `surface-ecosim_water_source` and
`surface-ecosim_source`, now contain **only layer-zero sources** in shared mode.
Ground water is converted with column area, cell volume and molar density, then
added once to the top cell of `subsurface_ecosim_water_source` alongside existing
root sources. Ground heat enters `subsurface_ecosim_source` [MW m⁻³]; its paired
slope/reference fields construct `subsurface_ecosim_source_linearized`.
Shared mode attaches these soil sources even when the input had `source term`
set to false. Existing user soil sources are retained; a sum already containing
EcoSIM receives only the missing correction, without duplicating the raw source.
The existing surface source configuration remains in use for the surface PKs.

ATS computes infiltration/advection and conduction between the two pools as
internal, equal-and-opposite exchanges. Pond/litter coverage weights the hydraulic
and thermal interface paths once. At zero coverage, the dry algebraic surface
trace closes to the soil state without an additional conserved surface store.
EcoSIM's native depression-storage formula initializes pond coverage; its
hydrological roughness differs from aerodynamic roughness.

`surface-ecosim_total_water_source` and `surface-ecosim_total_energy_source` are
component-sum diagnostics, not additional applied sources. In the C ABI,
`surface_*` aliases `layer0_*` in shared mode, so the heat flux, slope and reference
remain paired with the same receiver. Legacy mode retains its original source
semantics.

The surface storage evaluators add native litter retention and dry litter heat
capacity to the existing mobile pond storage. In symbols, for surface area A:

```text
Ntotal = Npond + A * Nretained(p)
Etotal = Epond + A * [Nretained(p) * (f*ul + (1-f)*ui) + Cdry*(T - 273.15)]
```

Here `f` is ATS's unfrozen fraction and `ul`, `ui` use the input's phase-energy
models. Retention inverts EcoSIM's existing two-branch litter water-potential
curve. Its analytic pressure derivative and the temperature/phase derivatives
enter the ATS solve. The native organic-carbon stock determines dry heat
capacity. EcoSIM also exports an effective litter/pond thermal resistance,
which ATS applies to the surface-to-soil conduction path. Material and thermal
resistance are frozen during each ATS solve and refreshed after EcoSIM advances.
The surface mobility boundary Jacobian now includes its freezing response.

The current coupled initialization still has **no litter stock**: this change
does not invent litter mass or activate litter biogeochemistry. With no litter,
retention and dry heat capacity vanish and the shared pool reduces to ATS
ponding. The material interface prepares storage for a future initialized litter
stock. Dynamic litter mass/energy changes, optional surface EWC predictors, and
a complete atmospheric-flux Jacobian need separate integration and validation.
Use the standard pressure/temperature solve for this implementation.

The new fields are `surface-ecosim_layer0_material` (14 native material
parameters) and `surface-ecosim_layer0_thermal_resistance` [m² K MW⁻¹]. Rebuild
both libraries together because the C/Fortran environment/configuration ABI
changed. Start from a fresh checkpoint series; old checkpoints lack these fields.
To use the previous independent layer-zero behavior, set the EcoSIM PK's
`shared surface layer` parameter to `false`.

Validation in the Ubuntu 24.04 Docker build:

- The one-day bare-soil case passes separate water/energy budget and source-mapping
  checks. Layer-zero heat is zero when that pool is absent.
- A two-hour partial frozen-pond case (300-second steps) passes with both source
  paths active and separate temperature references differing by up to 5.5 K.
- A ten-second liquid-pond case (one-second steps, diagnostic nonlinear/linear
  tolerances tightened to 1e-10) passes both pool budgets, with references
  differing by over 5 K. Production regression tolerances were unchanged.
- Existing user sources and user sums already containing EcoSIM pass the same
  budget checks, verifying that references are not double counted.
- Native snow/litter conservation checks, native retention/derivative checks,
  and existing ATS `freeze_thaw_surface`, `freeze_thaw_subsurface`, `surface_water`
  gold regressions pass without modifying gold data.
- Five short manufactured storage checks pass: four use 60-second steps and the
  occupied liquid-pond/litter check uses one second. Modified checkpoints discard
  the baseline predictor history. These verify storage, not litter biogeochemistry.
- Longer-step robustness remains unresolved: the manufactured liquid pond with
  retained litter water fails when drained over a single 60-second step. The
  original 200-day hourly case also remains nonconvergent. Consult the exported
  split-flux validation report for the final failing cycle; neither failure is
  represented as a passing regression.

The [field and source-routing specification](cases/200day-adaptive/REPRODUCE.md#checkpoint-fields-geometry-and-units)
and [balance checks](cases/200day-adaptive/REPRODUCE.md#independent-heat-and-water-balance-checks)
define independent checks of each receiver, its temperature feedback, and finite
output. The [optional litter retention and storage procedures](cases/200day-adaptive/REPRODUCE.md#optional-litter-retention-and-storage-checks)
describe the native API, synthetic material, checkpoint preparation, and acceptance
tolerances. Run these diagnostics on copies, keeping the baseline case unchanged.

## License

ATS helper is licensed under the [BSD 3-Clause License](LICENSE).
Downloaded Amanzi, ATS, EcoSIM, and other dependencies retain their own licenses.


## Adaptive pond–soil exchange timestep control

The shared EcoSIM layer-zero coupling now enables a numerical timestep indicator
in the permafrost MPC. It retains separate pond/litter and top-soil energy and water
stores and their physical flux laws. It adds no experimental hydraulic resistance.

The control activates when pond depth exceeds 0.01 mm, the top-soil/pond apparent
heat-capacity ratio is at least 10, and the reference-step exchange number exceeds
4. It estimates `tau = Cpond*Csoil / (G*(Cpond+Csoil))`, using the smaller of the
pond's apparent and sensible capacities to protect against a nonlinear trial
leaving the latent-heat interval. Retained litter water and dry litter heat
capacity are included. `G` is the covered conductive interface conductance,
including the existing native layer-zero thermal resistance. The cap is rounded
down to a dyadic subdivision of the reference timestep, and MPI ranks take a
common minimum. These are configurable numerical thresholds, not a physical
infiltration cap or a proof of unconditional solver convergence.

Merge these settings into the existing input, keeping the other entries:

```xml
<ParameterList name="cycle driver">
  <ParameterList name="timestep manager">
    <Parameter name="prescribed timesteps are maximum" type="bool" value="true" />
  </ParameterList>
</ParameterList>
<ParameterList name="PKs">
  <ParameterList name="permafrost surface and subsurface">
    <ParameterList name="pond soil exchange timestep control">
      <Parameter name="enabled" type="bool" value="true" />
      <Parameter name="maximum exchange number" type="double" value="4.0" />
      <Parameter name="minimum heat capacity contrast" type="double" value="10.0" />
      <Parameter name="minimum ponded depth [m]" type="double" value="0.00001" />
      <Parameter name="reference timestep [s]" type="double" value="3600.0" />
    </ParameterList>
  </ParameterList>
</ParameterList>
```

Use your input's actual permafrost PK name. The control defaults on only when that
MPC uses EcoSIM's shared layer-zero storage; other applications retain their prior
behavior unless explicitly enabled. Set `enabled=false` to disable the indicator.
For diagnostics, set `verbose object / verbosity level=high` on that MPC.

A prescribed schedule remains strict by default. The explicit maximum-schedule
option above allows subdivisions and failed-step retries while preserving the
original cumulative time boundaries, including after restart. Its schedule origin
is zero seconds unless `prescribed timesteps start time [s]` specifies another
origin. An exact fixed-hour regression cannot also take adaptive substeps; its
input must opt into maximum-schedule mode. Ordinary adaptive runs need no
prescribed schedule.

The adaptive configuration completes the 200-day case from its original initial
condition with unchanged nonlinear and linear tolerances. The original strict
hourly control still reproduces the failure. See the
[independent reproduction and conservation checks](cases/200day-adaptive/REPRODUCE.md).

Focused tests in the configured Docker build:

```bash
ctest --test-dir /ats-build/build/amanzi \
  -R '^(utils_time_step_manager|pond_soil_exchange_control)$' --output-on-failure
```
