# Reproduce and independently validate the pond–soil case

This is a specification for your own scripts. The supplied
[`../../reproduce.sh`](../../reproduce.sh) command implements the baseline run,
checkpoint comparison, and basic plots. The optional checks below need no
particular plotting or validation language.

## Inputs and environment

Read URLs, full source commits, file SHA-256 hashes, runtime expectations, and
comparison tolerances from [`case.json`](case.json). Verify every listed file
before running. Keep the input parameters and both HDF5 files unchanged; do not
regenerate or round the prescribed timesteps. Retain a copy of this helper
revision with your run record.

The reference environment is Ubuntu 24.04, Linux ARM64, GNU C++/Fortran 13.3,
and one MPI rank. Use the committed [compiler Dockerfile](../../docker/ubuntu-compiler.dockerfile)
or record equivalent compiler, MPI, CMake, and library versions. Docker image
identity and installed package versions should accompany the results. Ubuntu
packages and all transitive downloads are not locked by this case, so source
pins alone do not guarantee identical results on another platform.

## Independent source selection and build

Build inside the compiler container, with the helper checkout at `/ats-helper`
and a new writable work volume at `/ats-work`. For example, from the helper root
on the host:

```bash
docker build -f docker/ubuntu-compiler.dockerfile -t ats-helper-ubuntu:24.04 .
docker run --rm -it \
  --mount "type=bind,source=$PWD,target=/ats-helper" \
  --mount type=volume,source=ats-pond-reproduce-work,target=/ats-work \
  -w /ats-helper ats-helper-ubuntu:24.04 bash --login
```

Inside the container, set `WORK=/ats-work`. Using any JSON reader, set
`AMANZI_URL`, `AMANZI_COMMIT`, `ATS_COMMIT`, `ECOSIM_URL`, and `ECOSIM_COMMIT`
from `case.json`. The following commands assume fresh destinations:

```bash
mkdir -p "$WORK/sources"
export AMANZI_SRC_DIR="$WORK/sources/amanzi"
export ECOSIM_SRC_DIR="$WORK/sources/ecosim"
export AMANZI_TPLS_DIR="$WORK/install/tpls"
git clone --no-checkout "$AMANZI_URL" "$AMANZI_SRC_DIR"
git -C "$AMANZI_SRC_DIR" fetch origin "$AMANZI_COMMIT"
git -C "$AMANZI_SRC_DIR" checkout --detach "$AMANZI_COMMIT"
git -c url.https://github.com/.insteadOf=git@github.com: \
  -C "$AMANZI_SRC_DIR" submodule update --init --recursive
git clone --no-checkout "$ECOSIM_URL" "$ECOSIM_SRC_DIR"
git -C "$ECOSIM_SRC_DIR" fetch origin "$ECOSIM_COMMIT"
git -C "$ECOSIM_SRC_DIR" checkout --detach "$ECOSIM_COMMIT"
git -c url.https://github.com/.insteadOf=git@github.com: \
  -C "$ECOSIM_SRC_DIR" submodule update --init --recursive
```

ATS is Amanzi's `src/physics/ats` submodule. Require its HEAD to equal
`ATS_COMMIT`, and require every recursive submodule to match its parent's
gitlink. Do not use `submodule update --remote`. The HTTPS rewrite supports
EcoSIM's public SSH-addressed submodule without forwarding SSH keys. For existing
checkouts, verify their origins, commits, and local changes before reuse.

The pinned Amanzi still declares EcoSIM ref `agraus/PrescribedPhenology` in
`config/SuperBuild/TPLVersions.cmake`. That branch name must resolve to the
**manifest's EcoSIM commit** for this build. A local mirror can do this without
editing the source checkout or following a remote branch tip:

```bash
git clone --bare "$ECOSIM_SRC_DIR" "$WORK/EcoSIM.git"
git --git-dir="$WORK/EcoSIM.git" update-ref \
  refs/heads/agraus/PrescribedPhenology "$ECOSIM_COMMIT"
export GIT_CONFIG_COUNT=3
export GIT_CONFIG_KEY_0="url.file://$WORK/EcoSIM.git.insteadOf"
export GIT_CONFIG_VALUE_0=https://github.com/EcoSIM-model/EcoSIM
export GIT_CONFIG_KEY_1=protocol.file.allow
export GIT_CONFIG_VALUE_1=always
export GIT_CONFIG_KEY_2=url.https://github.com/.insteadOf
export GIT_CONFIG_VALUE_2=git@github.com:
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
cd "$WORK"
bash "$AMANZI_SRC_DIR/bootstrap.sh" \
  --opt --parallel=2 --with-mpi=/usr \
  --with-cmake=/usr/bin/cmake --with-ctest=/usr/bin/ctest \
  --with-python=/usr/bin/python3 --enable-shared --enable-clm \
  --disable-structured --enable-unstructured --enable-mesh_mstk \
  --disable-mesh_moab --enable-hypre --enable-silo --disable-petsc \
  --disable-amanzi_physics --enable-ats_physics --enable-ecosim \
  --disable-geochemistry --disable-build_user_guide --ats_dev \
  --amanzi-install-prefix="$WORK/install/amanzi" \
  --amanzi-build-dir="$WORK/build/amanzi" \
  --tpl-install-prefix="$WORK/install/tpls" \
  --tpl-build-dir="$WORK/build/tpls" \
  --tpl-download-dir="$WORK/downloads" --tools-download-dir="$WORK/downloads" \
  --tools-build-dir="$WORK/build/tools" --tools-install-prefix="$WORK/install/tools"
```

