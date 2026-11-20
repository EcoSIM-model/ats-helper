"""Automated comparison report: ATS-EcoSIM against standalone EcoSIM for the tests of a suite.

Reads, for each vegetated test of the case folder that has both:
  ATS        pairs/<test>/ats/        surf_prop.dat (hourly), ats_vis_data.h5 (daily soil profiles)
  standalone pairs/<test>/standalone/ the EcoSIM history files
and writes pairs/report/index.html (self-contained, figures embedded), pairs/report/figures/*.png
and pairs/report/report.json (all metrics).

Per test: time series of transpiration, canopy evaporation, snow depth, temperatures, ground heat
flux and cumulative transpiration; daily scatter plots, the summer diurnal cycle and monthly totals;
totals, correlations, snow seasons, temperature bias/RMSE and the health of the ATS run. Across
tests: totals, correlation, snow and temperature differences against the sweep axis (LAI) for each group.

The agreement thresholds (DEFAULT_TOL below) are
placeholders for "ballpark": green = within the first, amber = within the second, red = outside. They
grade the matching of the two models, they are not a statement about either model's correctness.

Needs numpy, h5py, netCDF4 and matplotlib (the host Python; the dev container lacks netCDF4).
"""

import base64
import datetime
import html
import io
import json
import re
import sys
from pathlib import Path

import h5py
import matplotlib
import netCDF4
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# set by configure() from the case folder
PAIRS = META = SUITE_DIR = None
AXIS_LABEL = "LAI"
NDAYS = 729
HOURS = NDAYS * 24
PAD = 3                                   # padding of the ATS hourly arrays for alignment shifts
MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

# Agreement thresholds: (green, amber). kind: ratio |ATS/SA - 1| <=, min value >=, max value <=.
DEFAULT_TOL = {
    "transp_ratio":    ("ratio", 0.25, 0.50, "transpiration total, ATS/SA"),
    "transp_corr":     ("min", 0.90, 0.70, "transpiration daily correlation"),
    "canopy_ratio":    ("ratio", 0.25, 0.50, "canopy evaporation total, ATS/SA"),
    "canopy_corr":     ("min", 0.90, 0.70, "canopy evaporation daily correlation"),
    "snow_peak_ratio": ("ratio", 0.15, 0.30, "peak snow depth, ATS/SA"),
    "melt_out_days":   ("max", 7.0, 21.0, "first-season melt-out, |ATS - SA| [d]"),
    "top_cell_rmse":   ("max", 2.0, 5.0, "top soil cell temperature RMSE, year 2 [K]"),
    "skin_rmse":       ("max", 3.0, 6.0, "ATS skin vs standalone top layer temperature RMSE, year 2 [K]"),
}
DEFAULT_COLORS = {"deciduous": "#2ca02c", "evergreen": "#1f77b4", "grass": "#d62728", "shrub": "#9467bd"}
TOL = dict(DEFAULT_TOL)
COLORS = dict(DEFAULT_COLORS)
ATS_C, SA_C = "#1f77b4", "#d95f02"


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def read_dat(path):
    names, rows = [], []
    for line in open(path):
        if line.startswith("# Observation Name:"):
            names.append(line.split(":", 1)[1].strip())
        elif not line.startswith("#") and not line.startswith('"') and line.strip():
            rows.append([float(v) for v in line.split(",")])
    a = np.array(rows)
    return {n: a[:, i] for i, n in enumerate(names)}


def hourly(t, x):
    """ATS observation at time t_i = i h placed at index i + PAD (NaN where missing)"""
    out = np.full(HOURS + 2 * PAD + 2, np.nan)
    i = np.rint(np.asarray(t) / 3600.0).astype(int) + PAD
    ok = (i >= 0) & (i < len(out))
    out[i[ok]] = np.asarray(x)[ok]
    return out


def aligned(arr, shift):
    """a[j]: the ATS value at the end of hour j+1 (+ shift), the time of standalone record j"""
    s = PAD + 1 + shift
    return arr[s:s + HOURS]


def top_cell(volumes):
    """index of the top soil cell from the cell volumes of the column (the vis file has no coordinates).

    A graded column (the matched variants) is numbered from the top and its top cell is the thinnest one.
    A uniform column (the generated mesh of the suite inputs) is numbered from the bottom up: the last cell.
    """
    v = np.round(np.asarray(volumes, dtype=float), 9)
    return int(np.argmin(v)) if v.min() < v.max() else len(v) - 1


