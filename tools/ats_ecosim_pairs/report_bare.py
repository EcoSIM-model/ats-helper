"""Comparison report of the bare-ground pair: ATS-EcoSIM against standalone EcoSIM, no plants.

Reads pairs/<baseline>/ats (the matched ATS variant) and pairs/<baseline>/standalone, and for context the
suite's own run <baseline>/ (the original ATS input, not on the graded column). Writes
pairs/report/<baseline>.html (figure embedded), pairs/report/figures/<baseline>_timeseries.png and
pairs/report/<baseline>.json.

With no plants there is no transpiration or canopy evaporation, so the quantities are snow depth and its
seasons, the skin and soil temperatures, the ground heat flux and the vapor flux from the ground and snow.
These are not graded: the pair has no PFT sweep to rank against. The numbers and their definitions are
the ones of report.py.

Needs numpy, h5py, netCDF4 and matplotlib (the host Python).
"""

import datetime
import html
import json
from pathlib import Path

import numpy as np

from . import report as R

plt = R.plt
ORIG_C = "0.55"


def metrics(name, ats, sa, shift, orig=None):
    A = {k: R.aligned(ats[k], shift) for k in ("snow_vapor", "heat", "snow_depth", "skin", "air")}
    m = {"test": name, "alignment_shift_hours": shift}
    sd_a, sd_s = A["snow_depth"], sa["snow_depth"]
    sea_a, sea_s = R.snow_seasons(sd_a), R.snow_seasons(sd_s)
    m["snow_seasons_ats"], m["snow_seasons_sa"] = sea_a, sea_s
    m["snow_peak_ats"], m["snow_peak_sa"] = float(np.nanmax(sd_a)), float(np.nanmax(sd_s))
    m["snow_corr"], m["snow_rmse"] = R.corr(R.daily_mean(sd_a), R.daily_mean(sd_s)), R.rmse(R.daily_mean(sd_a), R.daily_mean(sd_s))
    m["snow_days_ats"] = int((R.daily_mean(sd_a) > 0.01).sum())
    m["snow_days_sa"] = int((R.daily_mean(sd_s) > 0.01).sum())
    m["air_max_abs_diff_K"] = float(np.nanmax(np.abs(A["air"] - sa["air"])))
    sk, tp = R.daily_mean(A["skin"]), R.daily_mean(sa["top"])
    for label, sl in (("all", slice(None)), ("y1", slice(0, 365)), ("y2", slice(365, None))):
        m[f"skin_bias_{label}"], m[f"skin_rmse_{label}"] = R.bias(sk[sl], tp[sl]), R.rmse(sk[sl], tp[sl])
    m["skin_corr_y2"] = R.corr(sk[365:], tp[365:])
    m["_top"] = None
    if ats["top_T"] is not None:
        j = np.rint(ats["top_days"] * 24).astype(int) - 1
        ok = (j >= 0) & (j < R.HOURS)
        days, top_a, top_s = ats["top_days"][ok], ats["top_T"][ok], sa["top"][j[ok]]
        y2 = days >= 365
        for label, sel in (("all", slice(None)), ("y1", ~y2), ("y2", y2)):
            m[f"top_cell_bias_{label}"], m[f"top_cell_rmse_{label}"] = R.bias(top_a[sel], top_s[sel]), R.rmse(top_a[sel], top_s[sel])
        m["top_cell_corr_y2"] = R.corr(top_a[y2], top_s[y2])
        m["_top"] = (days, top_a, top_s)
    m["heat_mean_ats"], m["heat_mean_sa"] = float(np.nanmean(A["heat"])), float(np.nanmean(sa["heat"]))
    m["heat_corr"] = R.corr(R.daily_mean(A["heat"]), R.daily_mean(sa["heat"]))
    m["snow_vapor_total_ats"] = float(np.nansum(A["snow_vapor"]))
    m["ground_vapor_total_sa"] = float(np.nansum(sa["ground_vapor"]))
    m["precip_sa_mm"] = float(sa["precip_total"])
    m["_series"] = (A, sa, orig)
    return m