Preserve build logs and confirm the EcoSIM source actually compiled under the
TPL build tree has the locked commit and no tracked edits. Require a successful
build, no unresolved `ldd` entries, and `EcoSIM for ATS` in `ats --list_pks`.
Use a separate build tree when changing source pins or toolchains.

## Run and baseline acceptance

```bash
export LD_LIBRARY_PATH="$(find "$WORK/install" -type d \( -name lib -o -name lib64 \) -print | sort | paste -sd: -)"
mkdir "$WORK/run"
cp /ats-helper/cases/200day-adaptive/input.xml "$WORK/run/"
cp -R /ats-helper/cases/200day-adaptive/data "$WORK/run/"
cd "$WORK/run"
mpiexec --allow-run-as-root --stdin none -n 1 \
  "$WORK/install/amanzi/bin/ats" --xml_file=input.xml > model.stdout 2>&1
```

Require successful process exit. In `model.stdout`, require the cycle-start
sequence `0, 1, ..., 4810` with no repeated cycle and no
`step failed with error code` diagnostic. Read the expected step count from
the manifest rather than hard-coding it in reusable scripts.

In `checkpoint_final.h5`, require `time` within `1e-6 s` of the manifest end time
(17,280,000 s), `cycle` equal to its accepted-step count (4,811), and
`mpi_num_procs` equal to its rank count (1). Require all numeric datasets to be
finite, with the same dataset names and shapes as the reference checkpoint
(168 datasets in this case). Compare every numeric element using:

```text
abs(actual - reference) <= atol + rtol * abs(reference)
```

Use `atol` and `rtol` from `case.json`; compare nonnumeric datasets exactly.
Do not compare raw HDF5 file bytes, version strings, wall times, or every
integrator metadata attribute. Reaching day 200 alone is not a baseline pass.

## Checkpoint fields, geometry, and units

The following optional physics checks apply to this fixture: one 1 m² column,
100 cells over 10 m, top soil cell index 99, surface cell index 0, and top soil
faces 499 (lower) and 500 (upper). Each soil cell has volume `V=0.1 m³`.
These are zero-based serial indices, not general rules for other partitions or
meshes. Face fluxes at these two faces are positive upward.

Flatten scalar component datasets to select a cell or face. Here `part` means
`layer0` or `ground`; `@current` is part of the literal dataset name.

| Dataset | Meaning / units |
| --- | --- |
| `surface-ecosim_<part>_energy_source.cell.0` | Raw external heat source, MW m⁻² |
| `surface-ecosim_<part>_water_source.cell.0` | Raw water source, m H₂O s⁻¹ |
| `surface-ecosim_total_energy_source.cell.0` / `...total_water_source.cell.0` | Sum of the corresponding two sources, same units |
| `subsurface_ecosim_source.cell.0` | Soil heat source, MW m⁻³ |
| `subsurface_ecosim_water_source.cell.0` | Soil water source, mol m⁻³ s⁻¹ |
| `surface-ecosim_source_dtemperature.cell.0` | Layer-zero heat slope, MW m⁻² K⁻¹ |
| `surface-ecosim_source_temperature_ref.cell.0` | Layer-zero paired reference temperature, K |
| `subsurface_ecosim_source_dtemperature.cell.0` | Soil heat slope, MW m⁻³ K⁻¹ |
| `subsurface_ecosim_source_temperature_ref.cell.0` | Soil paired reference temperature, K |
| `surface-temperature.cell.0` / `temperature.cell.0` | Surface / soil temperature, K |
| `surface-energy@current.cell.0` / `energy@current.cell.0` | Surface / soil cell energy, MJ |
| `surface-water_content@current.cell.0` / `water_content@current.cell.0` | Surface / soil cell water, mol |
| `diffusive_energy_flux.face.0` / `advected_energy_flux.face.0` | Conductive / advective face heat flow, MW |
| `water_flux.face.0` | Face water flow, mol s⁻¹ |

