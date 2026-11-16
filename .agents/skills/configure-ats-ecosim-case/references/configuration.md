# Configuration contract

Adapted from the [coupler draft](https://docs.google.com/document/d/1QaM7qWsRI3Zy02kxQVj_NG6fOZdKofBG9nT0EHyDLHM/edit).
Checked against ATS `aabc2fcba0949b6c9cc98195ae62e5561001f156` and EcoSIM
`d23a54bf1c8bc1fd6a6b89a5f84b9307e6fe2225`. Recheck readers on other revisions.
Paths below are relative to the ATS or EcoSIM repository root.

## Reader and PK structure

Inspect ATS `src/pks/ecosim/EcoSIM_ATS_interface.cc`: parameter parsing,
`parseParameterList`, `Setup`, initialization, and transfer routines. In EcoSIM,
check `f90src/ATSUtils/ATSEcoSIMInitMod.F90` and `ATSEcoSIMAdvanceMod.F90`.

The bundled case puts the permafrost surface/subsurface MPC before `EcoSIM for
ATS` under a top-level weak MPC. The permafrost MPC contains surface/subsurface
flow and energy PKs. Preserve both the PK-tree structure and the corresponding
MPC execution-order parameters; adding a PK name in only one place is incomplete.
The template is a bare column, not a ready-made vegetated hillslope.

Preserve `primary variable key=hydraulic_conductivity` from the working template
unless the selected implementation requires otherwise. A returned EcoSIM source
should not be reassigned as its primary variable based on an older draft example.

## Exact parameter spellings at the checked revision

| XML name | Contract |
| --- | --- |
| `engine` | `EcoSIM` |
| `engine input file` | Readable trait NetCDF path for prescribed phenology; the bare template's `Null` is not a trait table |
| `prescribe phenology` | Boolean; default false; native initialization sets both `plant_model` and `ldo_sp_mode` from this flag |
| `number of pfts` | Number of local PFT slots per column, not the number of rows in the trait file |
| `EcoSIM precipitation` | Case-sensitive boolean; choose total precipitation versus separate rain/snow consistently with its reader |
| `prescribe snow albedo` | Boolean; when enabled, supply the selected snow-albedo field |
| `initial time step` | Seconds; the working template uses 3600 |
| `field capacity [Mpa]`, `wilting point [Mpa]` | Negative pressures in MPa; preserve calibrated values |
| `heat capacity [MJ mol^-1 K^-1]` | Preserve the intended model value and units |
| `wind speed reference height [m]` | Must match the interpretation of the forcing, not an unrelated default |
| `soil surface roughness height [m]` | Aerodynamic soil roughness |
| `shared surface layer` | Default true for the checked implementation |
| `semi-implicit surface heat exchange` | Default true |

An XML string containing `${VARIABLE}` is not evidence of shell expansion.
Resolve file paths in the actual execution environment and verify the reader.

Site parameters accept scalar numeric Parameters or a same-named ParameterList
with string `file` and `header` entries. The file header names differ from some
XML parameter names:

| XML parameter/sublist | HDF5 dataset header |
| --- | --- |
| `starting year` | `start year` |
| `starting day of year [0-364]` | `start day of year [0-364]` |
| `starting hour [h]` | `start hour [h]` |
| `latitude [degrees]` | `latitude [deg]` |
| `solar noon [h]` | `solar noon [h]` |

Year and zero-based day are required. Hour, latitude, and solar noon default to
0, 40, and 12 respectively; these defaults are not site measurements. Do not
copy the draft's scalar `starting hour` or `latitude [deg]` spellings into this
revision. Use one authoritative value for each parameter, not both forms.

## Phenology and spatial PFT assignments

Provide `surface-LAI`, `surface-SAI`, and `surface-vegetation_type` as cell fields
on the surface mesh, with `number of pfts` components each and identical slot
ordering. The checked C++ setup also reads these fields for the bare template,
so retain its zero-valued fields when prescribed phenology is off.

LAI/SAI are dimensionless and nonnegative. Vegetation IDs are categorical integer
values, even when stored in ATS double-valued fields. Inspect the selected NetCDF
table's actual dimensions and name/code arrays, then verify each active one-based
row index. The checked `ecosim_pftpar_20261021.nc` has an `npfts` dimension of 83;
other files can differ. Do not equate CLM vegetation codes, root-profile classes,
land-cover categories, and trait-table row indices.

EcoSIM `f90src/Mesh/GridConsts.F90` sets `JP=5` at this revision. Check that limit
and the C/Fortran allocation/stride contract before using multiple slots. The
first PFT slot being zero triggers the bare-column path in the checked advance
routine: do not put an active later slot behind a zero first slot. Treat changes
to PFT identity across time or restart as a state-consistency question; time-varying
LAI/SAI does not by itself prove arbitrary trait-row switching is supported.

For a map, verify region names, overlap/uncovered cells, cell/entity IDs, and
mesh partition correspondence. A raster's row order is not ATS surface-cell
order. For a file evaluator, inspect the selected evaluator's HDF5 contract and
component ordering before writing data. Do not interpolate categorical PFT IDs
into fractional values. The draft's Snodgrass table is a site-specific proposal;
its XML even assigns a deciduous block to `Barren Land`. Do not copy it as a
universal mapping. Read any generated mapping back and summarize counts per ID.

## Sources and diagnostics

The current EcoSIM PK owns returned sources through primary evaluators. Do not
declare independent constant evaluators for `surface-ecosim_source` or
`surface-ecosim_water_source`; older examples doing so conflict with this owner.
Keep their flow/energy consumers enabled. Surface water uses meters per second
with `water source in meters=true`; heat sources use MW m⁻², not W m⁻².
Shared mode routes separate layer-zero and direct-to-soil sources and adds the
soil contributions once. Do not manually add a second copy or reinstate native
layer-zero infiltration alongside ATS exchange.

Inspect `ATSStateRegistryMod.F90` for diagnostic names, units, and PRIVATE versus
OUTPUT roles. Do not assign independent initial conditions to every native
carried state. `visualize ecosim state` accepts registered OUTPUT names, not an
arbitrary Fortran identifier. In shared mode the litter/pond mirror fields are
OUTPUT, not independent native stores. The checked native initialization forces
microbial and soil chemistry models off; `microbe model=true` alone is not a
validated transport/BGC application.

Use the [independent validation specification](../../../../cases/200day-adaptive/REPRODUCE.md)
for field units, source signs, and accepted-step checks. Its cell/face indices
and numerical budgets are specific to its serial column fixture.