def figure(m, label):
    A, sa, orig = m["_series"]
    days = np.arange(R.NDAYS) + 0.5
    hdays = (np.arange(R.HOURS) + 1) / 24.0
    fig, ax = plt.subplots(5, 1, figsize=(12, 13), sharex=True)
    ax[0].plot(hdays, A["snow_depth"], color=R.ATS_C, lw=1, label="ATS (matched variant)")
    ax[0].plot(hdays, sa["snow_depth"], color=R.SA_C, lw=1, ls="--", label="standalone")
    if orig is not None:
        ax[0].plot(hdays, R.aligned(orig["snow_depth"], m["alignment_shift_hours"]), color=ORIG_C, lw=0.8, label="ATS (original input)")
    ax[0].set_ylabel("Snow depth [m]")
    ax[0].legend(ncol=3, fontsize=8, loc="upper right")
    ax[1].plot(days, R.daily_mean(A["air"]), color="0.7", lw=0.8, label="air (daily mean)")
    ax[1].plot(days, R.daily_mean(A["skin"]), color=R.ATS_C, lw=1, label="ATS surface (skin)")
    ax[1].plot(days, R.daily_mean(sa["top"]), color=R.SA_C, lw=1, ls="--", label="standalone top soil layer")
    if m["_top"] is not None:
        d, a, _ = m["_top"]
        ax[1].plot(d, a, color="#08306b", lw=0, marker=".", ms=2.5, label="ATS top soil cell (daily snapshot)")
    ax[1].set_ylabel("Temperature [°C]")
    ax[1].legend(ncol=4, fontsize=8, loc="upper center")
    ax[2].axhline(0, color="0.8", lw=0.8)
    ax[2].plot(days, R.daily_mean(A["skin"]) - R.daily_mean(sa["top"]), color=R.ATS_C, lw=1)
    ax[2].set_ylabel("ATS skin minus\nstandalone top layer [K]")
    ax[3].plot(days, R.daily_mean(A["heat"]), color=R.ATS_C, lw=1)
    ax[3].plot(days, R.daily_mean(sa["heat"]), color=R.SA_C, lw=1, ls="--")
    ax[3].set_ylabel("Ground heat flux [W/m²]\n(daily mean)")
    ax[4].plot(days, R.daily_sum(A["snow_vapor"]), color=R.ATS_C, lw=1, label="ATS snow evaporation + sublimation")
    ax[4].plot(days, R.daily_sum(sa["ground_vapor"]), color=R.SA_C, lw=1, ls="--", label="standalone ground vapor flux (includes soil evaporation)")
    ax[4].set_ylabel("Vapor flux [mm/d]")
    ax[4].set_xlabel("Day of simulation")
    ax[4].legend(fontsize=8, loc="upper right")
    for a in ax:
        a.grid(alpha=0.25)
        a.axvline(365, color="0.8", lw=0.8)
    fig.suptitle(label, y=0.995, fontsize=13)
    fig.tight_layout()
    return fig