Check source sums and routing: the two component sources must sum to the total;
`surface-ecosim_source.cell.0` aliases layer-zero heat and
`surface-ecosim_water_source.cell.0` aliases layer-zero water. At top soil cell
99, multiplying the soil heat source by `V` must recover ground heat times area;
multiplying soil water source by `V` must recover ground water times area times
`rho_mol=1000/0.0180153 mol m⁻³`. This fixture has no additional root/user soil
sources, so both mapped source fields are zero below the top cell. Use absolute
tolerances `1e-10` for component/routing equalities, `1e-12` for the surface heat
alias, `1e-16` for the surface water alias and lower-cell zeros, in each field's
stated units. Coverage `surface-ecosim_layer0_fraction.cell.0` must lie in [0,1]
to `1e-12` roundoff. Zero layer-zero flux is valid when that pool is absent.
The diagnostic mirrors `surface-litter_water.cell.0`, `surface-litter_ice.cell.0`,
`surface-litter_temperature.cell.0`, and `surface-litter_heat_capacity.cell.0`
must not appear as independently checkpointed native carry state in shared mode.

## Independent heat and water balance checks

Use only pairs of checkpoints whose cycle numbers differ by one. Let `old`
denote the start and `new` the accepted end, with `dt=time_new-time_old` in
seconds. **Freeze the source, slope, and reference from `old`.** EcoSIM may have
already refreshed the raw fields in `new` for the following interval.

For each pool, reconstruct heat used over the accepted interval:

```text
q0 = q0_old + K0_old * (T0_new - T0ref_old)
qg = qg_old + Kg_old * (Tsoil_new - Tgref_old)
```

`K0` is the surface slope. Convert the soil slope to per-area `Kg` by multiplying
the top-cell volumetric slope by `V/A`. Define `H499`, `H500` as the sum of
conductive and advective heat flow at the new checkpoint, and `F499`, `F500` as
its water flow. For `A=1 m²`, test these residuals:

```text
surface energy: (E0_new-E0_old)/dt - A*q0 - H500
soil energy:    (Eg_new-Eg_old)/dt - A*qg + H500 - H499
surface water: (W0_new-W0_old)/dt - A*w0_old*rho_mol - F500
soil water:    (Wg_new-Wg_old)/dt - A*wg_old*rho_mol + F500 - F499
```

Require absolute residuals below `1e-8 MW` for energy and `1e-5 mol s⁻¹` for
water. Require at least one consecutive checkpoint pair. For a different
application, include its additional sources and actual geometry before using
these equations.

Also compare `surface-ecosim_source_applied.cell.0` to reconstructed `q0`
(`1e-10 MW m⁻²` tolerance). For the cumulative
`surface-ecosim_source_correction_energy.cell.0` ledger, require:

```text
abs(ledger_new-ledger_old - dt*(q_applied_new-q0_old)) < 1e-7 MJ m⁻²
```

Here the final `q0_old` means the **raw** old reference heat source, not the
temperature-corrected value. To exercise temperature feedback, require an
absolute surface slope above `1e-9 MW m⁻² K⁻¹` or a volumetric ground slope above
`1e-9 MW m⁻³ K⁻¹` somewhere in the tested series. Surface slopes must be at most
`1e-12 MW m⁻² K⁻¹` (nonpositive within roundoff); a bare/absent pool may have zero
slope. Do not interpret a raw next-step source as the accepted heat flux or
diagnose a nonlinear trial from it.

## Optional litter retention and storage checks

These are separate manufactured diagnostics, not changes to the baseline.
Use matching ATS and EcoSIM binaries, and operate on copies of checkpoints.
The native C ABI `ecosim_layer0_retention` takes a pointer to 14 doubles,
pressure and atmospheric pressure as double values, and output pointers for
retained depth, its pressure derivative, and an integer status. Pressure is Pa,
depth is m H₂O, and the derivative is m Pa⁻¹. The shared-library installation
provides `libATSUtils_mods.so`; inspect the pinned EcoSIM interface for the ABI.

For a forward/inverse retention check, use the property vector:

```text
[0.0008, 0.0001, 0.001,
 log(0.4), log(0.4/0.05), log(0.033), log(1.5/0.033),
 log(0.8), log(0.8/0.4), 1,
 log(0.001), log(0.033/0.001), -100, -0.001]
```

All logs are natural. For water fractions `theta` = 0.01, 0.025, 0.05, 0.1,
0.2, 0.39, 0.41, 0.5, 0.7, and 0.79, calculate native water potential (MPa):

