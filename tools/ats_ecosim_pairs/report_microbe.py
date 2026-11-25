"""Report on a matched microbe pair: ATS-EcoSIM and standalone EcoSIM side by side.

Reads pairs/<test>/ats/ats_vis_data.h5 (daily), ats/bgc_sources.dat (hourly, if the input has it) and the
standalone's hourly history files, and writes pairs/report/<test>.html (figures embedded) and
pairs/report/index.html. The sections follow the one-year ATS-EcoSIM microbe report (biomass and its structure by
functional group, environment, solutes) and add the comparison with the standalone, in particular the
transport of the dissolved species and gases: ATS moves them with the water flux between cells, the standalone
solves its own diffusion, dissolution and gas exchange.

Needs numpy, h5py, netCDF4 and matplotlib.
"""

import base64
import glob
import html
import io
import json
from pathlib import Path

import h5py
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import netCDF4  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, LogNorm  # noqa: E402

M_C, M_N, M_P = 12.011, 14.007, 30.974
MOLAR_WATER = 55500.0            # mol m-3 of liquid water (ATS uses 55508 at 7 C; the difference is under 0.1 %)

SURF, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
ATS_C, SA_C = "#2a78d6", "#eb6834"
SER = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CM_BLUE = LinearSegmentedColormap.from_list("blue_seq", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])
CM_ORANGE = LinearSegmentedColormap.from_list("orange_seq", ["#fbe0d2", "#f6b999", "#f08e5f", "#eb6834", "#c24f22", "#913a18", "#63280f"])
plt.rcParams.update({
    "figure.facecolor": SURF, "savefig.facecolor": SURF, "axes.facecolor": SURF, "text.color": INK,
    "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
    "font.size": 9.5, "axes.titlesize": 10.5, "axes.titleweight": "semibold", "axes.titlecolor": INK,
    "legend.frameon": False, "legend.fontsize": 8.5, "lines.linewidth": 1.8, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
})

POOLS = ["kinetic", "structural", "reserve"]
CPLX = ["woody litter", "fine litter", "manure", "POM", "humus"]
HETER_FG = ["Aerobic bacteria", "Facultative denitrifiers", "Aerobic fungi", "Fermenters", "Acetoclastic methanogens",
            "Aerobic N2-fixers", "Anaerobic N2-fixers"]
AUTOR_FG = ["AOB", "NOB", "Aerobic methanotrophs", "ANME-2d", "Hydrogenotrophic methanogens", "NC10"]
# dissolved solutes: (ATS species, standalone history field, g of element per mol, label, unit basis)
SOLUTES = [("DOC", "DOC_vr", M_C, "DOC", "gC"), ("acetate", "acetate_vr", M_C, "Acetate", "gC"),
           ("NH4", "NH4_vr", M_N, "NH₄⁺", "gN"), ("NO3", "NO3_vr", M_N, "NO₃⁻", "gN"),
           ("DON", "DON_vr", M_N, "DON", "gN"), ("DOP", "DOP_vr", M_P, "DOP", "gP")]
