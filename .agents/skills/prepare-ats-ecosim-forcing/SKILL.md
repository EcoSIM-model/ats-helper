---
name: prepare-ats-ecosim-forcing
description: Prepare and validate hourly DayMet-derived HDF5 forcing and site/calendar metadata for ATS–EcoSIM, using the selected ATS converter or a documented compatible equivalent. Use for hourly weather preparation or converting existing DayMet text; reproducing a locked case must preserve its bundled forcing.
---

# Prepare hourly ATS–EcoSIM forcing

Produce the forcing HDF5, retain its raw input and provenance, and report the
actual temporal coverage and validation results. For a locked published case,
reuse its data instead of regenerating it.

1. Resolve latitude, longitude, requested interval, raw-data source, time/calendar
   convention, and destination from available case inputs. Ask for missing site
   choices. Do not substitute UTC, local civil time, or measured wind for the
   converter's local-solar assumptions without an explicit transformation.
2. Use the Docker/source selection in
   [build-ats-ecosim](../build-ats-ecosim/SKILL.md). Locate
   `$ATS_SRC_DIR/tools/utils/daymet_to_ats_ecosim.py` and its imported
   `daymet_to_ats.py`. Read their current CLI and date validation; do not download
   a different branch's script just to obtain a newer-looking interface.
3. Read [the conversion and validation contract](references/forcing.md).
   Verify Python dependencies and invoke `--help` in the execution environment.
   At the checked revision, latitude/longitude are **positional arguments**;
   the draft's `--lat`/`--lon` command is incompatible.
4. Prefer the user's existing raw file when appropriate. Preserve it unchanged;
   use a separate output directory and avoid overwriting existing forcing.
   Retain the raw request/response identity and checksum for downloaded data.
5. Read the generated HDF5 back and check schema, dimensions, finite values,
   hourly spacing, raw-data coverage, daily water/shortwave accounting, and site
   metadata. Report any shortwave capping and synthetic-wind assumptions rather
   than silently accepting them as measured data or exact conservation.

Return raw/HDF5 paths and hashes, converter/source revision and command, actual
first/last record and supported simulation coverage, calendar, assumptions, and
checks performed. Supply the exact XML `file`/`header` names needed by
[case configuration](../configure-ats-ecosim-case/references/configuration.md).
Do not change solver controls, production clock code, or an existing reference
checkpoint while preparing weather data.
