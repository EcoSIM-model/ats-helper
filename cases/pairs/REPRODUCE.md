# Reproducing the paired ATS-EcoSIM / standalone EcoSIM runs

`cases/pft_tests` holds 14 pairs: a bare-ground column and 13 prescribed-phenology PFT columns
(deciduous, evergreen, grass and shrub at several LAI), each run with ATS-EcoSIM and with standalone
EcoSIM on the same graded 19-layer column and 729 days of hourly Daymet forcing. The pairs use the
`matched-start` profile: both models start at 1.00 C and the ATS bottom temperature is 274.429 K
(initial temperature + 0.28 K, EcoSIM's deep source temperature).

This case builds the model sources recorded in `case.json`, runs every pair and checks the result.

    ./reproduce.sh pairs                    # build (hours the first time), run, verify
    ./reproduce.sh pairs --prepare-only     # download and verify the sources and inputs only

`JOBS=4 ./reproduce.sh pairs` sets the compile jobs (default 2); `PAIRS_JOBS=N` sets how many pairs run
at once (default: up to 4). The build and the runs happen in their own Docker volume, named after the
case file's hash, so they never touch the development build.

## What is pinned

| | URL | commit |
|---|---|---|
| Amanzi | `jinyun1tang/amanzi` | the Amanzi of the 200day-adaptive case |
| ATS | `AndrewGraus/ats`, branch `ecosim-pk-only` | a commit Amanzi does not record |
| EcoSIM | `EcoSIM-model/EcoSIM`, branch `agraus/standalone_match` | |

The exact hashes are in `case.json`. Branch names only say where the commits were published; the
runner always checks out the commit, never the branch tip. Because Amanzi's recorded ATS pointer is a
different commit, `override_amanzi_gitlink` makes the runner fetch the ATS commit from its own URL
after Amanzi's submodules are initialised (Amanzi then sees its ATS pointer as moved, which is expected).
Amanzi's `TPLVersions.cmake` still names the EcoSIM ref `agraus/PrescribedPhenology`; the build script
resolves that name to the locked EcoSIM commit through a local mirror, so the name itself does not matter.

The suite's inputs are locked too: the checksums of `suite.json`, the forcing, the ATS templates and
one checksum over all generated pair inputs (`pair_inputs_sha256`). A changed input stops the case before
anything is built. The committed inputs name the PFT parameter file through the developer's checkout
(`repos/dev/ecosim`); the run replaces that path, in its own copy of the inputs, by the locked EcoSIM
checkout and checks the file's checksum.

## What is checked

Every pair must run to the end: the standalone run writes all 17496 hourly samples, and the ATS run
reaches day 729 with no EcoSIM `ENDRUN`, no timestep collapse and a normal exit. In addition the number of
accepted ATS steps of every pair must equal the one in `case.json` (`cycle_tolerance` 0). These counts
repeat exactly between runs on the reference platform, so a change in any pair shows up.

This does not compare fields. To judge a run scientifically, build the comparison report from its copy of
the suite (the result page names the directory):

    python3 tools/ats_pairs.py report build-logs/reproduce/pairs/<run>/cases/pft_tests/suite.json

## Results

`build-logs/reproduce/pairs/<run>/` holds `index.html` (status and per-pair table), `report.json`,
`verification.json`, the build logs and `cases/pft_tests/`, a copy of the suite with all run outputs
(several GB for the full suite).

## Refreshing the lock

After changing the sources or the inputs: regenerate the pairs if needed
(`python3 tools/ats_pairs.py pairs cases/pft_tests/suite.json --profile matched-start`), run both backends
of the whole suite on a build of the new sources, then write the case again with `docker/lock_pairs_case.py`
(its docstring gives the commands). It refuses to lock unless every run passed.

## Limits

An Ubuntu 24.04 ARM64, single-rank baseline; apt packages and transitive downloads are not locked.
Other platforms may change the cycle counts without the physics being wrong.