# dissolved gases: concentrations per m3 of water
GASES = [("CO2", "CO2w_conc_vr", M_C, "CO₂", "gC"), ("O2", "O2w_conc_vr", 32.0, "O₂", "g"),
         ("CH4", "CH4w_conc_vr", M_C, "CH₄", "gC"), ("N2O", "N2Ow_conc_vr", 28.014, "N₂O", "gN"),
         ("NH3", "NH3w_conc_vr", M_N, "NH₃", "gN")]


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
class Pair:
    """the data of one pair, on the same layers and a common daily axis"""

    def __init__(self, pair_dir, meta):
        self.dir, self.meta = Path(pair_dir), meta
        self.n = meta["layers"]
        self.dz = np.array(meta["dz"])
        self.depth = np.cumsum(self.dz) - 0.5 * self.dz            # layer centres from the surface
        self.bottom = np.cumsum(self.dz)
        self.ndays = int(meta["ndays"])
        self.load_ats()
        self.load_standalone()

    # -- ATS ---------------------------------------------------------------
    def load_ats(self):
        f = h5py.File(self.dir / "ats" / "ats_vis_data.h5", "r")
        self.ats_file = f
        ts = sorted(f["temperature"].keys(), key=int)
        self.ats_hours = np.array([int(t) for t in ts])
        self.ats_days = self.ats_hours / 24.0
        self._ts = ts
        # layer order: the exodus mesh of a regraded column starts at the surface, a generated mesh at the bottom
        self.flip = not self.meta.get("regraded", False)
        p0 = self.g("pressure")[0]
        if self.n > 2 and self.meta.get("head") is not None and (p0[0] > p0[-1]) != self.flip:       # a saturated box has no gradient
            raise RuntimeError("ATS layer order disagrees with the pressure profile")
        self.ats_theta = self.g("saturation_liquid") * self.g("porosity")
        self.ats_T = self.g("temperature") - 273.15
        self.ats_S = self.g("saturation_liquid")
        self.species = [k[len("mole_fraction."):] for k in f.keys() if k.startswith("mole_fraction.")]
        self.H = self.A = None
        if "heter_biomass.0" in f:
            nh = sum(1 for k in f if k.startswith("heter_biomass."))
            na = sum(1 for k in f if k.startswith("autor_biomass."))
            self.H = np.array([self.g(f"heter_biomass.{k}") for k in range(nh)])      # (comp, t, layer) g per layer
            self.A = np.array([self.g(f"autor_biomass.{k}") for k in range(na)])
        self.q_z = self.g("darcy_velocity.z") if "darcy_velocity.z" in f else None
        self.sources = self.read_sources()

    def g(self, key):
        a = np.array([self.ats_file[key][t][:, 0] for t in self._ts])
        return a[:, ::-1] if self.flip else a

    def read_sources(self):
        path = self.dir / "ats" / "bgc_sources.dat"
        if not path.is_file():
            return None
        rows = [ln for ln in path.read_text().splitlines() if ln and not ln.startswith("#")]
        names = [x.strip('"') for x in rows[0].split(",")]
        arr = np.array([[float(x) for x in ln.split(",")] for ln in rows[1:]])
        return dict(t=arr[:, 0], **{n.replace("_source", ""): arr[:, i] for i, n in enumerate(names) if i})

    def ats_conc_soil(self, species, mw):
        """g of the element per m3 of soil, per layer, daily"""
        return self.g(f"mole_fraction.{species}") * MOLAR_WATER * self.ats_theta * mw

    def ats_conc_water(self, species, mw):
        return self.g(f"mole_fraction.{species}") * MOLAR_WATER * mw

    def ats_inventory_mol(self, species):
        return self.g(f"total_component_quantity.{species}")           # mol per layer (1 m2)

    # -- standalone ----------------------------------------------------------
    def load_standalone(self):
        files = sorted(glob.glob(str(self.dir / "standalone" / "*.ecosim.h0.*.nc")))
        if not files:
            raise RuntimeError(f"no standalone history in {self.dir / 'standalone'}")
        sets = [netCDF4.Dataset(x) for x in files]
        self._sets = sets
        self.sa_hours = sum(s.dimensions["time"].size for s in sets)
        # state sampled at the end of the hour that closes each day, like the ATS daily snapshots
        self.sa_idx = np.maximum(24 * np.arange(self.ndays + 1) - 1, 0)

    ALIASES = {"acetate_vr": "Acetate_vr"}           # spelled differently in the restructured EcoSIM

    def _name(self, name):
        if name not in self._sets[0].variables and self.ALIASES.get(name) in self._sets[0].variables:
            return self.ALIASES[name]
        return name

    def sa(self, name):
        """(hours, layers) or (hours,) of a history field of the standalone"""
        name = self._name(name)
        out = np.concatenate([np.ma.filled(s[name][:], np.nan) for s in self._sets if name in s.variables], axis=0)
        return out[:, : self.n, 0] if out.ndim == 3 else out[:, 0]

    def sa_has(self, name):
        return self._name(name) in self._sets[0].variables

    def sa_daily(self, name):
        return self.sa(name)[self.sa_idx]

    def sa_daily_mean(self, name):
        """mean over each day of a column flux, for days 0..ndays-1"""
        a = self.sa(name)
        return a[: self.ndays * 24].reshape(self.ndays, 24).mean(1)


# ---------------------------------------------------------------------------
# html helpers
# ---------------------------------------------------------------------------
CSS = (Path(__file__).with_name("report_microbe.css").read_text() if Path(__file__).with_name("report_microbe.css").is_file()
       else """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--blue:#2a78d6;--note:#2a78d6;--caution:#b45309}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
header.top{background:var(--surface);border-bottom:1px solid var(--grid);padding:28px 20px 18px}.wrap{max-width:1180px;margin:0 auto;padding-inline:20px}header.top .wrap{padding-inline:0}
h1{font-size:26px;line-height:1.2;margin:0 0 6px;font-weight:650}.sub{color:var(--ink2);margin:0;font-size:14px}
nav.toc{position:sticky;top:0;z-index:5;background:rgba(252,252,251,.95);border-bottom:1px solid var(--grid)}nav.toc .wrap{display:flex;gap:4px 16px;flex-wrap:wrap;padding-block:8px;font-size:13px}
nav.toc a{color:var(--ink2);text-decoration:none}nav.toc a:hover{color:var(--blue);text-decoration:underline}main{padding-block:22px 60px}
section{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:22px 26px 14px;margin:0 0 22px}h2{font-size:20px;margin:0 0 6px;font-weight:650}h3{font-size:15px;margin:22px 0 8px;font-weight:650}
p.lede{color:var(--ink2);margin:0 0 14px;max-width:80ch}figure{margin:14px 0 18px}figure img{width:100%;height:auto;display:block;border:1px solid var(--grid);border-radius:6px}
figcaption{color:var(--ink2);font-size:13px;margin-top:6px;max-width:100ch}code{font:12.5px ui-monospace,monospace;background:#f1f0ec;padding:1px 5px;border-radius:4px}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:18px 0}.kpi{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}
.kv{font-size:26px;font-weight:650;line-height:1.1}.ku{font-size:12px;font-weight:500;color:var(--ink2)}.kl{font-size:12.5px;color:var(--ink2);margin-top:6px}
.callout{display:flex;gap:10px;border:1px solid var(--grid);border-left:4px solid var(--note);background:var(--surface);border-radius:6px;padding:11px 14px;margin:14px 0;font-size:14px;max-width:110ch}
.callout.caution{border-left-color:var(--caution);background:#fdf6ee}.callout .ci{font-size:17px}.tw{overflow-x:auto;margin:10px 0 16px}
table{border-collapse:collapse;font-size:13px;min-width:min(100%,640px)}caption{caption-side:top;text-align:left;color:var(--ink2);font-size:12.5px;padding-bottom:5px}
th,td{padding:6px 12px;border-bottom:1px solid var(--grid);text-align:right;vertical-align:top}th:first-child,td:first-child{text-align:left}th{font-weight:600;color:var(--ink2)}
td{font-variant-numeric:tabular-nums}table.text th,table.text td{text-align:left}footer{color:var(--muted);font-size:12.5px;padding:0 0 40px}
@media (max-width:820px){.kpis{grid-template-columns:repeat(2,1fr)}}@media (max-width:520px){.kpis{grid-template-columns:1fr}}
""")


