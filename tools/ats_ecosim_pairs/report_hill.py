"""Comparison report of a multi-column pair (a namelist test): ATS-EcoSIM against standalone EcoSIM, per column.

Reads pairs/<test>/ats (surf_prop.dat with the observations of every column, ats_vis_data.h5 with the daily
soil temperature of every cell) and pairs/<test>/standalone (the EcoSIM history files), the way report.py does
for one column, and compares every column with the same definitions and thresholds (report.py:
compute, DEFAULT_TOL). Writes pairs/report/<test>.html (figures embedded), pairs/report/figures/<test>_*.png and
pairs/report/<test>.json.

What is compared per column: transpiration and canopy evaporation, snow depth and its seasons, the ATS skin and
top soil cell against the standalone top layer, and the deeper soil temperature. What differs between the
columns (slope, plant type) is in the table next to the numbers. Needs numpy, h5py, netCDF4 and matplotlib.
"""

import datetime
import html
import json
import math
from pathlib import Path

import h5py
import netCDF4
import numpy as np

from . import report as R

plt = R.plt
ATS_C, SA_C = R.ATS_C, R.SA_C
DEEP_LAYER = 4                                  # layer (0-based) of the deeper soil temperature: about 0.2-0.5 m


def cell_map(vis, meta):
    """index of (layer, column) in the cell arrays of the vis file: the mesh's element blocks are the layers

    The cells are vertical prisms: volume = horizontal area * thickness (the top face is tilted)."""
    ncol, n = meta["columns"], meta["layers"]
    area = np.array([d * meta["width"] for d in meta["dx"]])
    key = sorted(vis["cell_volume"].keys(), key=int)[0]
    vol = np.asarray(vis["cell_volume"][key][...]).ravel()
    layer_major = np.concatenate([area * dz for dz in meta["dz"]])
    if len(vol) == len(layer_major) and np.allclose(vol, layer_major, rtol=1e-3):
        return lambda layer, col: layer * ncol + col
    column_major = np.concatenate([[a * dz for dz in meta["dz"]] for a in area])
    if len(vol) == len(column_major) and np.allclose(vol, column_major, rtol=1e-3):
        return lambda layer, col: col * n + layer
    raise SystemExit("the cell volumes of ats_vis_data.h5 match neither a layer-major nor a column-major numbering of the mesh")


def load_ats(run, meta):
    """per column the series report.compute expects; hourly series are padded as report.hourly does"""
    d = R.read_dat(run / "surf_prop.dat")
    t = d["time [s]"]
    ncol = meta["columns"]
    area = [dx * meta["width"] for dx in meta["dx"]]          # EcoSIM's column area: the horizontal projection of the cell
    out = []
    for c in range(ncol):
        k = f"_c{c + 1}"
        out.append({
            # EcoSIM's transpiration and evaporation outputs are volumes of the cell (the area is 1 m2 in the
            # single-column pairs); divided by its horizontal area they are depths per hour like the standalone's
            "transp": R.hourly(t, d["transpiration" + k] * 1000.0 / area[c]),
            "canopy": R.hourly(t, d["evaporation_canopy" + k] * 1000.0 / area[c]),
            "snow_vapor": R.hourly(t, (d["evaporation_snow" + k] + d["sublimation_snow" + k]) * 1000.0 / area[c]),
            "snow_depth": R.hourly(t, d["snow_depth" + k]),
            "skin": R.hourly(t, d["surface_temperature" + k] - 273.15),
            "air": R.hourly(t, d["air_temperature" + k] - 273.15),
            "heat": R.hourly(t, d["Q_e_surf" + k] * 1e6 / area[c]),          # MW over the cell -> W per m2 of the cell, as EcoSIM
            "top_days": None, "top_T": None, "deep_T": None,
        })
    vis = run / "ats_vis_data.h5"
    if vis.is_file():
        with h5py.File(vis, "r") as f:
            g = f["temperature"]
            keys = sorted(g.keys(), key=int)
            days = np.array([g[k].attrs["Time"] * 365.25 for k in keys])
            cells = np.array([np.asarray(g[k][...]).ravel() for k in keys])
            where = cell_map(f, meta)
        if len(keys) > 100 and np.allclose(np.diff(days)[:50], 1.0, atol=1e-3):
            for c in range(ncol):
                out[c]["top_days"] = days
                out[c]["top_T"] = cells[:, where(0, c)] - 273.15
                out[c]["deep_T"] = cells[:, where(min(DEEP_LAYER, meta["layers"] - 1), c)] - 273.15
    return out


