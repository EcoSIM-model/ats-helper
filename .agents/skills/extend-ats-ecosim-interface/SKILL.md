---
name: extend-ats-ecosim-interface
description: Add or change a variable exchanged between ATS C++ and EcoSIM Fortran, including ABI layout, allocation, mapping, units, field ownership, and restart registration. Use for coupler-development requests such as passing a new surface or soil variable; ordinary XML setup uses configure-ats-ecosim-case.
---

# Extend the ATS–EcoSIM interface

Implement the requested exchange across both source repositories, with a
documented direction, lifetime, units, shape, ownership, and validation result.
Read [the interface change map](references/interface.md) before editing.

1. Select the intended Docker/source environment using
   [build-ats-ecosim](../build-ats-ecosim/SKILL.md). Read applicable `AGENTS.md`
   files in each repository and inspect dirty/staged changes before edits.
2. Trace the closest existing variable through ATS state, native environment or
   feedback, memory allocation, C/Fortran association, and its actual consumer.
   Confirm whether the requested behavior belongs in XML, a diagnostic registry,
   or the public ABI. Do not add a redundant stored copy of an existing field.
3. Write the field contract before changing layout: provider and receiver,
   intensive versus extensive quantity, units and sign, mesh/column/PFT shape,
   time tag, refresh point, default/absent behavior, and restart/rollback owner.
   Update the relevant paths on both sides together.
4. Review actual C struct and Fortran `bind(c)` member order, scalar kinds,
   pointer/value rules, allocation/free pairs, logical dimensions, flattening,
   and unit conversions. Use the existing size guard, but do not treat matching
   byte sizes as proof of matching offsets or semantics.
5. Build both changed components against matching sources. Run a focused
   sentinel/round-trip check and a short physics case that exercises the new
   exchange. Add restart testing for carried state; add multi-column/PFT/rank
   checks when those dimensions are affected. Keep existing reference data
   unchanged and report configurations not exercised.

Return the implementation paths in each repository, source diff summary,
field contract, build identity, tests and conservation/restart consequences.
An ABI or checkpoint-layout change must be stated explicitly. Do not silently
combine a newly built ATS with an older installed EcoSIM library. Commit or
publish model changes only when requested.