def b64(fig, dpi=110):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


class Page:
    def __init__(self):
        self.parts, self.toc, self.open_ = [], [], False

    def section(self, sid, title, lede=""):
        self.end()
        self.toc.append((sid, title))
        self.parts.append(f'<section id="{sid}"><h2>{html.escape(title)}</h2>' + (f'<p class="lede">{lede}</p>' if lede else ""))
        self.open_ = True

    def end(self):
        if self.open_:
            self.parts.append("</section>")
            self.open_ = False

    def h3(self, text):
        self.parts.append(f"<h3>{html.escape(text)}</h3>")

    def p(self, text):
        self.parts.append(f"<p>{text}</p>")

    def figure(self, fig, alt, caption):
        self.parts.append(f'<figure><img alt="{html.escape(alt)}" src="data:image/png;base64,{b64(fig)}">'
                          f"<figcaption>{caption}</figcaption></figure>")

    def callout(self, kind, label, body):
        icon = {"note": "ⓘ", "caution": "⚠"}[kind]
        self.parts.append(f'<div class="callout {kind}"><div class="ci">{icon}</div><div><b>{label}.</b> {body}</div></div>')

    def table(self, headers, rows, caption=None, text=False):
        head = "".join(f"<th>{h}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
        cap = f"<caption>{caption}</caption>" if caption else ""
        self.parts.append(f'<div class="tw"><table class="{"text" if text else ""}">{cap}<tr>{head}</tr>{body}</table></div>')

    def kpis(self, items):
        cells = "".join(f'<div class="kpi"><div class="kv">{v}</div><div class="kl">{lbl}</div></div>' for v, lbl in items)
        self.parts.append(f'<div class="kpis">{cells}</div>')

    def render(self, title, sub):
        self.end()
        nav = "".join(f'<a href="#{i}">{html.escape(t)}</a>' for i, t in self.toc)
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
                f'<header class="top"><div class="wrap"><h1>{html.escape(title)}</h1><p class="sub">{sub}</p></div></header>'
                f'<nav class="toc"><div class="wrap">{nav}</div></nav><main class="wrap">' + "".join(self.parts) + "</main></body></html>")


def fnum(x, digits=3):
    if x is None or not np.isfinite(x):
        return "&ndash;"
    if x == 0:
        return "0"
    if abs(x) >= 1e4 or abs(x) < 1e-2:
        return f"{x:.2e}"
    return f"{x:.{digits}g}" if abs(x) < 100 else f"{x:,.1f}"


