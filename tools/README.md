# ATS-EcoSIM pairs tooling

Generates the inputs of a suite of ATS-EcoSIM tests, matched standalone EcoSIM cases, runs both with a
watchdog, and writes a comparison report. A suite is described by one manifest, for example
[`cases/pft_tests/suite.json`](../cases/pft_tests/suite.json).

    python tools/ats_pairs.py inputs    SUITE [--profile dev-pk] [--only T ...] [--out DIR] [--dry-run]
    python tools/ats_pairs.py pairs     SUITE [--profile dev-pk] [--only T ...] [--population G=V ...] [--dry-run]
    python tools/ats_pairs.py run       SUITE ats|ats-matched|standalone [--jobs N] [--only T ...] [--container NAME]
    python tools/ats_pairs.py summarize SUITE ats|ats-matched|standalone [--only T ...] [--container NAME]
    python tools/ats_pairs.py report    SUITE [--ats-kind matched|suite] [--only T ...] [--check]

| command | what it does | needs |
|---|---|---|
| `inputs` | `input/<test>.xml` from the template, the sweep's edits and an optional profile | standard library |
| `pairs` | `pairs/`: standalone grid, climate, phenology and namelist per test, the graded exodus column, matched ATS variants `pairs/<test>/ats_match.xml`, and a README of what is and is not matched | numpy, h5py, netCDF4, the ATS meshing tools |
| `run` | runs the tests (ATS or standalone), notes failures, starts the next test | standard library |
| `summarize` | re-evaluates finished runs from their logs, runs nothing | standard library |
| `report` | `pairs/report/index.html` (figures embedded), `figures/*.png`, `report.json` | numpy, h5py, netCDF4, matplotlib |

`run` and `summarize` use only the standard library, so with `--container NAME` they run inside that Docker
container (the workspace is `/ats-helper` there) where `ats` and the standalone EcoSIM executable are, while
`inputs`, `pairs` and `report` run on the host. The dev container has no `netCDF4`.

## What `run` checks

A run is **stopped and noted** when its timestep falls below `--min-dt-days` (ATS only), or at the wall-clock
or log-size limit. It **passes** only if it reached its end time (ATS: the last step line; standalone: an
hourly history sample for every hour), exited normally and has no EcoSIM `ENDRUN`. EcoSIM exits with code 0
when it aborts, so the exit code alone is not trusted. A failed run is reported with the last error text of its
log (for example a missing file or parameter). Each run directory gets `status.txt`; the suite gets a summary
file (`suite_summary.txt`, `pairs/ats_match_summary.txt`, `pairs/standalone_summary.txt`), written when the whole
suite is processed.

## The manifest

A JSON file next to the suite's `templates/` and `data/`. Unknown keys are errors.

| key | meaning |
|---|---|
| `name`, `description` | |
| `ats.template` | the ATS input every sweep test is made from (path relative to the manifest) |
| `ats.baseline` | `{"name", "template"}`: one more test, the template as it is |
| `ats.forcing`, `ats.data_dir`, `ats.data_prefix` | hourly forcing file; the directory the templates call `data_prefix` (default `../data/`), rewritten relative to each run directory |
| `name_pattern` | test name, filled from the sweep entry; numbers become `1`, `0p5` (`pft_{group}_lai{lai}`) |
| `sweep` | entries with a `group`; a list-valued field expands into one test per value |
| `xml_edits` | edits of the template for each sweep test (below) |
| `profiles` | named edit lists applied with `--profile NAME` (for example the settings of the development build) |
| `standalone` | `pft_file` (relative to the EcoSIM tree), `years`, `layers`, `top_dz`, `particle_density`, `soil_albedo`, `wind_height`, `history_fields`, templates; the defaults are in `manifest.py` |
| `compare.thresholds` | `"quantity": [green, amber]` overriding the report's defaults |

An edit names a parameter by the lists that contain it, joined by `/` (`**` stands for any number of lists):

    {"set": "state/evaluators/surface-LAI/**/function-constant/value", "value": "{lai}"}
    {"set": "PKs/EcoSIM for ATS/engine input file", "value": "{pft_file}", "relative_to_run_dir": true, "max_length": 128}
    {"add_after": "PKs/EcoSIM for ATS/engine", "params": [{"name": "shared surface layer", "type": "bool", "value": false}]}

Edits change the text of the file only where they say, so comments and formatting are kept. A path must match
exactly one parameter, otherwise the edit fails and names the path. `{name}` placeholders come from the sweep
entry, plus `name` and `pft_file`. Values are written according to the parameter's own type.

## Adding a suite

1. Put the template(s), forcing and the manifest in a directory (`cases/<suite>/`).
2. List the sweep and the edits; run `inputs --dry-run`, then `inputs`.
3. `run <suite> ats`, then, for a comparison, `pairs`, `run <suite> ats-matched`, `run <suite> standalone`, `report`.

## What is still specific to the PFT suite

The package is generic in how a suite is described, edited, run and summarized. These parts still assume the
PFT suite's layout and are the next candidates to move into the manifest:

- the **pairs** step reads fixed parameter paths of the ATS input (`surface-LAI`, `base_porosity`,
  `WRM parameters/mineral`, the generated mesh, ...), needs a sweep with `pft`, `vegetation_type`, `population`
  and a numeric axis, and builds one column with one PFT from EcoSIM's Blodget templates;
- the **report** compares a fixed set of quantities from `surf_prop.dat` and the EcoSIM history, aligned by
  an empirical shift, with the thresholds above; ATS observation names and the standalone variable mapping are
  in the code;
- the "not matched" list the `pairs` step writes is partly typed in the code: it was checked against the current
  coupled code (wind height, soil albedo, FC/WP and root-uptake constants, plant state), not all of it. The
  geothermal-flux and thermal-property statements are inherited and not re-checked.

Tests: `python -m unittest tests.test_ats_ecosim_pairs` (the XML edits, manifest, rendering against the
committed inputs, run classification with fake executables; no model is run).
