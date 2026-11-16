---
name: diagnose-ats-ecosim-coupling
description: Diagnose ATS–EcoSIM runtime failures or suspicious pond/soil temperatures, heat/water fluxes, snowmelt, timing, restart, or parallel behavior using source traces and accepted-state evidence. Use for scientific coupling troubleshooting; building, preparing a new case, and replaying a named baseline have separate skills.
---

# Diagnose an ATS–EcoSIM coupling problem

Identify the failing interval or disputed result, establish evidence for its
cause, and report an actionable next check or a verified fix within the user's
requested scope. Use [the diagnostic procedure](references/diagnostics.md).

1. Select and verify the intended container, build, source revisions, loaded
   libraries, input, forcing, and restart. Follow the environment selection in
   [build-ats-ecosim](../build-ats-ecosim/SKILL.md). Distinguish a new issue from
   historical results in the draft or local notes.
2. Locate the first failure, last accepted state, attempted timestep and any
   retries. Separate accepted outputs from nonlinear trial guesses and sources
   already refreshed for the next interval. Do not assign a cause to EcoSIM
   from a temperature warning alone.
3. Trace the receiving balance and its actual source/flux producers, including
   units, signs, area/volume mapping, phase/storage terms, and derivative/reference
   timing. Read the selected native state registry before interpreting a
   diagnostic as a conserved store or a physical ATS flux.
4. Check time/calendar alignment and restart ownership before changing physics.
   For timestep sensitivity, hold physical initial state and EcoSIM exchange
   events fixed so forcing-phase changes do not masquerade as convergence error.
5. Run the smallest useful controlled experiment in a scratch copy. Vary one
   relevant factor, preserve logs, and state its limits. Do not change production
   inputs, solver bounds/tolerances, source references, or gold results merely
   to obtain a pass. A requested code fix needs matching rebuild and validation.

Return a table or concise trace around the interval, the first concrete failure,
confirmed cause versus remaining hypotheses, checks performed, and artifact paths.
Report whether the evidence establishes completion, numerical reproduction,
conservation, or physical accuracy; those are different claims. Avoid declaring
a general solver or model fix from one successful smoke test.