def style(ax, grid="y"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_axisbelow(True)
    if grid:
        ax.grid(True, axis=grid, color=GRID, lw=0.6)
    ax.tick_params(length=3, width=0.6, color=AXIS)


def pos(x):
    x = np.asarray(x, float)
    return np.where(x > 0, x, np.nan)


# ---------------------------------------------------------------------------
# quantities on common axes
# ---------------------------------------------------------------------------
def n_guilds(pair, what="H"):
    """heterotroph or autotroph guilds in the ATS state arrays: 3 elements x guilds x 3 pools (x 5 complexes)"""
    arr = pair.H if what == "H" else pair.A
    return arr.shape[0] // (45 if what == "H" else 9)


def biomass_by(pair, elem=None, fg=None, pool=None, cplx=None, what="H"):
    """ATS biomass summed over the selected components: (t, layer) g per layer (m2 column)"""
    arr = pair.H if what == "H" else pair.A
    ng = n_guilds(pair, what)
    k = np.arange(arr.shape[0])
    e = k % 3
    g = (k // 3) % (3 * ng)
    m = np.ones(len(k), bool)
    if elem is not None:
        m &= e == elem
    if fg is not None:
        m &= g // 3 == fg
    if pool is not None:
        m &= g % 3 == pool
    if cplx is not None and what == "H":
        m &= k // (9 * ng) == cplx
    return arr[m].sum(0)


def sa_microbe(pair, el):
    """standalone total microbial biomass: (day, layer) g per layer"""
    name = {"C": "tMicrobeC_vr", "N": "tMicrobeN_vr", "P": "tMicrobeP_vr"}[el]
    return pair.sa_daily(name) * pair.dz


def depth_time(ax, days, depth, field, cmap, title, label, vmin=None, vmax=None, zmax=None):
    f = pos(field).T
    vmax = vmax or np.nanmax(f) if np.isfinite(f).any() else 1.0
    vmin = vmin or max(vmax * 1e-6, np.nanmin(f[np.isfinite(f)]) if np.isfinite(f).any() else 1e-9)
    edges_d = np.concatenate([[0], 0.5 * (days[1:] + days[:-1]), [days[-1]]])
    edges_z = np.concatenate([[0], 0.5 * (depth[1:] + depth[:-1]), [2 * depth[-1] - 0.5 * (depth[-1] + depth[-2])]])
    im = ax.pcolormesh(edges_d, edges_z, f, cmap=cmap, norm=LogNorm(vmin=vmin, vmax=vmax), shading="flat")
    ax.set_ylim(zmax or edges_z[-1], 0)
    ax.set_xlabel("day of run")
    ax.set_title(title)
    plt.colorbar(im, ax=ax, label=label, pad=0.02)
    style(ax, "")
    return im


# ---------------------------------------------------------------------------
# the report of one pair
# ---------------------------------------------------------------------------
def build_one(pair, readme_notes):
    m = pair.meta
    n, dz = pair.n, pair.dz
    days = pair.ats_days
    d0, d1 = days, np.arange(pair.ndays + 1)
    page = Page()
    top = min(n, 3)                                       # layers shown as time series
    mid = n // 2

    # ---- numbers used in the tiles
    ats_hC = biomass_by(pair, 0, what="H").sum(1) if pair.H is not None else None
    ats_aC = biomass_by(pair, 0, what="A").sum(1) if pair.A is not None else None
    sa_C = sa_microbe(pair, "C").sum(1)
    ats_mic = (ats_hC + ats_aC) if ats_hC is not None else None
    hr = -pair.sa_daily_mean("ECO_HR_CO2_col") if pair.sa_has("ECO_HR_CO2_col") else None       # gC m-2 h-1 -> positive
    items = []
    if ats_mic is not None:
        items.append((f"{ats_mic[0]:.2f} → {ats_mic[-1]:.1f} g C m⁻²", "ATS-EcoSIM microbial carbon, first → last day"))
    items.append((f"{sa_C[0]:.0f} → {sa_C[-1]:.0f} g C m⁻²", "standalone microbial carbon, first → last day"))
    if ats_mic is not None and ats_mic[0] > 0:
        items.append((f"×{sa_C[0] / ats_mic[0]:,.0f}", "standalone start-up biomass relative to the ATS seed"))
    if hr is not None:
        items.append((f"{hr.sum() * 24:.0f} g C m⁻²", f"standalone heterotrophic respiration over the {pair.ndays} days (CO₂-C)"))
    for sp, field, mw, lbl, unit in SOLUTES[:3]:
        if sp in pair.species and pair.sa_has(field):
            inv_a = (pair.ats_conc_soil(sp, mw) * dz).sum(1)
            inv_s = (pair.sa_daily(field) * dz).sum(1)
            items.append((f"{inv_a[0]:.1f} vs {inv_s[0]:.2g}", f"{lbl} column inventory, day 0, ATS vs standalone ({unit} m⁻²)"))
    page.kpis(items[:6])

    page.callout("caution", "Read the comparison with the start-up states in mind",
                 "The two models do not start from the same state, and the pair cannot make them: ATS seeds the microbes with a small "
                 "population and sets every dissolved species from the input's mole fractions, whereas the standalone derives the "
                 "microbial biomass from the soil organic carbon and its dissolved pools from the soil chemistry (see \"Not matched\" "
                 "in the last section). Absolute values therefore differ at day 0; the figures show absolute values and values "
                 "relative to day 0, and the budgets use changes.")
    ndays_txt = f"{pair.ndays} days"
    page.callout("note", "Calendar",
                 f"Both runs start on day of year {m['start_doy']} of {m['start_year']} (365-day years) and run {ndays_txt}; "
                 "the time axes count days of the run. The forcing of the input starts at its first record in both models.")

    # ---- 1 setup
    page.section("setup", "1 · The pair",
                 "What the two models were given. The ATS input is the one in the case folder with its data files rebased; the "
                 "standalone case is written from it.")
    rows = [["Input", f"<code>{html.escape(m['source_xml'])}</code>"],
            ["Column", f"{n} layers, {m['depth']:g} m, top layer {dz[0]:.3g} m" +
             (f" (graded; the input has {m['input_layers']} layers: the standalone holds 20 layer slots)" if m.get("regraded") else "")],
            ["Run", f"{ndays_txt} from day {m['start_doy']} of {m['start_year']}, EcoSIM step 1 h in both"],
            ["Plants", "none in both models" + (" (the input's prescribed phenology is dropped: the standalone has no matching input)"
                                                 if m.get("phenology_dropped") else "")],
            ["Soil", f"porosity {m['porosity']:g}; start temperature {m['T0']:g} K"
                     + (" (held at the bottom)" if m.get("bottom_T") else "")],
            ["Transported species", f"{len(m['components'])}: " + ", ".join(m["components"])],
            ["Standalone forcing", "near-neutral (the ATS input has no surface energy exchange)" if m.get("thermal_neutral")
             else "the input's forcing, hourly"],
            ["Microbial groups", f"{m['n_heter']} heterotroph and {m['n_autor']} autotroph (ATS state); the standalone reports totals"]]
    page.table(["Item", "Value"], rows, text=True)

    # ---- 2 environment
    page.section("env", "2 · Environment of the microbes",
                 "Temperature and water content of the layers where the biomass sits. The microbial rates depend strongly on both.")
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.4))
    lay = sorted({0, mid, n - 1})
    for j, L in enumerate(lay):
        c = SER[j]
        axes[0].plot(d0, pair.ats_T[:, L], color=c, label=f"layer {L + 1} ({pair.depth[L]:.2f} m)")
        axes[0].plot(d1, pair.sa_daily("TEMP_vr")[:, L], color=c, ls="--")
        axes[1].plot(d0, pair.ats_theta[:, L], color=c)
        axes[1].plot(d1, pair.sa_daily("VSPore_vr")[:, L], color=c, ls="--")
    axes[0].set_ylabel("temperature [°C]")
    axes[1].set_ylabel("water content [m³ m⁻³]")
    for a in axes:
        a.set_xlabel("day of run")
        style(a)
    axes[0].legend(title="solid: ATS, dashed: standalone", loc="upper left", fontsize=7.5)
    fig.tight_layout()
    page.figure(fig, "temperature and water content", "Temperature (left) and water content (right) of three layers. Solid: ATS; dashed: "
                "standalone. The standalone's water content is its pore volume (<code>VSPore_vr</code>), a different quantity from the ATS "
                "liquid fraction where the soil is not saturated.")

    # ---- 3 biomass totals
    page.section("biomass", "3 · Microbial biomass",
                 "Biomass summed over the column, per element. The standalone history has the total per layer; ATS also has the "
                 "functional groups, pools and substrate complexes (section 4).")
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.3))
    for ax, el, k in zip(axes, "CNP", range(3)):
        s_ = sa_microbe(pair, el).sum(1)
        if pair.H is not None:
            a_ = biomass_by(pair, k, what="H").sum(1) + biomass_by(pair, k, what="A").sum(1)
            ax.plot(d0, pos(a_), color=ATS_C, label="ATS-EcoSIM")
        ax.plot(d1, pos(s_), color=SA_C, ls="--", label="standalone")
        ax.set_yscale("log")
        ax.set_xlabel("day of run")
        ax.set_ylabel(f"{el} in biomass [g m⁻²]")
        style(ax)
    axes[0].legend()
    fig.tight_layout()
    page.figure(fig, "column biomass totals", "Microbial carbon, nitrogen and phosphorus in the column (all heterotroph and autotroph groups). "
                "Logarithmic axes: the two models start orders of magnitude apart.")
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.3))
    if ats_mic is not None and ats_mic[0] > 0:
        axes[0].plot(d0, ats_mic / ats_mic[0], color=ATS_C, label="ATS-EcoSIM")
    axes[0].plot(d1, sa_C / sa_C[0], color=SA_C, ls="--", label="standalone")
    axes[0].set_ylabel("biomass carbon / day 0")
    axes[0].set_yscale("log")
    axes[0].legend()
    if hr is not None:
        axes[1].plot(np.arange(pair.ndays), hr * 24, color=SA_C, ls="--", label="standalone, heterotrophic respiration")
    if pair.sources is not None and "CO2" in pair.sources:
        t = pair.sources["t"]
        dayidx = (t[1:] // 86400).astype(int)
        daily = np.bincount(dayidx, weights=pair.sources["CO2"][1:] * 3600.0 * M_C, minlength=pair.ndays)[: pair.ndays]
        axes[1].plot(np.arange(pair.ndays), daily, color=ATS_C, label="ATS, EcoSIM CO₂ source term")
    axes[1].set_ylabel("CO₂-C produced [g C m⁻² d⁻¹]")
    axes[1].legend()
    for a in axes:
        a.set_xlabel("day of run")
        style(a)
    fig.tight_layout()
    page.figure(fig, "relative growth and CO2 production", "Left: biomass carbon relative to its day-0 value. Right: carbon released as CO₂ "
                "per day: the standalone's heterotrophic respiration and, where the input observes it, ATS's EcoSIM CO₂ source term "
                "(an integral over the column, in mol per second).")

    if n >= 6:
        page.h3("Where the biomass is")
        zmax = min(pair.bottom[-1], 3.0)
        fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
        a_c = (biomass_by(pair, 0, what="H") + biomass_by(pair, 0, what="A")) / dz if pair.H is not None else None
        s_c = sa_microbe(pair, "C") / dz
        vmax = max(np.nanmax(pos(s_c)), np.nanmax(pos(a_c)) if a_c is not None else 0)
        vmin = vmax * 1e-6
        if a_c is not None:
            depth_time(axes[0], d0, pair.depth, a_c, CM_BLUE, "ATS-EcoSIM", "g C m⁻³", vmin, vmax, zmax)
        depth_time(axes[1], d1, pair.depth, s_c, CM_ORANGE, "standalone", "g C m⁻³", vmin, vmax, zmax)
        axes[0].set_ylabel("depth [m]")
        fig.tight_layout()
        page.figure(fig, "biomass depth-time", "Microbial carbon concentration (layer content divided by layer thickness) against depth and time, "
                    "same colour scale in both panels, down to 3 m.")

    # ---- 4 ATS functional groups
    if pair.H is not None:
        page.section("groups", "4 · Functional groups, substrates and pools (ATS-EcoSIM)",
                     "The ATS state carries every functional group, the pool (kinetic, structural, reserve) and, for the heterotrophs, "
                     "the substrate complex. The standalone history used here has totals only, so this section is ATS only. "
                     + ("" if n_guilds(pair, "H") == 7 else
                        f"This build has {n_guilds(pair, 'H')} heterotroph guilds; they are numbered, not named."))
        fig, axes = plt.subplots(1, 2, figsize=(12, 3.6))
        names_h = HETER_FG if n_guilds(pair, "H") == 7 else [f"heterotroph guild {j + 1}" for j in range(n_guilds(pair, "H"))]
        for j, nm in enumerate(names_h):
            axes[0].plot(d0, pos(biomass_by(pair, 0, fg=j, what="H").sum(1)), color=SER[j % 8], label=nm)
        axes[0].set_title("Heterotrophs, carbon [g C m⁻²]")
        names_a = AUTOR_FG if n_guilds(pair, "A") == 6 else [f"autotroph guild {j + 1}" for j in range(n_guilds(pair, "A"))]
        for j, nm in enumerate(names_a):
            axes[1].plot(d0, pos(biomass_by(pair, 0, fg=j, what="A").sum(1)), color=SER[j % 8], label=nm)
        axes[1].set_title("Autotrophs, carbon [g C m⁻²]")
        for a in axes:
            a.set_yscale("log")
            a.set_xlabel("day of run")
            a.legend(fontsize=7.5, ncol=2)
            style(a)
        fig.tight_layout()
        page.figure(fig, "growth by functional group", "Carbon of each functional group, summed over the column and the complexes.")
        fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.4))
        for c in range(5):
            axes[0].plot(d0, pos(biomass_by(pair, 0, cplx=c, what="H").sum(1)), color=SER[c], label=CPLX[c])
        axes[0].set_title("Heterotrophs by substrate complex [g C m⁻²]")
        axes[0].legend(fontsize=7.5)
        axes[0].set_yscale("log")
        for p_ in range(3):
            axes[1].plot(d0, pos(biomass_by(pair, 0, pool=p_, what="H").sum(1)), color=SER[p_], label=POOLS[p_])
        axes[1].set_title("Heterotroph carbon by pool [g C m⁻²]")
        axes[1].legend(fontsize=7.5)
        axes[1].set_yscale("log")
        cn = np.where(biomass_by(pair, 1, what="H").sum(1) > 0, biomass_by(pair, 0, what="H").sum(1) / np.where(biomass_by(pair, 1, what="H").sum(1) > 0, biomass_by(pair, 1, what="H").sum(1), 1), np.nan)
        cp = np.where(biomass_by(pair, 2, what="H").sum(1) > 0, biomass_by(pair, 0, what="H").sum(1) / np.where(biomass_by(pair, 2, what="H").sum(1) > 0, biomass_by(pair, 2, what="H").sum(1), 1), np.nan)
        axes[2].plot(d0, cn, color=ATS_C, label="ATS C:N")
        axes[2].plot(d0, cp / 10.0, color=SER[1], label="ATS C:P / 10")
        sC, sN, sP = (sa_microbe(pair, e).sum(1) for e in "CNP")
        axes[2].plot(d1, np.where(sN > 0, sC / np.where(sN > 0, sN, 1), np.nan), color=ATS_C, ls="--", label="standalone C:N")
        axes[2].plot(d1, np.where(sP > 0, sC / np.where(sP > 0, sP, 1), np.nan) / 10.0, color=SER[1], ls="--", label="standalone C:P / 10")
        axes[2].set_title("Community stoichiometry (mass ratios)")
        axes[2].legend(fontsize=7.5)
        for a in axes:
            a.set_xlabel("day of run")
            style(a)
        fig.tight_layout()
        page.figure(fig, "complexes, pools, stoichiometry", "Left: heterotroph carbon by substrate complex. Middle: by biomass pool. Right: "
                    "C:N and C:P (divided by 10) of the whole community in both models.")

    # ---- 5/6 solutes and gases
    def species_section(sid, title, lede, specs, basis):
        avail = [s for s in specs if s[0] in pair.species and pair.sa_has(s[1])]
        if not avail:
            return
        page.section(sid, title, lede)
        plotted = avail[:4]                                  # the others are in the table
        nrow = len(plotted)
        fig, axes = plt.subplots(nrow, 3, figsize=(13, 2.7 * nrow), squeeze=False)
        snaps = [0, pair.ndays // 4, pair.ndays // 2, pair.ndays]
        for r, (sp, field, mw, lbl, unit) in enumerate(plotted):
            if basis == "soil":
                a_conc = pair.ats_conc_soil(sp, mw)
                s_conc = pair.sa_daily(field)
                inv_a, inv_s = (a_conc * dz).sum(1), (s_conc * dz).sum(1)
                u = f"{unit} m⁻³ soil"
            else:
                a_conc = pair.ats_conc_water(sp, mw)
                s_conc = pair.sa_daily(field)
                inv_a = pair.ats_inventory_mol(sp).sum(1) * mw
                th_s = pair.sa_daily("VSPore_vr")
                inv_s = (s_conc * th_s * dz).sum(1)
                u = f"{unit} m⁻³ water"
            ax = axes[r, 0]
            ax.plot(d0, pos(inv_a), color=ATS_C, label="ATS")
            ax.plot(d1, pos(inv_s), color=SA_C, ls="--", label="standalone")
            ax.set_yscale("log")
            ax.set_ylabel(f"{lbl} in the column\n[{unit} m⁻²]")
            style(ax)
            ax = axes[r, 1]
            for L in sorted({0, mid}):
                c = SER[0] if L == 0 else SER[2]
                ax.plot(d0, pos(a_conc[:, L]), color=c, label=f"layer {L + 1}")
                ax.plot(d1, pos(s_conc[:, L]), color=c, ls="--")
            ax.set_yscale("log")
            ax.set_ylabel(f"{lbl} [{u}]")
            style(ax)
            ax = axes[r, 2]
            for j, sd in enumerate(snaps):
                ia = int(np.argmin(np.abs(d0 - sd)))
                ax.plot(pos(a_conc[ia]), pair.depth, color=SER[j], label=f"day {sd}")
                ax.plot(pos(s_conc[sd]), pair.depth, color=SER[j], ls="--")
            ax.set_xscale("log")
            ax.invert_yaxis()
            ax.set_ylabel("depth [m]")
            style(ax, "x")
            if r == 0:
                axes[r, 0].legend()
                axes[r, 1].legend(title="solid ATS, dashed standalone", fontsize=7.5)
                axes[r, 2].legend(fontsize=7.5)
            if r == nrow - 1:
                for c in range(3):
                    axes[r, c].set_xlabel("day of run" if c < 2 else f"concentration [{u}]")
        fig.tight_layout()
        page.figure(fig, f"{title} comparison", "Left: column inventory. Middle: concentration in the surface layer and a middle layer. Right: "
                    "profiles on four days. Solid lines ATS-EcoSIM, dashed the standalone; zero values are not drawn on the logarithmic axes. "
                    + (f"Not drawn, but in the table: {', '.join(s[3] for s in avail[4:])}." if len(avail) > 4 else ""))
        rows = []
        for sp, field, mw, lbl, unit in avail:
            if basis == "soil":
                ia = (pair.ats_conc_soil(sp, mw) * dz).sum(1)
                is_ = (pair.sa_daily(field) * dz).sum(1)
            else:
                ia = pair.ats_inventory_mol(sp).sum(1) * mw
                is_ = (pair.sa_daily(field) * pair.sa_daily("VSPore_vr") * dz).sum(1)
            pick = [0, pair.ndays // 4, pair.ndays // 2, pair.ndays]
            rows.append([f"{lbl} ({unit} m⁻²)"] + [f"{fnum(ia[int(np.argmin(np.abs(d0 - d)))])} / {fnum(is_[d])}" for d in pick])
        page.table(["Inventory, ATS / standalone"] + [f"day {d}" for d in [0, pair.ndays // 4, pair.ndays // 2, pair.ndays]], rows,
                   caption="Column inventories (data table for the figure).")

    species_section("solutes", "5 · Dissolved carbon and nutrients",
                    "Concentrations per m³ of soil, and inventories, of the dissolved organic species and the inorganic nitrogen. "
                    "ATS: mole fraction × water density × water content × molar mass; standalone: the history field.", SOLUTES, "soil")
    species_section("gases", "6 · Dissolved gases",
                    "Gases dissolved in the soil water, per m³ of water. ATS carries the gases as aqueous species "
                    "(<code>number of gaseous components</code> is 0), so these are the only gas state it has.", GASES, "water")

    # ---- 7 production and transport
    page.section("transport", "7 · Production and transport",
                 "How much of what the microbes make or consume stays in the column, and how each model moves the dissolved species. "
                 "ATS moves them only with the water flux between cells (the inputs set no molecular diffusion and no gas phase); the "
                 "standalone solves diffusion, dissolution and gas exchange with the atmosphere itself.")
    if pair.sources is not None:
        page.h3("ATS: change in inventory against the cumulative EcoSIM source")
        t = pair.sources["t"]
        sp_rows, sp_names = [], []
        nsp = min(6, len([s for s in pair.species if s in pair.sources]))
        picks = [s for s in ("CO2", "O2", "NH4", "NO3", "DOC", "CH4", "acetate") if s in pair.sources and s in pair.species][:6]
        fig, axes = plt.subplots(2, 3, figsize=(12.5, 6.2))
        for ax, sp in zip(axes.ravel(), picks):
            mol = pair.ats_inventory_mol(sp).sum(1)
            cum = np.concatenate([[0.0], np.cumsum(pair.sources[sp][1:] * 3600.0)])
            hours = np.arange(len(cum))
            ax.plot(d0, mol - mol[0], color=ATS_C, label="Δ inventory")
            ax.plot(hours / 24.0, cum, color=SA_C, label="Σ source")
            ax.plot(d0, (mol - mol[0]) - cum[np.minimum(pair.ats_hours, len(cum) - 1)], color=SER[2], ls=":", label="difference = transport across the boundaries")
            ax.set_title(sp)
            ax.set_xlabel("day of run")
            ax.set_ylabel("mol in the column")
            style(ax)
            end_diff = (mol[-1] - mol[0]) - cum[min(pair.ats_hours[-1], len(cum) - 1)]
            sp_rows.append([sp, fnum(mol[-1] - mol[0]), fnum(cum[min(pair.ats_hours[-1], len(cum) - 1)]), fnum(end_diff)])
        axes.ravel()[0].legend(fontsize=7.5)
        for ax in axes.ravel()[len(picks):]:
            ax.axis("off")
        fig.tight_layout()
        page.figure(fig, "ATS source and transport budget", "For each species: how the column inventory changed (blue), the cumulative source EcoSIM gave "
                    "ATS (orange, the <code>subsurface_ecosim_mole_fraction_source</code> observation integrated over time), and what is left (dotted): "
                    "net transport across the column boundaries (top: infiltration, evaporation and the surface; bottom: the bottom boundary).")
        page.table(["Species", "Δ inventory [mol]", "Σ source [mol]", "transport across boundaries [mol]"], sp_rows,
                   caption=f"Budget over {pair.ndays} days, ATS-EcoSIM (negative transport = lost from the column).")
    else:
        page.p("The ATS input has no source-term observation, so the budget against the cumulative EcoSIM source is not shown for this pair.")
    page.h3("Water movement and dissolved-species fluxes")
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.4))
    if pair.q_z is not None:
        for j, L in enumerate(sorted({0, mid, n - 1})):
            axes[0].plot(d0, pair.q_z[:, L] * 86400.0 * 1000.0, color=SER[j], label=f"layer {L + 1}")
        axes[0].set_ylabel("ATS vertical Darcy velocity [mm d⁻¹]")
        axes[0].legend(fontsize=7.5)
    cum_days = np.arange(pair.ndays + 1)
    if hr is not None:
        axes[1].plot(cum_days, np.concatenate([[0.0], np.cumsum(hr * 24)]), color=SA_C, ls="--", label="standalone: heterotrophic respiration")
    if pair.sources is not None and "CO2" in pair.sources:
        cs = np.concatenate([[0.0], np.cumsum(pair.sources["CO2"][1:] * 3600.0 * M_C)])
        axes[1].plot(np.arange(len(cs)) / 24.0, cs, color=ATS_C, label="ATS: EcoSIM CO₂ source")
    axes[1].set_ylabel("cumulative CO₂-C produced [g C m⁻²]")
    axes[1].legend(fontsize=7.5)
    flux_series = [("CH4_DIF_FLX_col", "CH₄ diffusive flux to the atmosphere [µmol C m⁻² s⁻¹]", 1.0),
                   ("N2O_SEMIS_FLX_col", "N₂O flux [g N m⁻² h⁻¹]", 1.0)]
    shown = False
    for name, lbl, k in flux_series:
        if pair.sa_has(name):
            axes[2].plot(np.arange(pair.ndays), pair.sa_daily_mean(name) * k, label=lbl.split(" [")[0])
            shown = True
    axes[2].set_ylabel("standalone surface fluxes (EcoSIM units)")
    if shown:
        axes[2].legend(fontsize=7.5)
    for a in axes:
        a.set_xlabel("day of run")
        style(a)
    fig.tight_layout()
    page.figure(fig, "water movement and fluxes", "Left: ATS vertical water flux at three layers (positive up); it carries the dissolved species. Middle: the "
                "carbon each model's microbes released as CO₂, cumulatively. Right: the standalone's gas fluxes to the atmosphere, which have no counterpart in the ATS transport of "
                "these inputs: a gas produced in ATS leaves the column only dissolved in moving water.")
    if hr is not None:
        co2_sa = hr.sum() * 24
        rows = []
        text = []
        if pair.sa_has("CO2w_conc_vr"):
            inv_s = (pair.sa_daily("CO2w_conc_vr") * pair.sa_daily("VSPore_vr") * dz).sum(1)
            gone = co2_sa - (inv_s[-1] - inv_s[0])
            rows.append(["standalone", f"{co2_sa:.1f}", f"{inv_s[-1] - inv_s[0]:+.2f}", f"{gone:.1f}",
                         "respiration minus the change of dissolved CO₂: what left through the surface"])
        if pair.sources is not None and "CO2" in pair.sources and "CO2" in pair.species:
            cum = np.sum(pair.sources["CO2"][1:] * 3600.0) * M_C
            dinv = (pair.ats_inventory_mol("CO2").sum(1)[-1] - pair.ats_inventory_mol("CO2").sum(1)[0]) * M_C
            rows.insert(0, ["ATS-EcoSIM", f"{cum:.1f}", f"{dinv:+.1f}", f"{cum - dinv:.1f}",
                            "source minus the change of dissolved CO₂: what was carried out of the column by the water"])
            text.append(f"In ATS the column's dissolved CO₂ changed by {dinv:+.1f} g C m⁻² while EcoSIM added {cum:.1f} g C m⁻², so "
                        f"{cum - dinv:.1f} g C m⁻² left with the moving water. The dissolved CO₂ is the only CO₂ ATS holds, "
                        "and its start-up value is set by the input.")
        page.table(["Model", "CO₂-C produced [g C m⁻²]", "Δ dissolved CO₂-C [g C m⁻²]", "left the column [g C m⁻²]", "how"], rows, text=True,
                   caption="Carbon budget of CO₂ over the run. The microbial communities differ by orders of magnitude, so the totals are not rates of the same population.")
        for x in text:
            page.p(x)

    # ---- 8 method
    page.section("method", "Method, matching and checks")
    chk = [["Layers", f"{n} (ATS cell volumes / area 1 m² equal the pair's thicknesses)"],
           ["ATS snapshots", f"{len(pair.ats_hours)} daily (hour 0 to {int(pair.ats_hours[-1])})"],
           ["Standalone samples", f"{pair.sa_hours} hourly in {len(pair._sets)} files; day d is the sample ending at hour 24d"],
           ["ATS solute concentration", "mole fraction × 55 500 mol m⁻³ × liquid saturation × porosity × molar mass of the element "
                                      "(DOC and acetate counted as carbon, DON as nitrogen)"],
           ["Dissolved gas", "per m³ of water: mole fraction × 55 500 × molar mass (CO₂ and CH₄ as carbon, N₂O as nitrogen)"],
           ["Biomass", "ATS: g per layer from the heterotroph and autotroph state arrays (element = index mod 3); "
                       "standalone: <code>tMicrobeC_vr</code> etc. [g m⁻³] × layer thickness"]]
    page.table(["Check / definition", "Value"], chk, text=True)
    page.h3("What the pair does not match")
    page.parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in readme_notes) + "</ul>")
    return page