def load_sa(run, meta):
    ncol, hours = meta["columns"], R.HOURS
    names = ("QTransp_col", "QVegEvap_col", "EVAPGrnd_col", "SNOWPACK_col", "SNOWDENS_col", "AIR_TEMP_col",
             "HeatFlx2Grnd_col", "TEMP_vr", "tPRECIP_col")
    arr, last_precip = {}, np.zeros(ncol)
    for fn in sorted(run.glob("*.ecosim.h0.*.nc")):
        d = netCDF4.Dataset(fn)
        d.set_auto_mask(False)
        for k in names:
            if k not in d.variables:
                continue
            v = np.asarray(d[k][...], dtype=float)
            v = v.reshape(v.shape[0], v.shape[1], -1)[:, :, :ncol] if k == "TEMP_vr" else v.reshape(v.shape[0], -1)[:, :ncol]
            if k == "tPRECIP_col":
                last_precip = v[-1]
            else:
                arr.setdefault(k, []).append(v)
        d.close()
    a = {k: np.concatenate(v)[:hours] for k, v in arr.items()}
    out = []
    for c in range(ncol):
        dens = a["SNOWDENS_col"][:, c]
        depth = np.where((dens > 0) & (dens < 1000), a["SNOWPACK_col"][:, c] / np.where(dens > 0, dens, 1), 0.0)
        out.append({
            "transp": a["QTransp_col"][:, c], "canopy": a["QVegEvap_col"][:, c], "ground_vapor": a["EVAPGrnd_col"][:, c],
            "snow_depth": np.where(a["SNOWPACK_col"][:, c] > 0, depth, 0.0), "air": a["AIR_TEMP_col"][:, c],
            "top": a["TEMP_vr"][:, 0, c], "deep": a["TEMP_vr"][:, min(DEEP_LAYER, meta["layers"] - 1), c],
            "heat": a["HeatFlx2Grnd_col"][:, c] * 1e6 / 3600.0, "precip_total": float(last_precip[c]),
        })
    return out