```text
theta < 0.4: psi = -exp(log(0.033) + log(0.4/theta)/log(0.4/0.05)*log(1.5/0.033))
otherwise:   psi = -exp(log(0.001) + log(0.8/theta)/log(0.8/0.4)*log(0.033/0.001))
pressure = 101325 + 1e6*psi
```

Require status 0 and returned depth within `1e-14 m` of `0.001*theta`.
Check the positive derivative against a centered pressure difference with
`epsilon=0.1 Pa`, to relative error `1e-5`. A zero vector must give zero depth
and derivative; invalid positive-capacity/zero-volume material must return
status 1. These tests check retention and derivatives, not litter biology.

For a storage diagnostic, first generate a separate one-day fixture copy with
checkpoints at cycles 0 and 12. In copied checkpoints, set the 14
`surface-ecosim_layer0_material.cell.<j>` components to the vector above, replacing
`0.001` saturation potential with `0.0005` in components 10, 11, and 13
(zero-based). Set `surface-ecosim_layer0_thermal_resistance.cell.0=5000 m² K MW⁻¹`.
Test the following one-step variants:

| Variant | Initial checkpoint | Retained capacity | Surface pressure | Step |
| --- | --- | --- | --- | --- |
| Dry litter | Cycle 12 | Set component 0 to zero | Keep baseline | 60 s |
| Retained, frozen | Cycle 12 | Keep vector | Keep baseline | 60 s |
| Ponded, frozen | Cycle 12 | Keep vector | Atmospheric +20 Pa | 60 s |
| Retained, liquid | Cycle 0 | Keep vector | Keep baseline | 60 s |
| Ponded, liquid | Cycle 0 | Keep vector | Atmospheric +10 Pa | 1 s |

Update all surface pressure components and `pressure.face.0` at face 500
consistently. With `hret` from the native API, initialize surface inventory as:

```text
hpond = max(0, pressure-atmospheric_pressure)/(1000*9.80665)
N = (hpond+hret)*rho_mol
dT = T-273.15
u_liquid = 76e-6*dT
u_ice = (-6007.87 + (37.7841+0.131932*dT)*dT)*1e-6
E = N*u + 0.0001*dT
```

`N` is mol and `E` is MJ for this 1 m² fixture. The tested states must stay
outside the phase-transition interval, so choose the pure liquid/ice expression
according to their actual temperatures. Update current water/energy inventories
and current pond depth in the checkpoint. Reset each integrator attribute ending
`_TI_num_vectors` to zero because its old predictor history no longer matches the
manufactured state. Set `surface-ecosim_source_last_advance_time` to checkpoint
time to prevent an EcoSIM refresh during the short step. Restart to exactly
`time+step` with a prescribed schedule of that step length.

At the end, reconstruct storage from the final pressure and temperature. Require
water error below `1e-7 mol`, energy error below `1e-8 MJ`, finite numeric fields,
and end-time error below `1e-5 s`. The 60-second ponded-liquid variant is a known
failure, not an accepted diagnostic; do not increase the passing variant's step
and describe it as the same test.

## Plots and limits of the record

Basic independent plots need the cycle start times and step lengths from stdout,
plus final soil temperature and liquid/ice saturation profiles. For the generated
mesh, cell `j` has depth `10-(j+0.5)*0.1 m`; subtract 273.15 to plot temperature
in Celsius. Plot layout and graphics libraries do not affect acceptance.

To recreate full time-series figures, export the initial state and committed
accepted states after each successful step, excluding rejected nonlinear trials.
Record time, actual step length, temperatures, pond/snow depth, water phases,
and interface fluxes;
retain all 100 soil profiles with their actual times. Label reference fluxes
separately from heat used over the preceding step. Use duration-weighted means
when aggregating adaptive-step fluxes. Sparse checkpoints and the final state
alone cannot reconstruct those traces. Implement the exporter with documented
ATS observations or a read-only accepted-step hook, and record that configuration
with the data; a particular historical plot is not a mandatory baseline test.
For plots positive into soil, use `-H500/A * 1e6` in W m⁻² and
`-F500/(A*rho_mol) * 3.6e6` in mm h⁻¹.

This historical case intentionally preserves the near-hour EcoSIM clock defect.
Do not snap timestamps or fix refresh scheduling while claiming to reproduce
this reference. A corrected clock needs a separate case/reference. Successful
comparison and finite output establish numerical reproduction, not independent
observational validation. The full coupled build from an empty dependency cache
has not been validated for the packaged command; the input replay with the
existing coupled executable matched all 168 reference datasets exactly.