def html_page(m, health, health_orig, fig_b64, out):
    fmt = R.fmt
    seas = lambda s: "; ".join(f"d{x['start_day']}-{x['melt_out_day']} peak {x['peak']:.2f} m" for x in s) or "–"
    rows = [
        ("Snow depth", f"peak {fmt(m['snow_peak_ats'])} / {fmt(m['snow_peak_sa'])} m",
         f"daily r {fmt(m['snow_corr'])}, RMSE {fmt(m['snow_rmse'], '.3f')} m; days above 1 cm {m['snow_days_ats']} / {m['snow_days_sa']}"),
        ("Snow seasons (ATS)", seas(m["snow_seasons_ats"]), ""),
        ("Snow seasons (standalone)", seas(m["snow_seasons_sa"]), ""),
        ("Air temperature, forcing check", f"max |diff| {fmt(m['air_max_abs_diff_K'], '.2f')} K",
         "should be ~0 if both runs see the same forcing at the same hour"),
        ("Skin (ATS) vs top layer (SA), year 2", f"bias {fmt(m['skin_bias_y2'])} K, RMSE {fmt(m['skin_rmse_y2'])} K",
         f"daily r {fmt(m['skin_corr_y2'])}; year 1: bias {fmt(m['skin_bias_y1'])} K, RMSE {fmt(m['skin_rmse_y1'])} K; whole run: RMSE {fmt(m['skin_rmse_all'])} K"),
        ("Top soil cell (ATS snapshots) vs top layer (SA), year 2",
         f"bias {fmt(m.get('top_cell_bias_y2'))} K, RMSE {fmt(m.get('top_cell_rmse_y2'))} K",
         f"r {fmt(m.get('top_cell_corr_y2'))}; year 1: bias {fmt(m.get('top_cell_bias_y1'))} K, RMSE {fmt(m.get('top_cell_rmse_y1'))} K"),
        ("EcoSIM energy source to the ground, mean [W/m²]", f"{fmt(m['heat_mean_ats'], '.1f')} / {fmt(m['heat_mean_sa'], '.1f')}",
         f"daily r {fmt(m['heat_corr'])}. Not like for like: ATS is the raw EcoSIM source (before the linearization correction); the standalone is the heat flux into the ground"),
        ("Vapor flux, total [mm]", f"ATS snow {fmt(m['snow_vapor_total_ats'], '.1f')}; standalone ground {fmt(m['ground_vapor_total_sa'], '.1f')}",
         "not comparable: the standalone figure includes soil evaporation, the ATS observations do not"),
    ]
    for label, h in (("ATS run health (matched variant)", health), ("ATS run health (original input)", health_orig)):
        if h:
            rows.append((label, f"{h.get('steps')} steps, min dt {fmt(h.get('min_dt_s'), '.0f')} s, wall {h.get('wall_s')} s",
                         f"inadmissible-iterate warnings: energy {h.get('energy_warnings')}, flow {h.get('flow_warnings')}; "
                         f"solver FAIL {h.get('solver_fail')}; EcoSIM abort {h.get('ecosim_abort')}; status {h.get('status')}"))
    body = "".join(f"<tr><td class='l'>{html.escape(a)}</td><td class='l'>{html.escape(b)}</td><td class='l'>{html.escape(c)}</td></tr>"
                   for a, b, c in rows)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'><title>Bare ground: ATS-EcoSIM vs standalone EcoSIM</title>"
        f"<style>{R.CSS}</style></head><body><h1>Bare ground: ATS-EcoSIM against standalone EcoSIM</h1>"
        f"<p>Generated {now} from <code>pairs/{m['test']}/ats</code> and <code>pairs/{m['test']}/standalone</code>; "
        f"729 days, hourly, no plants. ATS flux and state observations are aligned to the standalone records with a shift of "
        f"{m['alignment_shift_hours']:+d} h (chosen on the air temperature, which both runs take from the same forcing).</p>"
        "<div class='note'>Not graded: there is no sweep to rank the pair against. The standalone has no skin temperature, so the "
        "surface comparison is the ATS skin against the standalone top soil layer, with the ATS top soil cell as the like-for-like "
        "check. The first winter is dominated by the initial condition (ATS starts the column at 260 K). The differences that remain "
        "between the two sides are listed in <code>pairs/README.md</code> (Not matched).</div>"
        f"<h2>Time series</h2><img src='data:image/png;base64,{fig_b64}'>"
        f"<h2>Diagnostics</h2><table><tr><th class='l'>diagnostic</th><th class='l'>ATS / standalone</th><th class='l'>detail</th></tr>{body}</table>"
        "</body></html>")


def build(m, out=None):
    """write the report of the bare-ground pair; returns an exit status"""
    R.configure(m)
    base = (m.ats.get("baseline") or {}).get("name")
    if not base:
        print("the suite has no baseline test")
        return 1
    out = Path(out) if out else R.PAIRS / "report"
    ats_dir, sa_dir = R.PAIRS / base / "ats", R.PAIRS / base / "standalone"
    if not (ats_dir / "surf_prop.dat").is_file() or not list(sa_dir.glob("*.ecosim.h0.*.nc")):
        print(f"skipping {base}: missing ATS or standalone output in pairs/{base}")
        return 1
    ats, sa = R.load_ats(base, "matched"), R.load_sa(base)
    orig = R.load_ats(base, "suite") if (R.SUITE_DIR / base / "surf_prop.dat").is_file() else None
    shift, scores = R.best_shift([(ats["air"], sa["air"])], "mae")
    print(f"alignment shift {shift:+d} h {scores}")
    m_ = metrics(base, ats, sa, shift, orig)
    m_["health"], m_["health_original"] = R.ats_health(base, "matched"), R.ats_health(base, "suite")
    fig = R.png(figure(m_, f"{base} - ATS (matched variant) vs standalone EcoSIM, no plants"),
                out / "figures" / f"{base}_timeseries.png")
    html_page(m_, m_["health"], m_["health_original"], fig, out / f"{base}.html")

    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if not k.startswith("_")}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, (np.floating, np.integer)):
            return x.item()
        return x
    (out / f"{base}.json").write_text(json.dumps(
        {"generated": datetime.datetime.now().isoformat(timespec="seconds"), **clean(m_)}, indent=1, default=float))
    print(f"wrote {out / (base + '.html')}")
    return 0