def compare(name, meta, ats, sa, shifts):
    """report.compute for every column, plus the deeper soil temperature"""
    R.META[name] = meta
    res = []
    for c in range(meta["columns"]):
        m = R.compute(name, ats[c], sa[c], shifts, "matched")
        m["column"] = c + 1
        m["pft"], m["slope_deg"], m["width"] = meta["pft"][c], meta["slope_deg"][c], meta["width"]
        m["deep_cell_rmse"] = m["deep_cell_bias"] = float("nan")
        if ats[c]["deep_T"] is not None:
            j = np.rint(ats[c]["top_days"] * 24).astype(int) - 1
            ok = (j >= 0) & (j < R.HOURS) & (ats[c]["top_days"] >= 365)
            m["deep_cell_rmse"] = R.rmse(ats[c]["deep_T"][ok], sa[c]["deep"][j[ok]])
            m["deep_cell_bias"] = R.bias(ats[c]["deep_T"][ok], sa[c]["deep"][j[ok]])
        res.append(m)
    return res


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
def panels(meta, title, draw, ylabel):
    ncol = meta["columns"]
    cols = 3 if ncol > 3 else ncol
    rows = int(math.ceil(ncol / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.3 * cols, 2.5 * rows + 0.4), sharex=True, squeeze=False)
    for c in range(ncol):
        ax = axes[c // cols][c % cols]
        draw(ax, c)
        ax.set_title(f"column {c + 1}: {meta['pft'][c]}, {meta['slope_deg'][c]:g}°", fontsize=9)
        ax.grid(alpha=0.3)
        if c % cols == 0:
            ax.set_ylabel(ylabel, fontsize=8)
    for k in range(ncol, rows * cols):
        axes[k // cols][k % cols].axis("off")
    for ax in axes[-1]:
        ax.set_xlabel("day of run", fontsize=8)
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    return fig


def figure_layout(meta):
    x, z = np.array(meta["x_nodes"]), np.array(meta["z_nodes"])
    fig, ax = plt.subplots(figsize=(9, 3.2))
    palette = {code: plt.get_cmap("tab10")(i) for i, code in enumerate(sorted(set(meta["pft"])))}
    for c in range(meta["columns"]):
        ax.fill([x[c], x[c + 1], x[c + 1], x[c]], [z[c], z[c + 1], z[c + 1] - meta["depth"], z[c] - meta["depth"]],
                color=palette[meta["pft"][c]], alpha=0.55, lw=0.5, ec="k")
        ax.text(0.5 * (x[c] + x[c + 1]), max(z[c], z[c + 1]) + 4, f"{c + 1}\n{meta['slope_deg'][c]:g}°", ha="center", fontsize=8)
    ax.plot(x, z, "k-", lw=1.5)
    if meta.get("water_table_z") is not None:
        ax.axhline(meta["water_table_z"], color="tab:blue", ls="--", lw=1, label="water table")
        ax.legend(loc="upper right", fontsize=8)
    for code, colour in palette.items():
        ax.fill([], [], color=colour, alpha=0.55, label=code)
    ax.legend(fontsize=8, loc="upper right")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("surface elevation [m]")
    ax.set_title("Layout (soil depth drawn to scale only horizontally)", fontsize=10)
    fig.tight_layout()
    return fig


def figure_transp(meta, results, shifts):
    def draw(ax, c):
        A, S = results[c]["_series"]
        ax.plot(R.daily_sum(A["transp"]), color=ATS_C, lw=1, label="ATS")
        ax.plot(R.daily_sum(S["transp"] if "transp" in S else S["transp"]), color=SA_C, lw=1, label="standalone")
        if c == 0:
            ax.legend(fontsize=7)
    return panels(meta, "Transpiration [mm d-1]", draw, "mm d-1")


def figure_snow(meta, results, shifts):
    def draw(ax, c):
        A, S = results[c]["_series"]
        ax.plot(R.daily_mean(A["snow_depth"]), color=ATS_C, lw=1, label="ATS")
        ax.plot(R.daily_mean(S["snow_depth"]), color=SA_C, lw=1, label="standalone")
        if c == 0:
            ax.legend(fontsize=7)
    return panels(meta, "Snow depth [m]", draw, "m")


def figure_temperature(meta, results, ats, sa):
    def draw(ax, c):
        A, S = results[c]["_series"]
        ax.plot(R.daily_mean(A["skin"]), color=ATS_C, lw=0.8, label="ATS skin")
        ax.plot(R.daily_mean(S["top"] if "top" in S else A["skin"]), color=SA_C, lw=0.8, label="standalone top layer")
        if ats[c]["top_T"] is not None:
            ax.plot(ats[c]["top_days"], ats[c]["top_T"], ".", color="0.3", ms=1.5, label="ATS top cell")
        if c == 0:
            ax.legend(fontsize=6)
    return panels(meta, "Surface and top soil temperature [°C]", draw, "°C")


def figure_ratios(meta, results):
    keys = (("transp_ratio", "transpiration"), ("canopy_ratio", "canopy evap."), ("snow_peak_ratio", "peak snow"))
    fig, ax = plt.subplots(figsize=(8, 3.2))
    width = 0.25
    cols = np.arange(meta["columns"])
    for i, (key, label) in enumerate(keys):
        ax.bar(cols + (i - 1) * width, [r[key] for r in results], width, label=label)
    ax.axhline(1.0, color="k", lw=0.8)
    ax.set_xticks(cols)
    ax.set_xticklabels([f"{c + 1}\n{meta['pft'][c]}" for c in cols], fontsize=8)
    ax.set_ylabel("ATS / standalone")
    ax.legend(fontsize=8)
    ax.set_title("Ratios by column", fontsize=10)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------
def page(name, meta, results, shifts, health, figs, out):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    counts = {"green": 0, "amber": 0, "red": 0}
    for r in results:
        for g in r["grades"].values():
            if g in counts:
                counts[g] += 1
    h = [f"<!doctype html><html><head><meta charset='utf-8'><title>{name}: ATS-EcoSIM vs standalone EcoSIM</title>"
         f"<style>{R.CSS}</style></head><body>",
         f"<h1>{html.escape(name)}: ATS-EcoSIM against standalone EcoSIM, by column</h1>",
         f"<p>Generated {now}; {meta['columns']} columns, {meta['ndays']} days, hourly. Graded quantities: "
         f"<span class='green'>&nbsp;{counts['green']} green&nbsp;</span> <span class='amber'>&nbsp;{counts['amber']} amber&nbsp;</span> "
         f"<span class='red'>&nbsp;{counts['red']} red&nbsp;</span> (thresholds of report.py, which grade how closely the models match).</p>",
         f"<div class='note'>Flux observations are aligned with a shift of {shifts['flux']:+d} h and states with {shifts['state']:+d} h "
         "(chosen to maximise agreement over all columns). Temperatures are graded on year 2. ATS soil cells are the daily snapshots of "
         "the vis file. Lateral flow differs between the models (see pairs/README.md of the case), so columns that receive or lose water "
         "(the downslope ones) are expected to disagree more than the upslope ones.</div>",
         "<h2>Layout</h2>", f"<img src='data:image/png;base64,{figs['layout']}'>",
         "<h2>Summary by column</h2>",
         "<table><tr><th>column</th><th class='l'>PFT</th><th>slope [°]</th><th>transp. ATS [mm]</th><th>SA [mm]</th><th>ratio</th>"
         "<th>daily r</th><th>canopy ratio</th><th>peak snow ATS / SA [m]</th><th>snow ratio</th><th>melt-out ATS / SA [d]</th>"
         "<th>skin RMSE yr 2 [K]</th><th>top cell RMSE yr 2 [K]</th><th>deeper cell RMSE yr 2 [K]</th></tr>"]
    for r in results:
        sa_, ss_ = r["snow_seasons_ats"], r["snow_seasons_sa"]
        melt = f"{sa_[0]['melt_out_day']} / {ss_[0]['melt_out_day']}" if sa_ and ss_ else "–"
        h.append(f"<tr><td>{r['column']}</td><td class='l'>{r['pft']}</td><td>{r['slope_deg']:g}</td>"
                 f"<td>{R.fmt(r['transp_total_ats'], '.1f')}</td><td>{R.fmt(r['transp_total_sa'], '.1f')}</td>{R.cell(r, 'transp_ratio')}"
                 f"{R.cell(r, 'transp_corr')}{R.cell(r, 'canopy_ratio')}<td>{R.fmt(r['snow_peak_ats'])} / {R.fmt(r['snow_peak_sa'])}</td>"
                 f"{R.cell(r, 'snow_peak_ratio')}<td class='{r['grades']['melt_out_days']}'>{melt}</td>"
                 f"{R.cell(r, 'skin_rmse')}{R.cell(r, 'top_cell_rmse')}<td>{R.fmt(r['deep_cell_rmse'])}</td></tr>")
    h.append("</table>")
    h.append(f"<img src='data:image/png;base64,{figs['ratios']}'>")
    for key, title in (("transp", "Transpiration"), ("snow", "Snow"), ("temp", "Temperature")):
        h.append(f"<h2>{title}</h2><img src='data:image/png;base64,{figs[key]}'>")
    h.append("<h2>ATS run health</h2>")
    h.append("<table>" + "".join(f"<tr><td class='l'>{html.escape(k)}</td><td class='l'>{html.escape(str(v))}</td></tr>"
                                 for k, v in health.items()) + "</table>")
    h.append("</body></html>")
    out.write_text("\n".join(h))


def build(case, name, out=None, check=False):
    """the report of one namelist test; returns 1 if it could not be built (or, with check, if a graded value is red)"""
    meta = case.meta(name)
    if meta is None:
        print(f"{name}: no pair; run the `pair` command first")
        return 1
    meta = dict(meta, forcing=str(case.pair_dir(name) / "forcing.h5"))      # pair.json may come from another machine
    run_ats, run_sa = case.pair_dir(name) / "ats", case.pair_dir(name) / "standalone"
    if not (run_ats / "surf_prop.dat").is_file() or not list(run_sa.glob("*.ecosim.h0.*.nc")):
        print(f"{name}: missing ATS or standalone output; skipped")
        return 1
    R.PAIRS, R.SUITE_DIR = case.pairs_dir, case.root
    R.META = {}                                       # report.compute reads the forcing file of the test from it
    R.NDAYS = int(meta["ndays"])
    R.HOURS = R.NDAYS * 24
    out = Path(out) if out else case.pairs_dir / "report"
    ats, sa = load_ats(run_ats, meta), load_sa(run_sa, meta)
    shifts = {}
    shifts["flux"], _ = R.best_shift([(a["canopy"], s["canopy"]) for a, s in zip(ats, sa)], "corr")
    shifts["state"], _ = R.best_shift([(a["snow_depth"], s["snow_depth"]) for a, s in zip(ats, sa)], "mae")
    results = compare(name, meta, ats, sa, shifts)
    health = R.ats_health(name, "matched")
    figs = {
        "layout": R.png(figure_layout(meta), out / "figures" / f"{name}_layout.png"),
        "ratios": R.png(figure_ratios(meta, results), out / "figures" / f"{name}_ratios.png"),
        "transp": R.png(figure_transp(meta, results, shifts), out / "figures" / f"{name}_transpiration.png"),
        "snow": R.png(figure_snow(meta, results, shifts), out / "figures" / f"{name}_snow.png"),
        "temp": R.png(figure_temperature(meta, results, ats, sa), out / "figures" / f"{name}_temperature.png"),
    }
    page(name, meta, results, shifts, health, figs, out / f"{name}.html")

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if not k.startswith("_") and not k.startswith("fig_")}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, (np.floating, np.integer)):
            return x.item()
        return x
    (out / f"{name}.json").write_text(json.dumps(
        {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "alignment_shift_hours": shifts,
         "columns": clean(results), "health": clean(health)}, indent=1, default=float))
    reds = [(r["column"], k) for r in results for k, g in r["grades"].items() if g == "red"]
    for r in results:
        print(f"  column {r['column']} ({r['pft']}, {r['slope_deg']:g}°): transp ratio {r['transp_ratio']:.2f} (r {r['transp_corr']:.2f}), "
              f"snow peak ratio {r['snow_peak_ratio']:.2f}, skin RMSE {r['skin_rmse']:.1f} K")
    print(f"wrote {out / (name + '.html')} ({len(results)} columns; {len(reds)} red graded quantities)")
    return 1 if (check and reds) else 0