def load_ats(test, kind):
    run = PAIRS / test / "ats" if kind == "matched" else SUITE_DIR / test
    d = read_dat(run / "surf_prop.dat")
    t = d["time [s]"]
    mm = 1000.0                           # m (1 m2 column) -> mm
    out = {
        "transp": hourly(t, d["transpiration"] * mm),
        "canopy": hourly(t, d["evaporation_canopy"] * mm),
        "snow_vapor": hourly(t, (d["evaporation_snow"] + d["sublimation_snow"]) * mm),
        "snow_depth": hourly(t, d["snow_depth"]),
        "skin": hourly(t, d["surface_temperature"] - 273.15),
        "air": hourly(t, d["air_temperature"] - 273.15),
        "heat": hourly(t, d["Q_e_surf"] * 1e6),        # MW over 1 m2 -> W m-2
    }
    # daily snapshots of the soil column: top-cell temperature at t = d days
    out["top_days"], out["top_T"] = None, None
    vis = run / "ats_vis_data.h5"
    if vis.is_file():
        with h5py.File(vis, "r") as f:
            g = f["temperature"]
            keys = sorted(g.keys(), key=int)
            days = np.array([g[k].attrs["Time"] * 365.25 for k in keys])      # ATS stores years of 365.25 days
            cells = np.array([np.asarray(g[k][...]).ravel() for k in keys])
            vol = np.asarray(f["cell_volume"][sorted(f["cell_volume"].keys(), key=int)[0]][...]).ravel()
        if len(keys) > 100 and np.allclose(np.diff(days)[:50], 1.0, atol=1e-3):
            out["top_days"], out["top_T"] = days, cells[:, top_cell(vol)] - 273.15
    return out


def load_sa(test):
    arr, last_precip = {}, 0.0
    names = ("QTransp_col", "QVegEvap_col", "EVAPGrnd_col", "SNOWPACK_col", "SNOWDENS_col", "AIR_TEMP_col",
             "HeatFlx2Grnd_col", "TEMP_vr", "tPRECIP_col")
    for fn in sorted((PAIRS / test / "standalone").glob("*.ecosim.h0.*.nc")):
        d = netCDF4.Dataset(fn)
        d.set_auto_mask(False)
        for k in names:
            if k not in d.variables:                 # for example the transpiration of a run without plants
                continue
            v = np.asarray(d[k][...], dtype=float)
            v = v[:, 0, 0] if k == "TEMP_vr" else v.reshape(v.shape[0], -1)[:, 0]
            if k == "tPRECIP_col":
                last_precip += v[-1]
            else:
                arr.setdefault(k, []).append(v)
        d.close()
    a = {k: np.concatenate(v)[:HOURS] for k, v in arr.items()}
    missing = [k for k in names if k not in a and k != "tPRECIP_col"]
    if missing:
        print(f"  {test}: standalone history has no {missing}; taken as zero")
        for k in missing:
            a[k] = np.zeros(HOURS)
    dens = a["SNOWDENS_col"]
    depth = np.where((dens > 0) & (dens < 1000), a["SNOWPACK_col"] / np.where(dens > 0, dens, 1), 0.0)
    return {
        "transp": a["QTransp_col"], "canopy": a["QVegEvap_col"], "ground_vapor": a["EVAPGrnd_col"],
        "snow_depth": np.where(a["SNOWPACK_col"] > 0, depth, 0.0), "air": a["AIR_TEMP_col"],
        "top": a["TEMP_vr"], "heat": a["HeatFlx2Grnd_col"] * 1e6 / 3600.0,       # MJ m-2 h-1 -> W m-2
        "precip_total": last_precip,
    }


def ats_health(test, kind):
    run = PAIRS / test / "ats" if kind == "matched" else SUITE_DIR / test
    log = run / "out.log"
    if not log.is_file():
        return {}
    data = log.read_bytes()
    dts = np.array([float(x) for x in re.findall(rb"dt \[days\] = ([0-9.eE+-]+)", data)])
    status = (run / "status.txt").read_text().split("|") if (run / "status.txt").is_file() else []
    wall = re.search(r"wall (\d+) s", " ".join(status))
    return {
        "steps": int(len(dts)), "min_dt_s": float(dts.min() * 86400) if dts.size else None,
        "energy_warnings": len(re.findall(rb"subsurface energy  \|   is not admissible", data)),
        "flow_warnings": len(re.findall(rb"subsurface flow    \|   is not admissible", data)),
        "solver_fail": len(re.findall(rb"Solution iterate is not admissible, FAIL", data)),
        "ecosim_abort": bool(re.search(rb"^ *ENDRUN", data, re.M)),
        "wall_s": int(wall.group(1)) if wall else None,
        "status": status[0].strip() if status else "",
    }


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def daily_sum(x):
    return np.nansum(x[:HOURS].reshape(NDAYS, 24), axis=1)


def daily_mean(x):
    with np.errstate(all="ignore"):
        return np.nanmean(x[:HOURS].reshape(NDAYS, 24), axis=1)