def read_not_matched(case):
    path = case.pairs_dir / "README.md"
    if not path.is_file():
        return []
    notes, on = [], False
    for ln in path.read_text().splitlines():
        if ln.startswith("## Not matched"):
            on = True
        elif on and ln.startswith("## "):
            break
        elif on and ln.startswith("- "):
            notes.append(ln[2:].replace("**", "").replace("`", ""))
    return notes


def build_report(case, only=None, out=None):
    out = Path(out) if out else case.pairs_dir / "report"
    out.mkdir(parents=True, exist_ok=True)
    tests = case.tests(only)
    notes = read_not_matched(case)
    done = []
    status = 0
    for t in tests:
        meta = case.meta(t.name)
        if meta is None:
            print(f"  {t.name}: no pair (run `pair` first)")
            status = 1
            continue
        try:
            pair = Pair(case.pair_dir(t.name), meta)
            page = build_one(pair, notes)
        except Exception as exc:                      # noqa: BLE001 - one broken pair must not hide the others
            print(f"  {t.name}: report failed: {type(exc).__name__}: {exc}")
            status = 1
            continue
        title = f"ATS-EcoSIM and standalone EcoSIM: {t.name}"
        sub = (f"{pair.n} layers · {pair.ndays} days · {len(meta['components'])} transported species · microbial model on in both")
        (out / f"{t.name}.html").write_text(page.render(title, sub))
        print(f"  wrote {out / (t.name + '.html')}")
        done.append(t.name)
    if done:
        links = "".join(f'<li><a href="{n}.html">{html.escape(n)}</a></li>' for n in done)
        (out / "index.html").write_text(f'<!doctype html><meta charset="utf-8"><title>{case.name}</title><h1>{case.name}</h1><ul>{links}</ul>')
    return status
