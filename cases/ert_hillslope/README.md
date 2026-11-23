# ert_hillslope: six columns on a slope, trees upslope and grass downslope

`ERT6.namelist` is EcoSIM's `examples/run_dir/ERT_SatePhenol/ERT6.namelist` with its inputs copied into
`data/`. It describes one row of six 40 m columns (240 m long, 40 m wide) facing east, with a slope per column and
a plant type per column:

| column | PFT | slope | surface drop over the column [m] | LAI over the year |
|---|---|---|---|---|
| 1–4 | `ndlf43` (needleleaf tree) | 47° | 42.9 each | 1.16 – 1.43 |
| 5 | `gr3s43` (C3 grass) | 16° | 11.5 | 0.05 – 2.5 |
| 6 | `gr3s43` (C3 grass) | 12° | 8.5 | 0.05 – 2.5 |

The surface elevation is integrated from the slopes as EcoSIM does, which gives about 190 m of relief; the soil
is identical in every column (10 layers over 1.7 m, bulk density 0.7 – 1.52, a horizontal water table 0.25 m below
the lowest column).

## What differs from the EcoSIM example

* File paths are relative to this folder; the PFT and GHG files are looked up by name in the EcoSIM `input_data`.
* `data/ERT_grid_20250826_fcwp.nc` is the example's grid file with the field capacity and the wilting point of
  layers 1 – 10 written out (they are `-1` in the original: EcoSIM computes them from texture and bulk density).
  The values are the ones EcoSIM derived (the `FieldCapacity` and `WiltPoint_vr` of a restart file of the
  namelist), so the standalone run is the same; the ATS retention curve of each layer is fitted to them.

## Running it

```bash
python3 tools/ats_pairs.py pair   cases/ert_hillslope --container NAME   # standalone case and ATS input from the namelist
python3 tools/ats_pairs.py run    cases/ert_hillslope both --container NAME
python3 tools/ats_pairs.py report cases/ert_hillslope --container NAME   # pairs/report/ERT6.html
```

(or `all`). `pair` writes `pairs/ERT6/`: the ATS mesh, the forcing, `ats_match.xml`, and the standalone
namelist, grid and phenology; `pairs/README.md` lists what is matched and what is not. The run is the first
two forcing years of the namelist's climate file as 365-day years (729 days, `--days`), not the namelist's four
model years, because ATS has a 365-day calendar and the standalone's leap years would shift the comparison.

## What the coupling needed

The slope of every ATS surface cell and its aspect reach EcoSIM (`SetGroundSlopeAspect` in `SharedDataMod.F90`;
the ATS PK updates the derived fields before EcoSIM's initialization). Without them EcoSIM-in-ATS computes the
solar geometry of a flat surface, which is wrong by a large factor on a 47° slope.