def corr(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 10 or a[m].std() == 0 or b[m].std() == 0:
        return float("nan")
    return float(np.corrcoef(a[m], b[m])[0, 1])


def rmse(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    return float(np.sqrt(np.mean((a[m] - b[m]) ** 2))) if m.any() else float("nan")


def bias(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    return float(np.mean(a[m] - b[m])) if m.any() else float("nan")


def snow_seasons(depth_h, thresh=0.01, min_len=14, melt=0.005):
    """contiguous snow-covered periods of a depth series: start, peak, peak day and melt-out day"""
    dmax = np.nanmax(np.nan_to_num(depth_h[:HOURS]).reshape(NDAYS, 24), axis=1)
    cover = dmax > thresh
    seasons, i = [], 0
    while i < NDAYS:
        if not cover[i]:
            i += 1
            continue
        j = i
        while j < NDAYS and cover[j]:
            j += 1
        if j - i >= min_len:
            k = i + int(np.argmax(dmax[i:j]))
            end = next((m for m in range(k, j) if dmax[m] < melt), j)
            seasons.append({"start_day": i, "peak": float(dmax[k]), "peak_day": k, "melt_out_day": end,
                            "ongoing_at_end": j >= NDAYS})
        i = j
    return seasons


def best_shift(pairs, kind="corr"):
    """alignment shift (hours) of the ATS series against the standalone record, over all tests"""
    scores = {}
    for s in range(-2, 3):
        vals = []
        for ats, sa in pairs:
            a = aligned(ats, s)
            if kind == "corr":
                vals.append(corr(a, sa))
            else:
                vals.append(-np.nanmean(np.abs(a - sa)))
        scores[s] = float(np.nanmedian(vals))
    return max(scores, key=scores.get), scores


def grade(value, kind, green, amber):
    if value is None or not np.isfinite(value):
        return "na"
    err = abs(value - 1.0) if kind == "ratio" else value
    if kind == "min":
        return "green" if value >= green else ("amber" if value >= amber else "red")
    return "green" if err <= green else ("amber" if err <= amber else "red")


def compute(test, ats, sa, shifts, kind):
    fs, ss = shifts["flux"], shifts["state"]
    A = {k: aligned(ats[k], fs) for k in ("transp", "canopy", "snow_vapor", "heat")}
    A.update({k: aligned(ats[k], ss) for k in ("snow_depth", "skin", "air")})
    m = {"test": test}
    # fluxes
    for name, a, s in (("transp", A["transp"], sa["transp"]), ("canopy", A["canopy"], sa["canopy"])):
        ta, ts = float(np.nansum(a)), float(np.nansum(s))
        m[f"{name}_total_ats"], m[f"{name}_total_sa"] = ta, ts
        m[f"{name}_ratio"] = ta / ts if ts else float("nan")
        m[f"{name}_corr"] = corr(daily_sum(a), daily_sum(s))
        m[f"{name}_corr_hourly"] = corr(a, s)
        m[f"{name}_rmse_daily"] = rmse(daily_sum(a), daily_sum(s))
    m["snow_vapor_total_ats"] = float(np.nansum(A["snow_vapor"]))
    m["ground_plus_snow_vapor_total_sa"] = float(np.nansum(sa["ground_vapor"]))
    # snow
    sd_a, sd_s = A["snow_depth"], sa["snow_depth"]
    sea_a, sea_s = snow_seasons(sd_a), snow_seasons(sd_s)
    m["snow_seasons_ats"], m["snow_seasons_sa"] = sea_a, sea_s
    m["snow_peak_ats"] = float(np.nanmax(sd_a))
    m["snow_peak_sa"] = float(np.nanmax(sd_s))
    m["snow_peak_ratio"] = m["snow_peak_ats"] / m["snow_peak_sa"] if m["snow_peak_sa"] else float("nan")
    m["snow_corr"] = corr(daily_mean(sd_a), daily_mean(sd_s))
    m["snow_rmse"] = rmse(daily_mean(sd_a), daily_mean(sd_s))
    m["melt_out_days"] = (abs(sea_a[0]["melt_out_day"] - sea_s[0]["melt_out_day"])
                          if sea_a and sea_s else float("nan"))
    # temperatures
    m["air_max_abs_diff_K"] = float(np.nanmax(np.abs(A["air"] - sa["air"])))
    # Temperatures: reported for the whole run and for each year. The first winter is dominated by the initial
    # condition (ATS starts the whole column at 260 K), so the graded values and the headline numbers are year 2.
    sk, tp = daily_mean(A["skin"]), daily_mean(sa["top"])
    m["skin_rmse_all"], m["skin_bias_all"] = rmse(sk, tp), bias(sk, tp)
    m["skin_rmse_y1"], m["skin_bias_y1"] = rmse(sk[:365], tp[:365]), bias(sk[:365], tp[:365])
    m["skin_bias"], m["skin_rmse"], m["skin_corr"] = bias(sk[365:], tp[365:]), rmse(sk[365:], tp[365:]), corr(sk[365:], tp[365:])
    if ats["top_T"] is not None:
        j = np.rint(ats["top_days"] * 24).astype(int) - 1
        ok = (j >= 0) & (j < HOURS)
        days_ok = ats["top_days"][ok]
        top_a, top_s = ats["top_T"][ok], sa["top"][j[ok]]
        y2 = days_ok >= 365
        m["top_cell_rmse_all"], m["top_cell_bias_all"] = rmse(top_a, top_s), bias(top_a, top_s)
        m["top_cell_rmse_y1"], m["top_cell_bias_y1"] = rmse(top_a[~y2], top_s[~y2]), bias(top_a[~y2], top_s[~y2])
        m["top_cell_bias"], m["top_cell_rmse"], m["top_cell_corr"] = bias(top_a[y2], top_s[y2]), rmse(top_a[y2], top_s[y2]), corr(top_a[y2], top_s[y2])
        m["_top"] = (days_ok, top_a, top_s)
    else:
        for k in ("top_cell_rmse_all", "top_cell_bias_all", "top_cell_rmse_y1", "top_cell_bias_y1",
                  "top_cell_bias", "top_cell_rmse", "top_cell_corr"):
            m[k] = float("nan")
        m["_top"] = None
    # ground heat flux (definitions differ slightly: see the notes)
    m["heat_mean_ats"] = float(np.nanmean(A["heat"]))
    m["heat_mean_sa"] = float(np.nanmean(sa["heat"]))
    m["heat_corr"] = corr(daily_mean(A["heat"]), daily_mean(sa["heat"]))
    # forcing consistency
    with h5py.File(META[test]["forcing"], "r") as f:
        rain = f["precipitation rain [m s^-1]"][:HOURS]
        snow = f["precipitation snow [m SWE s^-1]"][:HOURS]
    m["precip_forcing_mm"] = float(np.sum((rain + snow) * 1000 * 3600))
    m["precip_sa_mm"] = float(sa["precip_total"])
    m["_series"] = (A, sa)
    # grades
    m["grades"] = {k: grade(m.get(k), TOL[k][0], TOL[k][1], TOL[k][2]) for k in TOL}
    return m


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def png(fig, path):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return base64.b64encode(buf.getvalue()).decode()


def month_totals(x):
    edges = np.cumsum([0] + MONTH_DAYS)
    d = daily_sum(x)
    out = []
    for y in (0, 365):
        for a, b in zip(edges[:-1], edges[1:]):
            lo, hi = y + a, min(y + b, NDAYS)
            out.append(d[lo:hi].sum() if lo < NDAYS else np.nan)
    return np.array(out)


def figure_timeseries(m, label):
    A, sa = m["_series"]
    days = np.arange(NDAYS) + 0.5
    hdays = (np.arange(HOURS) + 1) / 24.0
    fig, ax = plt.subplots(6, 1, figsize=(12, 15), sharex=True)
    ax[0].plot(days, daily_sum(A["transp"]), color=ATS_C, lw=1, label="ATS")
    ax[0].plot(days, daily_sum(sa["transp"]), color=SA_C, lw=1, ls="--", label="standalone")
    ax[0].set_ylabel("Transpiration [mm/d]")
    ax[1].plot(days, daily_sum(A["canopy"]), color=ATS_C, lw=1)
    ax[1].plot(days, daily_sum(sa["canopy"]), color=SA_C, lw=1, ls="--")
    ax[1].set_ylabel("Canopy evaporation [mm/d]")
    ax[2].plot(hdays, A["snow_depth"], color=ATS_C, lw=1)
    ax[2].plot(hdays, sa["snow_depth"], color=SA_C, lw=1, ls="--")
    ax[2].set_ylabel("Snow depth [m]")
    ax[3].plot(days, daily_mean(A["air"]), color="0.6", lw=0.8, label="air (daily mean)")
    ax[3].plot(days, daily_mean(A["skin"]), color=ATS_C, lw=1, label="ATS surface (skin)")
    ax[3].plot(days, daily_mean(sa["top"]), color=SA_C, lw=1, ls="--", label="standalone top soil layer")
    if m["_top"] is not None:
        d, a, s = m["_top"]
        ax[3].plot(d, a, color="#08306b", lw=0, marker=".", ms=2.5, label="ATS top soil cell (daily snapshot)")
    ax[3].set_ylabel("Temperature [°C]")
    ax[3].legend(ncol=4, fontsize=8, loc="upper center")
    ax[4].plot(days, daily_mean(A["heat"]), color=ATS_C, lw=1)
    ax[4].plot(days, daily_mean(sa["heat"]), color=SA_C, lw=1, ls="--")
    ax[4].set_ylabel("Ground heat flux [W/m²]\n(daily mean)")
    ax[5].plot(days, np.cumsum(daily_sum(A["transp"])), color=ATS_C, lw=1.4)
    ax[5].plot(days, np.cumsum(daily_sum(sa["transp"])), color=SA_C, lw=1.4, ls="--")
    ax[5].set_ylabel("Cumulative transpiration [mm]")
    ax[5].set_xlabel("Day of simulation")
    ax[0].legend(loc="lower left", fontsize=9, ncol=2)
    for a in ax:
        a.grid(alpha=0.25)
        a.axvline(365, color="0.8", lw=0.8)
    fig.suptitle(label, y=0.995, fontsize=13)
    fig.tight_layout()
    return fig


def figure_diagnostics(m):
    A, sa = m["_series"]
    fig, ax = plt.subplots(1, 4, figsize=(16, 3.8))
    for a, key, title in ((ax[0], "transp", "Transpiration, daily [mm/d]"), (ax[1], "canopy", "Canopy evaporation, daily [mm/d]")):
        x, y = daily_sum(sa[key]), daily_sum(A[key])
        a.scatter(x, y, s=5, alpha=0.5, color=ATS_C)
        lim = [min(np.nanmin(x), np.nanmin(y)), max(np.nanmax(x), np.nanmax(y))]
        a.plot(lim, lim, "k-", lw=0.8)
        a.set_xlabel("standalone")
        a.set_ylabel("ATS")
        a.set_title(f"{title}\nr = {m[key + '_corr']:.2f}", fontsize=10)
        a.grid(alpha=0.25)
    hours = np.arange(HOURS) % 24
    jja = np.zeros(HOURS, bool)
    for y in (0, 365):
        jja[(y + 151) * 24:(y + 243) * 24] = True
    for arr, color, ls, lab in ((A["transp"], ATS_C, "-", "ATS"), (sa["transp"], SA_C, "--", "standalone")):
        prof = [np.nanmean(arr[jja & (hours == h)]) for h in range(24)]
        ax[2].plot(range(24), prof, color=color, ls=ls, lw=1.6, label=lab)
    ax[2].set_title("Transpiration, mean diurnal cycle (Jun-Aug)\n[mm/h, hour of day of the record]", fontsize=10)
    ax[2].set_xlabel("hour of day")
    ax[2].legend(fontsize=8)
    ax[2].grid(alpha=0.25)
    ma, ms = month_totals(A["transp"]), month_totals(sa["transp"])
    x = np.arange(24)
    ax[3].bar(x - 0.2, ma, 0.4, color=ATS_C, label="ATS")
    ax[3].bar(x + 0.2, ms, 0.4, color=SA_C, label="standalone")
    ax[3].set_title("Transpiration, monthly totals [mm]", fontsize=10)
    ax[3].set_xlabel("month of simulation (1-24)")
    ax[3].legend(fontsize=8)
    ax[3].grid(alpha=0.25)
    fig.tight_layout()
    return fig


def figure_overview(results):
    fig, ax = plt.subplots(2, 3, figsize=(16, 8.5))
    spec = [("transp_total", "Transpiration, total [mm]"), ("canopy_total", "Canopy evaporation, total [mm]"),
            ("melt", "First-season melt-out [day]"), ("transp_corr", "Transpiration daily correlation [-]"),
            ("snow_peak", "Peak snow depth [m]"), ("skin_bias", "Skin (ATS) minus top layer (SA) temperature, year 2 [K]")]
    for a, (key, title) in zip(ax.ravel(), spec):
        for veg in COLORS:
            rs = sorted((r for r in results if r["veg"] == veg), key=lambda r: r["lai"])
            if not rs:
                continue
            x = [r["lai"] for r in rs]
            get = lambda r, side: {
                "transp_total": r[f"transp_total_{side}"], "canopy_total": r[f"canopy_total_{side}"],
                "melt": (r[f"snow_seasons_{side}"][0]["melt_out_day"] if r[f"snow_seasons_{side}"] else np.nan),
                "snow_peak": r[f"snow_peak_{side}"], "transp_corr": r["transp_corr"], "skin_bias": r["skin_bias"],
            }[key]
            if key in ("transp_corr", "skin_bias"):
                a.plot(x, [get(r, "ats") for r in rs], "o-", color=COLORS[veg], label=veg)
            else:
                a.plot(x, [get(r, "ats") for r in rs], "o-", color=COLORS[veg], label=f"{veg} ATS")
                a.plot(x, [get(r, "sa") for r in rs], "s--", color=COLORS[veg], alpha=0.7, mfc="none")
        a.set_title(title, fontsize=10)
        a.set_xlabel(AXIS_LABEL)
        a.grid(alpha=0.25)
    ax[0, 0].legend(fontsize=7, ncol=2)
    fig.text(0.5, 0.005, "solid = ATS, dashed open squares = standalone", ha="center", fontsize=9)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
CSS = """
body{font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:1250px;margin:20px auto;padding:0 16px;color:#222}
h1{font-size:1.6em}h2{border-bottom:1px solid #ccc;padding-bottom:3px;margin-top:2em}
table{border-collapse:collapse;font-size:0.85em;margin:0.6em 0}th,td{border:1px solid #ccc;padding:3px 7px;text-align:right}
th{background:#f0f0f0}td.l,th.l{text-align:left}
.green{background:#c9ecc9}.amber{background:#ffe9a8}.red{background:#f7b8b8}.na{background:#eee;color:#888}
img{max-width:100%}.note{background:#f6f6f6;border-left:4px solid #999;padding:6px 12px;font-size:0.9em}
code{background:#eee;padding:0 3px}
"""


def fmt(v, spec=".2f"):
    return "–" if v is None or (isinstance(v, float) and not np.isfinite(v)) else format(v, spec)


def cell(m, key, spec=".2f"):
    return f'<td class="{m["grades"][key]}">{fmt(m[key], spec)}</td>'


def render_html(results, kind, shifts, overview_b64, notes, out):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = {"green": 0, "amber": 0, "red": 0}
    for r in results:
        for g in r["grades"].values():
            if g in counts:
                counts[g] += 1
    h = [f"<!doctype html><html><head><meta charset='utf-8'><title>PFT tests: ATS-EcoSIM vs standalone EcoSIM</title><style>{CSS}</style></head><body>",
         "<h1>PFT tests: ATS-EcoSIM against standalone EcoSIM</h1>",
         f"<p>Generated {now} from <code>pairs/&lt;test&gt;/{'ats' if kind == 'matched' else '(suite run)'}</code> and "
         f"<code>pairs/&lt;test&gt;/standalone</code>; {len(results)} tests, 729 days, hourly.</p>",
         f"<p><b>Graded quantities:</b> <span class='green'>&nbsp;{counts['green']} green&nbsp;</span> "
         f"<span class='amber'>&nbsp;{counts['amber']} amber&nbsp;</span> <span class='red'>&nbsp;{counts['red']} red&nbsp;</span>. "
         "Thresholds are placeholders for &ldquo;ballpark&rdquo; and can be edited in <code>DEFAULT_TOL</code> at the top of "
         "<code>tools/ats_ecosim_pairs/report.py</code>; they grade how closely the two models match, not which is right.</p>",
         "<div class='note'><b>Read before interpreting.</b> "
         f"ATS flux observations are aligned to the standalone hourly records with a shift of {shifts['flux']:+d} h, and states with "
         f"{shifts['state']:+d} h (chosen to maximise agreement across all tests). "
         "The standalone history has no separate skin temperature (<code>TEMP_SURF_col</code> is a litter/soil weighted value that equals "
         "the top soil layer here, since there is no litter), so the &ldquo;surface temperature&rdquo; comparison is ATS skin against the standalone "
         "top soil layer, with the ATS top soil cell (daily snapshots) as the like-for-like check. Temperature agreement is graded on year 2: in the first "
         "winter ATS's surface and top soil cell stay several kelvin colder than the standalone, which comes from the initial condition (ATS starts the whole "
         "column at 260 K), and the two converge after the first snowmelt. Standalone ground evaporation includes soil "
         "evaporation, which the ATS observations do not provide, so it is not compared.</div>",
         "<h2>Summary</h2>",
         "<table><tr><th class='l'>test</th><th>transp. ATS [mm]</th><th>SA [mm]</th><th>ratio</th><th>daily r</th>"
         "<th>canopy evap ratio</th><th>daily r</th><th>peak snow ATS/SA [m]</th><th>ratio</th><th>melt-out ATS / SA [d]</th>"
         "<th>skin vs SA top layer RMSE, yr 2 [K]</th><th>top cell RMSE, yr 2 [K]</th><th>ATS min dt [s]</th><th>warnings</th></tr>"]
    for r in results:
        sa_, ss_ = r["snow_seasons_ats"], r["snow_seasons_sa"]
        melt = f"{sa_[0]['melt_out_day']} / {ss_[0]['melt_out_day']}" if sa_ and ss_ else "–"
        hth = r["health"]
        h.append(f"<tr><td class='l'><a href='#{r['test']}'>{r['test']}</a></td><td>{fmt(r['transp_total_ats'], '.1f')}</td>"
                 f"<td>{fmt(r['transp_total_sa'], '.1f')}</td>{cell(r, 'transp_ratio')}{cell(r, 'transp_corr')}"
                 f"{cell(r, 'canopy_ratio')}{cell(r, 'canopy_corr')}<td>{fmt(r['snow_peak_ats'])} / {fmt(r['snow_peak_sa'])}</td>"
                 f"{cell(r, 'snow_peak_ratio')}<td class='{r['grades']['melt_out_days']}'>{melt}</td>"
                 f"{cell(r, 'skin_rmse')}{cell(r, 'top_cell_rmse')}<td>{fmt(hth.get('min_dt_s'), '.0f')}</td>"
                 f"<td>{hth.get('energy_warnings', 0) + hth.get('flow_warnings', 0)}</td></tr>")
    h.append("</table>")
    h.append("<h2>Across tests</h2><p>Totals, snow timing and agreement against LAI, per vegetation type.</p>"
             f"<img src='data:image/png;base64,{overview_b64}'>")
    h.append("<h2>Differences in setup between the two sides</h2>" + notes)
    h.append("<h2>Tests</h2>")
    for r in results:
        hth = r["health"]
        h.append(f"<h3 id='{r['test']}'>{r['test']} &nbsp;<small>({r['veg']}, LAI {r['lai']:g})</small></h3>")
        h.append(f"<img src='data:image/png;base64,{r['fig_ts']}'><img src='data:image/png;base64,{r['fig_diag']}'>")
        rows = [
            ("Transpiration total [mm]", f"{fmt(r['transp_total_ats'], '.1f')} / {fmt(r['transp_total_sa'], '.1f')}", f"ratio {fmt(r['transp_ratio'])}, daily r {fmt(r['transp_corr'])}, hourly r {fmt(r['transp_corr_hourly'])}, daily RMSE {fmt(r['transp_rmse_daily'], '.3f')} mm/d"),
            ("Canopy evaporation total [mm]", f"{fmt(r['canopy_total_ats'], '.1f')} / {fmt(r['canopy_total_sa'], '.1f')}", f"ratio {fmt(r['canopy_ratio'])}, daily r {fmt(r['canopy_corr'])}, hourly r {fmt(r['canopy_corr_hourly'])}"),
            ("Snow evaporation + sublimation, ATS [mm]", fmt(r['snow_vapor_total_ats'], '.1f'), f"standalone ground + snow vapor flux (soil evaporation included): {fmt(r['ground_plus_snow_vapor_total_sa'], '.1f')} mm, not comparable"),
            ("Snow depth", f"peak {fmt(r['snow_peak_ats'])} / {fmt(r['snow_peak_sa'])} m", f"daily r {fmt(r['snow_corr'])}, RMSE {fmt(r['snow_rmse'], '.3f')} m"),
            ("Snow seasons (ATS)", "; ".join(f"d{s['start_day']}-{s['melt_out_day']} peak {s['peak']:.2f} m" for s in r["snow_seasons_ats"]) or "–", ""),
            ("Snow seasons (standalone)", "; ".join(f"d{s['start_day']}-{s['melt_out_day']} peak {s['peak']:.2f} m" for s in r["snow_seasons_sa"]) or "–", ""),
            ("Air temperature, forcing check", f"max |diff| {fmt(r['air_max_abs_diff_K'], '.2f')} K", "should be ~0 if both runs see the same forcing at the same hour"),
            ("Skin (ATS) vs top layer (SA), year 2", f"bias {fmt(r['skin_bias'])} K, RMSE {fmt(r['skin_rmse'])} K", f"daily r {fmt(r['skin_corr'])}; year 1: bias {fmt(r['skin_bias_y1'])} K, RMSE {fmt(r['skin_rmse_y1'])} K; whole run: RMSE {fmt(r['skin_rmse_all'])} K"),
            ("Top soil cell (ATS snapshots) vs top layer (SA), year 2", f"bias {fmt(r['top_cell_bias'])} K, RMSE {fmt(r['top_cell_rmse'])} K", f"r {fmt(r['top_cell_corr'])}; year 1: bias {fmt(r['top_cell_bias_y1'])} K, RMSE {fmt(r['top_cell_rmse_y1'])} K"),
            ("EcoSIM energy source to the ground, mean [W/m²]", f"{fmt(r['heat_mean_ats'], '.1f')} / {fmt(r['heat_mean_sa'], '.1f')}", f"daily r {fmt(r['heat_corr'])}. Not like for like: ATS is the raw EcoSIM source (before the linearization correction, water enthalpy referenced to 0 °C); the standalone is the heat flux into the ground"),
            ("Precipitation, forcing vs standalone [mm]", f"{fmt(r['precip_forcing_mm'], '.0f')} / {fmt(r['precip_sa_mm'], '.0f')}", "forcing total over 729 d vs the standalone's cumulative precipitation"),
            ("ATS run health", f"{hth.get('steps')} steps, min dt {fmt(hth.get('min_dt_s'), '.0f')} s, wall {hth.get('wall_s')} s",
             f"inadmissible-iterate warnings: energy {hth.get('energy_warnings')}, flow {hth.get('flow_warnings')}; solver FAIL {hth.get('solver_fail')}; EcoSIM abort {hth.get('ecosim_abort')}"),
        ]
        h.append("<table><tr><th class='l'>diagnostic</th><th class='l'>ATS / standalone</th><th class='l'>detail</th></tr>" +
                 "".join(f"<tr><td class='l'>{html.escape(a)}</td><td class='l'>{html.escape(b)}</td><td class='l'>{html.escape(c)}</td></tr>" for a, b, c in rows) + "</table>")
    h.append("<h2>Grading thresholds</h2><table><tr><th class='l'>quantity</th><th>green</th><th>amber</th></tr>")
    for k, (kind_, g, a, desc) in TOL.items():
        sym = {"ratio": "within ±", "min": "≥", "max": "≤"}[kind_]
        h.append(f"<tr><td class='l'>{html.escape(desc)}</td><td>{sym}{g:g}</td><td>{sym}{a:g}</td></tr>")
    h.append("</table></body></html>")
    out.write_text("\n".join(h))


def setup_notes():
    readme = PAIRS / "README.md"
    if not readme.is_file():
        return "<p>(pairs/README.md not found)</p>"
    text = readme.read_text()
    sec = text.split("## Not matched")[1].split("## Layers")[0] if "## Not matched" in text else ""
    items = re.findall(r"^- (.*)$", sec, re.M)
    li = "".join("<li>" + re.sub(r"`([^`]*)`", r"<code>\1</code>", re.sub(r"\*\*([^*]*)\*\*", r"<b>\1</b>", html.escape(i, quote=False))) + "</li>" for i in items)
    return ("<p>These differences remain even for the matched variants (from <code>pairs/README.md</code>):</p><ul>" + li + "</ul>")


# ---------------------------------------------------------------------------
def configure(m, only=None):
    """take the case folder's paths, run length and group colors from its pairs"""
    global PAIRS, META, SUITE_DIR, TOL, COLORS, AXIS_LABEL, NDAYS, HOURS
    PAIRS, SUITE_DIR = m.pairs_dir, m.root
    # tests that have a pair; a requested test without one is an error, any other is left out
    names = [t.name for t in m.tests(only)]
    META = {n: m.meta(n) for n in names if m.meta(n) is not None}
    missing = [n for n in names if n not in META]
    if missing and only:
        raise SystemExit(f"no pair for {missing}: run the `pair` command first")
    if missing:
        print(f"note: no pair for {missing}; they are left out")
    if not META:
        raise SystemExit("no pairs: run the `pair` command first")
    lengths = {v["ndays"] for v in META.values()}
    if len(lengths) != 1:
        raise SystemExit(f"the tests of one report must have the same run length; found {sorted(lengths)} days")
    NDAYS = int(lengths.pop())
    HOURS = NDAYS * 24
    TOL = dict(DEFAULT_TOL)
    for key, (green, amber) in m.thresholds.items():
        if key not in TOL:
            raise ValueError(f"unknown threshold {key!r}; known: {sorted(TOL)}")
        TOL[key] = (TOL[key][0], float(green), float(amber), TOL[key][3])
    tests = [t for t in m.tests(baseline=False) if t.name in META]
    groups = list(dict.fromkeys(t.group for t in tests))
    palette = plt.get_cmap("tab10").colors
    COLORS = {g: DEFAULT_COLORS.get(g) or matplotlib.colors.to_hex(palette[i % 10]) for i, g in enumerate(groups)}
    AXIS_LABEL = (tests[0].axis[0] or "LAI").upper() if tests else "LAI"


def build_report(m, ats_kind="matched", only=None, out=None, check=False):
    """write the report of a suite; returns an exit status (1 if check and a graded quantity is red)"""
    configure(m, only)
    out = Path(out) if out else PAIRS / "report"
    tests = []
    for t in (t for t in m.tests(only, baseline=False) if t.name in META):
        ats_dir = PAIRS / t.name / "ats" if ats_kind == "matched" else SUITE_DIR / t.name
        if (ats_dir / "surf_prop.dat").is_file() and list((PAIRS / t.name / "standalone").glob("*.ecosim.h0.*.nc")):
            tests.append((t.name, t.group, float(t.axis[1])))
        else:
            print(f"skipping {t.name}: missing ATS or standalone output")
    if not tests:
        raise SystemExit("nothing to compare")

    print(f"loading {len(tests)} tests ...")
    data = {n: (load_ats(n, ats_kind), load_sa(n)) for n, _, _ in tests}
    flux_shift, flux_scores = best_shift([(a["canopy"], s["canopy"]) for a, s in data.values()], "corr")
    state_shift, state_scores = best_shift([(a["snow_depth"], s["snow_depth"]) for a, s in data.values()], "mae")
    shifts = {"flux": flux_shift, "state": state_shift}
    print(f"alignment shifts: flux {flux_shift:+d} h {flux_scores}, state {state_shift:+d} h")

    results = []
    for name, veg, lai in tests:
        ats, sa = data[name]
        r = compute(name, ats, sa, shifts, ats_kind)
        r.update(veg=veg, lai=lai, health=ats_health(name, ats_kind))
        label = f"{name} - ATS ({'matched variant' if ats_kind == 'matched' else 'suite run'}) vs standalone EcoSIM"
        r["fig_ts"] = png(figure_timeseries(r, label), out / "figures" / f"{name}_timeseries.png")
        r["fig_diag"] = png(figure_diagnostics(r), out / "figures" / f"{name}_diagnostics.png")
        results.append(r)
        print(f"  {name}: transp ratio {r['transp_ratio']:.2f} (r {r['transp_corr']:.2f}), snow peak ratio {r['snow_peak_ratio']:.2f}, "
              f"skin RMSE {r['skin_rmse']:.1f} K, grades {sorted(set(r['grades'].values()))}")
    overview = png(figure_overview(results), out / "figures" / "overview.png")
    render_html(results, ats_kind, shifts, overview, setup_notes(), out / "index.html")

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if not k.startswith("_") and not k.startswith("fig_")}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, (np.floating, np.integer)):
            return x.item()
        return x
    (out / "report.json").write_text(json.dumps(
        {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "ats_kind": ats_kind,
         "alignment_shift_hours": shifts, "thresholds": {k: v[:3] for k, v in TOL.items()},
         "tests": clean(results)}, indent=1, default=float))
    reds = [(r["test"], k) for r in results for k, g in r["grades"].items() if g == "red"]
    print(f"wrote {out / 'index.html'} ({len(results)} tests; {len(reds)} red graded quantities)")
    return 1 if (check and reds) else 0
