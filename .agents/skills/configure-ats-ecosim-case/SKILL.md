---
name: configure-ats-ecosim-case
description: Create or adapt an ATS input XML for EcoSIM coupling, including PK ordering, forcing/site parameters, prescribed LAI/SAI, and spatial PFT assignments. Use for configuring a new coupled column or hillslope, enabling prescribed phenology, or mapping vegetation to an existing mesh. Named baseline reproduction uses reproduce-case.
---

# Configure an ATS–EcoSIM case

Create a runnable case copy and a concise record of its inputs and validation.
Use the source checkout selected by the user; branch tips and the draft manual
are not substitutes for that revision's input contract.

1. Establish the container and source paths using the environment-selection
   procedure in [build-ats-ecosim](../build-ats-ecosim/SKILL.md). Inspect model
   source and data inside the selected Docker environment. Read applicable
   repository instructions before making model changes; XML configuration alone
   should not require changing Fortran or C++.
2. Resolve the mesh, forcing period/calendar, PFT table, vegetation inputs,
   coupling mode, and output destination from the user's request and existing
   files. Ask for missing scientific choices rather than assigning arbitrary
   plant traits, land-cover mappings, or site coordinates.
3. Read [the configuration contract](references/configuration.md). Start from
   a compatible working XML, such as a **copy** of
   [the bundled column](../../../cases/200day-adaptive/input.xml), preserving its
   physics and solver settings unless the requested adaptation requires a change.
   A new mesh still needs its own regions, boundary conditions, and soil data.
4. Configure PK tree and execution order, source consumers, site/calendar
   parameters, and phenology fields together. Inspect the actual trait table and
   mesh/entity ordering before creating PFT mappings. Use
   [prepare-ats-ecosim-forcing](../prepare-ats-ecosim-forcing/SKILL.md) when hourly
   forcing must be generated.
5. Parse the generated XML; check for duplicate parameter/evaluator definitions,
   file existence in the run container, HDF5 dataset shapes and coverage, and
   matching PFT component counts. Run a short coupled initialization/simulation
   in scratch with the selected executable when available. A syntax check or
   MPI version command alone does not establish a working coupled case.

Report the case path, source commits, input checksums, calendar/time origin,
trait-file identity and mapping, changes from the template, and checks actually
run. Mark unresolved dependencies explicitly. Keep the published case input,
its forcing bytes, reference checkpoint, and unrelated user outputs unchanged.

For failures, use [diagnose-ats-ecosim-coupling](../diagnose-ats-ecosim-coupling/SKILL.md).
Do not enable experimental microbial transport or change model equations just
to make a configuration smoke test pass.
