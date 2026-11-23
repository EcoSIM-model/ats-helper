# Reproducing the paired ATS-EcoSIM / standalone EcoSIM runs

`cases/pft_tests` holds 14 ATS-EcoSIM inputs: a bare-ground column and 13 prescribed-phenology PFT columns
(deciduous, evergreen, grass and shrub at several LAI), 729 days of hourly Daymet forcing. For each input
`tools/ats_pairs.py` writes a matched standalone EcoSIM case and a revised ATS input on the same graded
19-layer column, starting both models at 274.15 K, runs both and compares them.

`case.json` in this folder pins the model sources and the inputs; the case builds those sources, does all of the above and checks the result.

    ./reproduce.sh pft_tests                    # build (hours the first time), pair, run, verify
    ./reproduce.sh pft_tests --prepare-only     # download and verify the sources and inputs only

`JOBS=4 ./reproduce.sh pft_tests` sets the compile jobs (default 2); `PAIRS_JOBS=N` sets how many pairs run
at once (default: up to 4); `PAIRS_ONLY="name ..."` restricts a run to some inputs, to try the workflow (the
result is then reported as "not a reproduction"). The build and the runs happen in their own Docker volume,
named after the case file's hash, so they never touch the development build.

## What is pinned

| | URL | commit |
|---|---|---|
| Amanzi | `jinyun1tang/amanzi` | the Amanzi of the 200day-adaptive case |
| ATS | `AndrewGraus/ats`, branch `ecosim-pk-only` | a commit Amanzi does not record |
| EcoSIM | `EcoSIM-model/EcoSIM`, branch `agraus/standalone_match` | |

The exact hashes are in the set `ats-ecosim` of the repo-level [`sources.json`](../../sources.json), which this
case's `case.json` names (`"sources": "ats-ecosim"`); other cases can name the same set or their own. Moving a
pin means editing `sources.json` and locking the case again. Branch names only say where the commits were published; the
runner always checks out the commit, never the branch tip. Because Amanzi's recorded ATS pointer is a
different commit, `override_amanzi_gitlink` makes the runner fetch the ATS commit from its own URL
after Amanzi's submodules are initialised (Amanzi then sees its ATS pointer as moved, which is expected).
Amanzi's `TPLVersions.cmake` still names the EcoSIM ref `agraus/PrescribedPhenology`; the build script
resolves that name to the locked EcoSIM commit through a local mirror, so the name itself does not matter.

The inputs are locked too: `case.json` lists the checksum of every `*.xml` and every data file of
this folder, and of the PFT parameter file in the EcoSIM checkout. A changed, added or removed input
stops the case before anything is built. The pairs themselves are not stored: the run writes them from the
inputs inside the container (the image has the Python packages `pair` needs, and the built TPLs provide the
`exodus` wrapper of the ATS meshing tools).

## What is checked

Every pair must run to the end: the standalone run writes all 17496 hourly samples, and the ATS run
reaches day 729 with no EcoSIM `ENDRUN`, no timestep collapse and a normal exit. In addition the number of
accepted ATS steps of every pair must equal the one in `case.json` (`cycle_tolerance` 0). These counts
repeat exactly between runs on the reference platform, so a change in any pair shows up.

This does not compare fields. The run also builds the comparison report of the pairs
(`cases/pft_tests/pairs/report/index.html` under the result directory, linked from the result page); if that
step fails it is noted in `report.json` and does not fail the reproduction. To build it again, from the
container: `python3 tools/ats_pairs.py report build-logs/reproduce/pft_tests/<run>/cases/pft_tests`.

## Results

`build-logs/reproduce/pft_tests/<run>/` holds `index.html` (status, per-pair table and a link to the comparison report), `report.json`,
`verification.json`, the build logs and `cases/pft_tests/`, a copy of the case folder with the pairs and all
run outputs (several GB).

## Refreshing the lock

After changing the sources (edit the set in `sources.json`) or the inputs: run the whole case folder on a build of the new sources
(`python3 tools/ats_pairs.py all cases/pft_tests --container NAME`), then write the case again with
`docker/lock_pairs_case.py` (its docstring gives the command). It refuses to lock unless every run passed.

## Limits

An Ubuntu 24.04 ARM64, single-rank baseline; apt packages and transitive downloads are not locked.
Other platforms may change the cycle counts without the physics being wrong. The full build of the pinned
sources from an empty volume and a full run of this case have not been done; the source download and the
in-container pair generation have been tested.
