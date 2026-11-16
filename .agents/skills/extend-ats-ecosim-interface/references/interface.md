# Interface change map

The [coupler draft](https://docs.google.com/document/d/1QaM7qWsRI3Zy02kxQVj_NG6fOZdKofBG9nT0EHyDLHM/edit)
motivates this workflow. This map is checked against ATS
`aabc2fcba0949b6c9cc98195ae62e5561001f156` and EcoSIM
`d23a54bf1c8bc1fd6a6b89a5f84b9307e6fe2225`; inspect the selected revision first.

## Choose the owner and lifetime

| Container / registry | Role |
| --- | --- |
| `EcoSizes` | Logical dimensions |
| `EcoConfig` | ATS → EcoSIM, setup configuration |
| `EcoEnvironment` | ATS → EcoSIM, supplied state/geometry/forcing at setup and advance |
| `EcoFeedback` | EcoSIM → ATS, sources and other physics feedback |
| `EcoInternalState` / `ATSStateRegistryMod` | Native carried state and diagnostic outputs stored by ATS |

Distinguish a property sent once from a state supplied every advance. An
externally prescribed field should not become an independently advanced native
store. In shared-layer mode, ATS owns pond/litter conservation; the native
layer-zero water/temperature mirrors derive fluxes. Native snow/canopy carry
state has a different owner and restart requirement.

## Files to inspect and update when relevant

Paths in the first group are relative to ATS:

| File | Responsibility |
| --- | --- |
| `src/pks/ecosim/EcoSIM_ATS_interface.hh` / `.cc` | Keys, requirements/evaluators, time tags, ATS↔column transfer, source application and units |
| `src/pks/ecosim/data/EcoContainers.hh` | C-compatible structs and field contracts |
| `src/pks/ecosim/data/EcoMemory.hh` / `.cc` | Allocation sizes, initialization, matching release |
| `src/pks/ecosim/ecosim_interface.h`, `EcoEngine.hh` / `.cc` | Native entry points and engine calls when signatures change |
| `src/pks/ecosim/data/EcoContainers.cc` | Interface function table |

The corresponding EcoSIM paths are under `f90src/ATSUtils/`:

| File | Responsibility |
| --- | --- |
| `EcoContainers.F90` | Mirrored interoperable `bind(c)` layouts |
| `ATSCPLMod.F90` | Configuration mapping, pointer association, input conversion |
| `SharedDataMod.F90` | Native pointers/carry data where needed |
| `ATSEcoSIMInitMod.F90` / `ATSEcoSIMAdvanceMod.F90` | Actual use at initialization/advance and feedback construction |
| `ecosim_wrappers.F90`, `c_f_interface_module.F90` | Exported wrappers, size/layout query, interoperation helpers |
| `ATSStateRegistryMod.F90` | State metadata, dimensions/roles, pack/unpack, checkpoint layout version |
| `ATSLayer0Mod.F90` | Shared surface state/material API when that exchange is affected |

Do not apply the draft's older `BGC_containers`/`BGC_memory` filenames blindly.
Adding a diagnostic through the registry may avoid adding a parallel ABI field.
Update CMake source lists/module include dependencies for new modules and verify
a clean model build: a shared module directory can hide a missing include path.

## Layout, ownership, and conversion checks

- Preserve corresponding C and Fortran member order, types, and alignment.
  `integer(c_int)`, `real(c_double)`, `logical(c_bool)`, and `type(c_ptr)` must
  match their C types; a native default integer/logical is not a portable ABI.
- Check `value` versus reference arguments on exported procedures. Never
  "double dereference" pointers as a speculative fix for a bad shape.
- Matrices currently use logical offset `column*cells+cell`, with Fortran
  shape `(cells, columns)`. Allocation capacities may be rounded upward; do not
  substitute capacity for logical stride. Inspect tensor/PFT offsets at each
  call site instead of inferring them from one matrix example.
- Map owned local columns and actual layer ordering; global cell IDs are not
  local array indices. PFT buffers and subsurface-layer buffers need separate
  dimension reasoning even where the legacy interface uses a common stride.
- Pair every allocation with a release and defined absent/zero-size behavior.
  Do not use a pointer after its owner is freed or a temporary string expires.
- State explicitly where hours become seconds, W becomes MW, native volumes
  become water depth/moles, or per-area sources become per-volume sources.
  Apply each conversion and each internal interface exchange once. For the
  checked shared coupling, surface heat is MW m⁻², surface water is m s⁻¹,
  soil heat is MW m⁻³, and soil water is mol m⁻³ s⁻¹.
- Keep a temperature slope paired with its source reference and temperature
  reference at the same refresh point. New atmospheric feedback must not
  duplicate ATS infiltration/advection/conduction between its own pools.

## State and evidence

`ATSStateRegistryMod.F90` distinguishes `kRolePrivate` (unpacked and checkpointed)
from `kRoleOutput` (diagnostic, not unpacked as carry state). Register dimensions,
units, and description; bump `kLayoutVersion` when its checkpoint layout changes.
Inspect ATS setup and pack/unpack together. State must belong to the correct
time tag, with failed-step rollback and restart behavior accounted for.

`ecosim_container_sizes()` guards C/Fortran sizes at setup. Also use distinguishable
sentinels by field, rank, column, layer, and PFT slot to catch same-size field
swaps, transposes, reversed layers, and bad offsets. Include one/multiple columns
and slots, missing/zero-owned data when supported, and round-trip versus
one-way behavior. Test with the actual shared libraries that will be loaded,
not just objects in a different build tree.

For a source change, check receiver units/signs and conservation over accepted
steps; for carried state, compare uninterrupted and split/restarted runs. The
[fixture specification](../../../../cases/200day-adaptive/REPRODUCE.md) gives
concrete field/budget tests, but its fixed mesh indices do not generalize to
other cases. Matching total struct size or a successful version command is
insufficient validation.
