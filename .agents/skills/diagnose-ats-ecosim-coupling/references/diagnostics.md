# Diagnostic procedure

Background: [coupler draft and historical debugging notes](https://docs.google.com/document/d/1QaM7qWsRI3Zy02kxQVj_NG6fOZdKofBG9nT0EHyDLHM/edit).
Version context: ATS `aabc2fcba0949b6c9cc98195ae62e5561001f156`, EcoSIM
`d23a54bf1c8bc1fd6a6b89a5f84b9307e6fe2225`. The draft mixes active procedures,
speculation, and older implementations; do not treat every listed issue or fix
as the status of another revision.

## Establish the event and executable

Record executable hash, source/configuration differences, build type/toolchain,
loaded EcoSIM library paths, MPI ranks, input/forcing hashes, and restart lineage.
An up-to-date incremental target is not evidence of a clean build. A missing
Fortran `.mod` file can be a target include/dependency error even when an older
build passes with a shared `-J` directory. Preserve working builds; use a new
scratch build directory for that comparison.

From the log, capture cycle, accepted time, attempted `dt`, solver error, and
retries. A rejected 343 K trial and the last accepted surface temperature are
different evidence. A strict prescribed schedule may reject subdivision;
maximum-schedule/adaptive mode is an explicit configuration choice, not a
license to alter the baseline's timestep file.

## Build a small accepted-state trace

For the relevant interval include time/dt, air, surface and top-soil temperature,
pond depth, snow depth, liquid/ice saturation, layer-zero coverage, external
layer-zero/ground water and heat, interface conduction/advection, and solver
iterations. Keep initial state and every accepted endpoint needed to reconstruct
the intervals; do not mix an old flux with a new inventory without timestamps.

Use the [independent validation specification](../../../../cases/200day-adaptive/REPRODUCE.md)
for exact field names, signs, reference-plus-slope reconstruction, and numerical
limits for its column. For other meshes, obtain actual area/volume and signed
face incidence, and include any additional source terms. State explicitly if
the available output is too sparse for an interval budget.

## Trace the physical paths

- Check `EcoSIM_ATS_interface.cc` source routing and native
  `ATSEcoSIMAdvanceMod.F90`/`ATSLayer0Mod.F90` producers. Shared mode separates
  direct soil exchange from pond/litter exchange. Verify the heat and water
  terms enter their own receivers once, and ATS's interface transfer is equal
  and opposite between its stores.
- Quantify pond and soil apparent/sensible heat capacities, ice/latent energy,
  coverage, interface conductance, and the actual adaptive cap before explaining
  a temperature swing. A small pond can respond rapidly; a ratio alone is not
  proof of a stabilization defect.
- Use `ATSStateRegistryMod.F90` to distinguish native snow/canopy carried state
  from OUTPUT diagnostics. Canopy longwave/latent/sensible carry values are not
  automatically the accepted ATS surface heat flux. In shared mode, native
  layer-zero temperature/water mirrors do not constitute a second conserved pool.
- For snow/phenology, check LAI/SAI, trait row IDs, active slots, radiation
  partition, albedo inputs, and weather time origin before changing parameters.
  Do not copy experimental root resistances or raise canopy thresholds from
  historical notes without evidence for the selected case.

## Time, restarts, and parallel comparisons

The checked `200day-adaptive` reference intentionally retains a near-hour clock
defect: raw floating-point seconds are truncated for the native hour and refresh
eligibility uses an exact elapsed-hour comparison. Check actual calendar labels
and refresh times/counts; a nanosecond perturbation can change a solar hour.
Do not silently fix the clock while comparing against that historical reference.

For refinement, use the same physical checkpoint, align native refresh times
and forcing samples, state the warm-up/comparison window, and compare common
times. Integrate fluxes with actual interval lengths. Hourly means need
duration weights; cumulative radiation diagnostics need increments before
interpreting them as rates. A daily thermal cycle persisting under refinement
does not validate its observed physical amplitude.

For restart tests, preserve clocks, paired source/slope references, integrator
history, and native PRIVATE state. Compare a continuous run with a split run
under the same input; do not clear history as a routine restart fix. Resetting
history is appropriate only for a separately labelled manufactured checkpoint.
For MPI, trace owned local column IDs and reduction/halo behavior and compare
global budgets and matching physical fields across decompositions. Two-rank
`--version` startup alone does not test coupled physics or concurrent I/O.

Historical microbe/transport plans are not a validated capability statement.
The checked native initialization disables microbial and soil chemistry models.
Report missing functionality or an unsupported configuration rather than
diagnosing its absent physics as a successful model response.
