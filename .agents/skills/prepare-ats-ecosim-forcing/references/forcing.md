# Forcing conversion contract

Background: [coupler draft, hourly forcing section](https://docs.google.com/document/d/1QaM7qWsRI3Zy02kxQVj_NG6fOZdKofBG9nT0EHyDLHM/edit).
The details below were checked against ATS
`aabc2fcba0949b6c9cc98195ae62e5561001f156`, specifically
`tools/utils/daymet_to_ats_ecosim.py` and `daymet_to_ats.py`.

## Execution

Required Python imports include NumPy, h5py, and requests, even when using
`--raw-file`. Check inside the selected container. The compiler image may lack
requests; on Ubuntu, provision `python3-requests` with the package manager if
needed. Do not install into an unrelated host Python environment.

Example with a pre-existing raw file and an explicitly chosen output:

```bash
python3 "$ATS_SRC_DIR/tools/utils/daymet_to_ats_ecosim.py" --help
python3 "$ATS_SRC_DIR/tools/utils/daymet_to_ats_ecosim.py" \
  38.8837 -106.95 --start 2010-01-01 --end 2010-01-03 \
  --raw-file "$RAW_FILE" --directory "$OUTPUT_DIR" -o "$OUTPUT_DIR/forcing.h5"
```

The site and dates are examples, not defaults. Without `--raw-file`, the parent
utility downloads a DayMet single-pixel text file into `--directory`. Create that
directory first. `--download-only` stops before HDF5 conversion. The checked
parent's date validator accepts no requested start before 1980-01-01, no end
after 2018-12-31, and requires end after start. These are **script restrictions**,
not a statement of the provider's current coverage. For an unsupported request,
establish a compatible converter or a documented, tested adaptation. Preserve the
original source identity; do not silently truncate the request or label modified
code as the pinned converter.

The raw-file path does not cause this converter to subset records by the CLI
dates: it converts the entire raw array. Metadata about a requested interval is
not proof of actual coverage. Inspect raw `year`/`yday`, gaps and duplicates,
actual first/last records, and output row count. If preprocessing a subset is
needed, preserve the original and record the explicit selection. A single raw
row may become a zero-dimensional NumPy record; the checked implementation
expects an array of records. Do not claim that unsupported single-row input
has been converted successfully.

## File schema

For `D` raw daily records, require `24*D` finite values in each time-series
dataset, in matching order:

| HDF5 name | Units |
| --- | --- |
| `time [s]` | Seconds from the first raw day's local-solar midnight; 0, 3600, ... |
| `air temperature [K]` | K |
| `incoming shortwave radiation [W m^-2]` | W m⁻², hourly mean |
| `vapor pressure air [Pa]` | Pa |
| `precipitation rain [m s^-1]` | m liquid water s⁻¹ |
| `precipitation snow [m SWE s^-1]` | m water equivalent s⁻¹ |
| `wind speed [m s^-1]` | m s⁻¹ |

Site/calendar values are one-element datasets, also written as attributes:
`latitude [deg]`, `longitude [deg]`, `solar noon [h]`, `start year`,
`start day of year [0-364]`, and `start hour [h]`. The PK reads the **datasets**;
attributes alone are insufficient for its file/header form. The writer declares
calendar `365_day (DayMet)` and hourly means stamped at interval starts.
Verify the actual raw day sequence, including leap-year handling and year-end
transitions; a label does not implement an arbitrary Gregorian/DST conversion.
The last timestamp is the start of the last hour, not an extra end-of-run sample.
Check the consuming evaluator's interpolation/extrapolation needs at the final
simulation query before claiming coverage.

## Physical and accounting checks

- Temperature follows the converter's diurnal curve using neighboring daily
  extrema; do not assume hourly samples must hit both extrema exactly.
- Vapor pressure is nonnegative and no greater than its saturation limit at
  hourly temperature: `610.78*exp(17.27*T_C/(T_C+237.3)) Pa`.
- Rain and snow are nonnegative. At `T_C>=0`, precipitation is rain; below zero
  it is snow. For each day, `sum((rain+snow)*3600) = prcp_mmday/1000` in meters.
- The normal shortwave target is `srad_Wm2*dayl_s` J m⁻² day⁻¹, recovered by
  `sum(shortwave*3600)`. The converter weights mid-hour sun elevation, but
  **caps** values at `1360*max(sun_sine,0)` W m⁻² if the DayMet total exceeds
  that simplified astronomical limit. On capped days report both requested
  and delivered totals and the affected dates. Do not state unconditional
  daily-total preservation. A positive-energy polar-night inconsistency also
  needs investigation, not normalization to a convenient total.
- The source checks uncapped shortwave totals with `rtol=1e-10, atol=1e-6` and
  precipitation with `rtol=1e-12` (NumPy's default absolute tolerance). Your
  validation should state its own absolute water tolerance explicitly.
- Wind is a synthetic constant 4 m s⁻¹. The parent metadata says reference
  height 2 m, while the PK default is 3 m. Resolve the intended height in the
  XML; that metadata does not automatically change the PK parameter. This
  forcing has no observed wind or independently supplied longwave dataset.

Create a read-back summary with row counts, time differences, ranges, daily
accounting errors, capping count/dates, and metadata values. Preserve all source
bytes and warnings. For a new case, a short ATS initialization/run additionally
checks that its evaluators consume the file as intended; it is separate from
the file's numerical checks.
