"""Matched ATS-EcoSIM input and standalone EcoSIM case of a multi-column EcoSIM namelist.

The namelist (cases/<case>/<test>.namelist) describes a row of columns, for example a hillslope of
six 40 m columns with a slope each and a plant type each. For every namelist of a case folder this writes
pairs/<test>/ in the same layout the single-column pairs use, so `run`, `summarize` and `report` work on it:

  hillslope.exo          the ATS mesh: the columns side by side with the surface elevation integrated from the
                         slopes of the grid file (as EcoSIM does), one layer set per soil layer
  forcing.h5             ATS forcing (hourly) from the climate file of the namelist, and the daily LAI and SAI
                         of every column from the monthly prescribed phenology
  ats_match.xml          the ATS-EcoSIM input: soil retention, porosity, conductivity per layer, PFT and LAI of
                         each column, the water-table and surface outlet boundaries of the grid file
  grid.nc, sp_phenol.nc  the grid file and the prescribed phenology as the standalone run reads them
  standalone.namelist    the namelist with the run, the output and the file names changed
  pair.json              what was matched (read by the report)

Everything is derived from the namelist and the files it names (grid, phenology, climate); what cannot be
derived (the ATS solver settings, the template of the input) comes from templates/ats_ecosim_column.xml.
Needs numpy, h5py and netCDF4 (and the ATS meshing tools).
"""

import json
import math
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import h5py
import netCDF4
import numpy as np

from . import namelist as nlmod
from .case import BOTTOM_OFFSET, CaseError
from .pairs import copy_structure, meshing_ats_module, water_viscosity
from .xmledit import param, parse_xml, plist

TEMPLATE = Path(__file__).with_name("templates") / "ats_ecosim_column.xml"
MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
FILL = 1.0e10                       # larger values in the climate file are fill (padding of the 366th day)
GRAVITY = 9.80665
RHO = 1000.0
P_ATM = 101325.0
SURFACE_LAYER_ID = 1001             # element block of layer 1 in the mesh; layer k has 1000 + k
SOLID_DENSITY_REFERENCE = "the particle density of the options (EcoSIM derives porosity from bulk density with it)"


# ---------------------------------------------------------------------------
# reading the namelist and the files it names
# ---------------------------------------------------------------------------
def resolve_file(name, base, ecosim, what):
    """a file of the namelist: next to the namelist (relative to its folder), else by name in the EcoSIM input_data"""
    path = (Path(base) / name)
    if path.is_file():
        return path.resolve()
    named = Path(ecosim) / "input_data" / Path(name).name
    if named.is_file():
        return named.resolve()
    raise CaseError(f"{what} {name!r} of the namelist was found neither relative to {base} nor as "
                    f"{named.name} in {named.parent} (see --ecosim-src)")


def read_grid(path):
    d = netCDF4.Dataset(path)
    d.set_auto_mask(False)
    try:
        return {k: np.asarray(d[k][...]) for k in d.variables}
    finally:
        d.close()


def vg_fit(phi, theta_fc, theta_wp, pc_fc, pc_wp):
    """van Genuchten alpha [Pa-1], n and residual saturation through the field capacity and wilting point

    The two points (water content at the matric potentials of field capacity and wilting point) fix alpha
    and n for a residual saturation of half the saturation at the wilting point."""
    s_fc, s_wp = theta_fc / phi, theta_wp / phi
    if not (1.0 > s_fc > s_wp > 0.0):
        raise CaseError("no van Genuchten curve passes through the field capacity and wilting point "
                        f"(porosity {phi:.3f}, {theta_fc:.3f}, {theta_wp:.3f}): need porosity > field capacity > wilting point > 0")
    sr = 0.5 * s_wp

    def alphas(n):
        m = 1.0 - 1.0 / n
        out = []
        for s, pc in ((s_fc, pc_fc), (s_wp, pc_wp)):
            se = (s - sr) / (1.0 - sr)
            out.append((se ** (-1.0 / m) - 1.0) ** (1.0 / n) / pc)
        return out

    def f(n):
        a_fc, a_wp = alphas(n)
        return math.log(a_wp) - math.log(a_fc)

    lo, hi = 1.02, 12.0
    if f(lo) * f(hi) > 0:
        raise CaseError("no van Genuchten curve passes through the field capacity and wilting point "
                        f"(porosity {phi:.3f}, {theta_fc:.3f}, {theta_wp:.3f})")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if f(lo) * f(mid) > 0 else (lo, mid)
    n = 0.5 * (lo + hi)
    return alphas(n)[0], n, sr


def daily_phenology(monthly, ndays):
    """EcoSIM's daily value from 12 monthly ones: linear between the middles of the months (PrescribePhenolMod)"""
    out, day = [], 0
    while len(out) < ndays:
        for kmo, mdays in enumerate(MONTH_DAYS, start=1):
            for dom in range(1, mdays + 1):
                t = (dom - 0.5) / mdays
                it1 = int(t + 0.5)
                m1, m2 = kmo + it1 - 1, kmo + it1
                m1 = 12 if m1 < 1 else m1
                m2 = 1 if m2 > 12 else m2
                w1 = (it1 + 0.5) - t
                out.append(w1 * monthly[m1 - 1] + (1.0 - w1) * monthly[m2 - 1])
                if len(out) == ndays:
                    return np.array(out)
    return np.array(out)


def plan_hillslope(test, cfg, ecosim, populations):
    """Everything the matched pair of a namelist needs, read from the namelist and its files."""
    name = test.name
    try:
        groups = nlmod.parse(test.source.read_text())
        nl = groups["ecosim"]
    except (KeyError, ValueError) as err:
        raise CaseError(f"{test.source.name}: no &ecosim group in the namelist ({err})") from None
    base = test.source.parent
    if not nl.get("ldo_sp_mode"):
        raise CaseError(f"{test.source.name}: only prescribed-phenology namelists (ldo_sp_mode = .true.) can be matched, "
                        "the coupling does not run EcoSIM's plant model")
    files = {
        "grid": resolve_file(nl["grid_file_in"], base, ecosim, "grid_file_in"),
        "phenology": resolve_file(nl["sp_phenol_in"], base, ecosim, "sp_phenol_in"),
        "climate": resolve_file(nl["clm_hour_file_in"], base, ecosim, "clm_hour_file_in"),
        "pft": resolve_file(nl["pft_file_in"], base, ecosim, "pft_file_in"),
        "ghg": resolve_file(nl["atm_ghg_in"], base, ecosim, "atm_ghg_in"),
    }
    g = read_grid(files["grid"])
    ncol = int(g["DHI"].shape[-1])
    if g["DVI"].shape[-1] != 1:
        raise CaseError(f"{name}: the grid has {g['DVI'].shape[-1]} rows; the matched pair supports a single row of columns")
    nh1, nh2, nv1, nv2 = (np.asarray(g[k]).astype(int).ravel() for k in ("NH1", "NH2", "NV1", "NV2"))
    if not (np.all(nh1 == nh2) and np.all(nv1 == 1) and np.all(nv2 == 1) and sorted(nh1) == list(range(1, ncol + 1))):
        raise CaseError(f"{name}: each topographic unit must be one column of the single row (NH1 = NH2, NV1 = NV2 = 1)")
    order = np.argsort(nh1)                                   # topographic unit of column 1, 2, ...
    dx = g["DHI"].reshape(-1)[:ncol].astype(float)
    width = float(g["DVI"].reshape(-1)[0])
    slope_deg = np.asarray(g["SL0"], float).ravel()[order]
    aspect = np.asarray(g["ASPX"], float).ravel()[order]
    az = np.radians((450.0 - aspect) % 360.0)                 # geometric azimuth (EcoSIM, readimod.F90)
    if np.any(np.abs(np.sin(az)) > 1e-9):
        raise CaseError(f"{name}: the aspect must be 90 or 270 degrees (down the row of columns); got {sorted(set(aspect))}. "
                        "A transect has no slope across it")
    dzdx = -np.tan(np.radians(slope_deg)) * np.cos(az)        # dz/dx of each column, EcoSIM StartsMod.F90
    x_nodes = np.concatenate([[0.0], np.cumsum(dx)])
    z_nodes = np.concatenate([[0.0], np.cumsum(dzdx * dx)])
    z_nodes = z_nodes - z_nodes.min()                          # lowest surface node at z = 0
    centers = 0.5 * (z_nodes[:-1] + z_nodes[1:])

    unit = int(order[0])
    n = int(g["NJ"].ravel()[unit] + g["NL1"].ravel()[unit] + g["NL2"].ravel()[unit])
    if int(g["NUI"].ravel()[unit]) != 1:
        raise CaseError(f"{name}: NUI must be 1")
    soil = {}
    for key in ("CDPTH", "BKDSI", "FC", "WP", "SCNV", "SCNH", "THW"):
        rows = np.asarray(g[key], float)[order][:, :n]
        if not np.allclose(rows, rows[0], rtol=1e-6, atol=1e-9):
            raise CaseError(f"{name}: {key} differs between the columns; the matched pair supports one soil profile")
        soil[key] = rows[0]
    if np.any(soil["FC"] <= 0) or np.any(soil["WP"] <= 0):
        raise CaseError(f"{name}: FC and WP of the grid file are -1 (EcoSIM computes them from texture and bulk density). "
                        "The ATS retention curve is fitted to them, so write them out: run the namelist, read "
                        "FieldCapacity and WiltPoint_vr of its restart file and put them into the grid file")
    if not np.allclose(soil["THW"], 1.0):
        raise CaseError(f"{name}: THW must be 1 (initial water at field capacity) in layers 1-{n}")
    bottoms = soil["CDPTH"]
    dz = np.diff(np.concatenate([[0.0], bottoms]))
    if np.any(dz <= 0):
        raise CaseError(f"{name}: CDPTH must increase with depth")
    if int(np.asarray(g["IETYPG"]).ravel()[0]) != 0:
        raise CaseError(f"{name}: IETYPG must be 0 (a climate zone would change the PFT code of the phenology file)")
    psi_fc = float(np.asarray(g["PSIFC"], float).ravel()[unit])
    psi_wp = float(np.asarray(g["PSIWP"], float).ravel()[unit])

    # prescribed phenology: one PFT per column, monthly LAI, SAI and height
    sp = netCDF4.Dataset(files["phenology"])
    sp.set_auto_mask(False)
    try:
        if sp.dimensions["year"].size != 1 or sp.dimensions["maxpfts"].size != 1:
            raise CaseError(f"{name}: the phenology file must be a one-year climatology with one PFT per column")
        sp_unit = {k: np.asarray(sp[k][...]) for k in ("NH1", "pft_type", "PlantPopuLive_pft", "LAI", "SAI", "HTOP")}
    finally:
        sp.close()
    sp_order = np.argsort(sp_unit["NH1"].astype(int))
    codes = [b"".join(sp_unit["pft_type"][0, u, 0]).decode().strip("\x00 ") for u in sp_order]
    pft_names = _pft_names(files["pft"])
    heights_table = _pft_heights(files["pft"])
    rows = []
    for c, code in enumerate(codes, start=1):
        if code not in pft_names:
            raise CaseError(f"{name}: column {c}: PFT {code!r} is not in {files['pft'].name}")
        rows.append(pft_names.index(code) + 1)
    lai = np.array([sp_unit["LAI"][0, u, 0, :] for u in sp_order], float)
    sai = np.array([sp_unit["SAI"][0, u, 0, :] for u in sp_order], float)
    htop = np.array([sp_unit["HTOP"][0, u, 0, :] for u in sp_order], float)
    pop = np.array([sp_unit["PlantPopuLive_pft"][0, u, 0] for u in sp_order], float)

    years = list(cfg["years"])
    ndays = int(cfg["days"])
    if ndays > 365 * len(years) or ndays < 1:
        raise CaseError(f"{name}: the run is {ndays} days; the matched pair needs at most {365 * len(years)} "
                        f"(the standalone years {tuple(years)} have 365 days)")
    latitude = float(np.asarray(g["ALATG"], float).ravel()[0])
    clim = netCDF4.Dataset(files["climate"])
    clim.set_auto_mask(False)
    try:
        file_years = [int(y) for y in clim["year"][:]]
        missing = [y for y in years if y not in file_years]
        if missing:
            raise CaseError(f"{name}: the climate file has no year {missing} (it has {file_years[0]}-{file_years[-1]})")
        idx = [file_years.index(y) for y in years]
        noon = float(np.asarray(clim["ZNOONG"][idx[0]], float).ravel()[0])
        wind_height = float(np.asarray(clim["Z0G"][idx[0]], float).ravel()[0])
        forcing = {}
        for key in ("TMPH", "WINDH", "RAINH", "DWPTH", "SRADH"):
            a = np.asarray(clim[key][idx], float)[:, :365, :, 0]            # (year, day, hour)
            if not np.all(np.isfinite(a)) or np.abs(a).max() > FILL:
                raise CaseError(f"{name}: {key} of the climate file has missing values in {years}")
            forcing[key] = a.reshape(-1)
    finally:
        clim.close()

    start_T = cfg["start_temperature"]
    atca = float(np.asarray(g["ATCAG"], float).ravel()[0])
    T0 = atca + 273.15 if start_T == "xml" else float(start_T)
    bottom_T = round(T0 + BOTTOM_OFFSET, 6) if start_T != "xml" else round(atca + 273.15 + BOTTOM_OFFSET, 6)

    pd = float(cfg["particle_density"])
    phi = 1.0 - soil["BKDSI"] / pd
    theta_fc, theta_wp = soil["FC"], soil["WP"]
    if not np.all((phi > theta_fc) & (theta_fc > theta_wp) & (theta_wp > 0)):
        raise CaseError(f"{name}: need porosity > field capacity > wilting point > 0 in every layer")
    vg = [vg_fit(phi[k], theta_fc[k], theta_wp[k], -psi_fc * 1e6, -psi_wp * 1e6) for k in range(n)]
    mu = water_viscosity(T0)
    perm = soil["SCNV"] / (1000.0 * 3600.0) * mu / (RHO * GRAVITY)       # SCNV [mm h-1] -> k [m2], as the column pairs

    # water table and lateral boundaries (EcoSIM external water table, Watsub XBoundaryFlowM)
    mode = int(np.asarray(g["IDTBLG"]).ravel()[0])
    wt = None
    if mode not in (0, 1):
        raise CaseError(f"{name}: IDTBLG = {mode}; the matched pair supports no water table (0) or a stationary one (1)")
    if mode == 1:
        if float(np.asarray(g["DTBLGG"]).ravel()[0]) != 0.0:
            raise CaseError(f"{name}: DTBLGG must be 0 (a horizontal water table)")
        depth0 = float(np.asarray(g["DTBLIG"]).ravel()[0])
        z_wt = float(centers.min()) - depth0               # EcoSIM: depth DTBLIG below the lowest column surface
        wt = {"z": z_wt, "depth_at_lowest": depth0}
    sides = {}
    for side, key in (("east", "RCHGEUG"), ("west", "RCHGWUG"), ("north", "RCHGNUG"), ("south", "RCHGSUG")):
        sides[side] = float(np.asarray(g[key]).ravel()[0])
    runoff = {side: float(np.asarray(g[key]).ravel()[0]) for side, key in
              (("east", "RCHQEG"), ("west", "RCHQWG"), ("north", "RCHQNG"), ("south", "RCHQSG"))}

    populations = populations or {}
    assumed_population = []
    for c in range(ncol):
        given = next((populations[k] for k in (name, codes[c], f"c{c + 1}") if k in populations), None)
        if given is not None:
            pop[c] = given

    return dict(
        test=test, nl=nl, groups=groups, files=files, ncol=ncol, dx=dx, width=width, x_nodes=x_nodes, z_nodes=z_nodes,
        centers=centers, slope_deg=slope_deg, aspect=aspect, n=n, dz=dz, bottoms=bottoms, depth=float(bottoms[-1]),
        soil=soil, phi=phi, theta_fc=theta_fc, theta_wp=theta_wp, vg=vg, perm=perm, psi_fc=psi_fc, psi_wp=psi_wp,
        codes=codes, rows=rows, lai=lai, sai=sai, htop=htop, pop=pop, heights_table=[float(heights_table[r - 1]) for r in rows],
        years=years, ndays=ndays, latitude=latitude, noon=noon, wind_height=wind_height, forcing=forcing,
        T0=T0, bottom_T=bottom_T, atca=atca, wt=wt, sides=sides, runoff=runoff, unit_order=[int(u) for u in order],
        particle_density=pd, assumed_population=assumed_population, ats_engine=files["pft"])


def _pft_names(pft_file):
    d = netCDF4.Dataset(pft_file)
    d.set_auto_mask(False)
    names = [b"".join(row).decode().strip("\x00 ") for row in d["pfts"][:]]
    d.close()
    return names


def _pft_heights(pft_file):
    d = netCDF4.Dataset(pft_file)
    d.set_auto_mask(False)
    h = np.asarray(d["CANHT"][:], float)
    d.close()
    return h


# ---------------------------------------------------------------------------
# ATS side: mesh and forcing
# ---------------------------------------------------------------------------
def write_mesh(plan, path, ats_src):
    meshing_ats = meshing_ats_module(ats_src)
    m2 = meshing_ats.Mesh2D.from_Transect(plan["x_nodes"], plan["z_nodes"], plan["width"])
    n = plan["n"]
    m3 = meshing_ats.Mesh3D.extruded_Mesh2D(m2, ["constant"] * n, [float(v) for v in plan["dz"]], [1] * n,
                                            [SURFACE_LAYER_ID + k for k in range(n)])
    if os.path.exists(path):
        os.remove(path)
    m3.write_exodus(str(path))


def write_forcing(plan, path):
    """ATS hourly forcing from the climate file, with the daily LAI and SAI of every column"""
    f = plan["forcing"]
    hours = len(f["TMPH"])
    ndays = plan["ndays"]
    with h5py.File(path, "w") as h:
        h["time [s]"] = 3600.0 * np.arange(hours)
        h["air temperature [K]"] = f["TMPH"] + 273.15
        h["vapor pressure air [Pa]"] = f["DWPTH"] * 1000.0
        h["wind speed [m s^-1]"] = f["WINDH"]
        h["incoming shortwave radiation [W m^-2]"] = f["SRADH"]
        h["precipitation rain [m s^-1]"] = f["RAINH"] / 1000.0 / 3600.0         # mm h-1; EcoSIM partitions rain and snow
        h["precipitation snow [m SWE s^-1]"] = np.zeros(hours)
        for key, val in (("latitude [deg]", plan["latitude"]), ("solar noon [h]", plan["noon"]),
                         ("start year", float(plan["years"][0])), ("start day of year [0-364]", 0.0),
                         ("start hour [h]", 0.0)):
            h[key] = np.array([val])
        nyears = len(plan["years"])
        for c in range(plan["ncol"]):
            for name, monthly in (("LAI", plan["lai"][c]), ("SAI", plan["sai"][c])):
                daily = daily_phenology(monthly, 365)                           # a climatology: every year the same
                h[f"{name} c{c + 1} [m2 m^-2]"] = np.tile(np.repeat(daily, 24), nyears)
        h.attrs["calendar"] = "365_day"
        h.attrs["time convention"] = ("time [s] = 0 at 00:00 of the start date; values are hourly means stamped at the "
                                      "start of the hour (the layout of EcoSIM's hourly climate file); generated by "
                                      "tools/ats_pairs.py pair from the climate file of the namelist")
        h.attrs["years"] = np.array(plan["years"])


# ---------------------------------------------------------------------------
# ATS input
# ---------------------------------------------------------------------------
def _fmt(v):
    return repr(float(v))


def _el(xml):
    return ET.fromstring(xml)


def constant_function(value):
    return (f'<ParameterList name="function-constant" type="ParameterList">'
            f'<Parameter name="value" type="double" value="{_fmt(value)}"/></ParameterList>')


def tabular_function(file, y_header):
    return (f'<ParameterList name="function-tabular" type="ParameterList">'
            f'<Parameter name="file" type="string" value="{file}"/>'
            f'<Parameter name="x header" type="string" value="time [s]"/>'
            f'<Parameter name="y header" type="string" value="{y_header}"/></ParameterList>')


def region_function(name, region, inner, component="cell"):
    return (f'<ParameterList name="{name}" type="ParameterList"><Parameter name="region" type="string" value="{region}"/>'
            f'<Parameter name="component" type="string" value="{component}"/>'
            f'<ParameterList name="function" type="ParameterList">{inner}</ParameterList></ParameterList>')


def evaluator(name, kind, regions, constant_in_time=False, extra=""):
    """an independent-variable evaluator; regions is [(sublist name, region, inner function xml)]"""
    ct = '<Parameter name="constant in time" type="bool" value="true"/>' if constant_in_time else ""
    body = "".join(region_function(n, r, inner) for n, r, inner in regions)
    return _el(f'<ParameterList name="{name}" type="ParameterList"><Parameter name="evaluator type" type="string" '
               f'value="{kind}"/>{ct}<ParameterList name="function" type="ParameterList">{body}</ParameterList>{extra}'
               f'</ParameterList>')


def replace_child(parent, new):
    for c in list(parent):
        if c.tag == "ParameterList" and c.get("name") == new.get("name"):
            parent.remove(c)
    parent.append(new)


def set_param(node, name, value, type_=None):
    try:
        p = param(node, name)
    except KeyError:
        p = ET.SubElement(node, "Parameter", name=name, type=type_ or "double")
    if type_:
        p.set("type", type_)
    p.set("value", value if isinstance(value, str) else (_fmt(value) if isinstance(value, float) else str(value)))
    return p


def box_region(name, low, high):
    return _el(f'<ParameterList name="{name}" type="ParameterList"><ParameterList name="region: box" type="ParameterList">'
               f'<Parameter name="low coordinate" type="Array(double)" value="{{{", ".join(_fmt(v) for v in low)}}}"/>'
               f'<Parameter name="high coordinate" type="Array(double)" value="{{{", ".join(_fmt(v) for v in high)}}}"/>'
               f'</ParameterList></ParameterList>')


def labeled_region(name, label, entity, mesh_file):
    return _el(f'<ParameterList name="{name}" type="ParameterList"><ParameterList name="region: labeled set" type="ParameterList">'
               f'<Parameter name="label" type="string" value="{label}"/>'
               f'<Parameter name="file" type="string" value="{mesh_file}"/>'
               f'<Parameter name="format" type="string" value="Exodus II"/>'
               f'<Parameter name="entity" type="string" value="{entity}"/></ParameterList></ParameterList>')


def boundary_faces(plan):
    """box regions of the faces that exchange water with the external water table: {name: (low, high)}"""
    out = {}
    if plan["wt"] is None:
        return out
    z_wt, depth, W = plan["wt"]["z"], plan["depth"], plan["width"]
    x, zn = plan["x_nodes"], plan["z_nodes"]
    ncol = plan["ncol"]
    # a column exchanges water only where its soil is below the water table
    wet = [c for c in range(ncol) if min(zn[c], zn[c + 1]) - depth < z_wt]
    eps = 0.01
    for side, c in (("east", ncol - 1), ("west", 0)):
        if plan["sides"][side] > 0 and c in wet:
            xe = x[-1] if side == "east" else x[0]
            out[f"wt_{side}"] = ([xe - eps, -1.0, min(zn) - depth - 1.0], [xe + eps, W + 1.0, max(zn) + 1.0])
    for side, y in (("south", W), ("north", 0.0)):
        if plan["sides"][side] > 0 and wet:
            for c in wet:
                out[f"wt_{side}_c{c + 1}"] = ([x[c] + eps, y - eps, min(zn[c], zn[c + 1]) - depth - 1.0],
                                              [x[c + 1] - eps, y + eps, max(zn[c], zn[c + 1]) + 1.0])
    return out


def build_xml(plan, pair, mesh, forcing_h5, template_text, pft_file):
    """the ATS-EcoSIM input of the hillslope (text)"""
    ats_run = pair / "ats"
    root = parse_xml(template_text).getroot()
    n, ncol = plan["n"], plan["ncol"]
    W = plan["width"]
    x, zn = plan["x_nodes"], plan["z_nodes"]
    rel_mesh = os.path.relpath(mesh, ats_run)
    rel_forcing = os.path.relpath(forcing_h5, ats_run)

    # ---- mesh
    dom = plist(root, "mesh", "domain")
    for c in list(dom):
        if c.get("name") in ("mesh type", "generate mesh parameters"):
            dom.remove(c)
    mt = ET.Element("Parameter", name="mesh type", type="string", value="read mesh file")
    rm = ET.Element("ParameterList", name="read mesh file parameters", type="ParameterList")
    ET.SubElement(rm, "Parameter", name="file", type="string", value=rel_mesh)
    ET.SubElement(rm, "Parameter", name="format", type="string", value="Exodus II")
    dom.insert(0, rm)
    dom.insert(0, mt)

    # ---- regions
    regions = plist(root, "regions")
    for c in list(regions):
        regions.remove(c)
    for node in (_el('<ParameterList name="subsurface domain" type="ParameterList"><ParameterList name="region: all" '
                     'type="ParameterList"/></ParameterList>'),
                 _el('<ParameterList name="surface domain" type="ParameterList"><ParameterList name="region: all" '
                     'type="ParameterList"/></ParameterList>'),
                 labeled_region("surface", 2, "Face", rel_mesh), labeled_region("bottom face", 1, "Face", rel_mesh)):
        regions.append(node)
    for k in range(n):
        regions.append(labeled_region(f"layer_{k + 1}", SURFACE_LAYER_ID + k, "Cell", rel_mesh))
    eps = 0.01
    for c in range(ncol):
        regions.append(box_region(f"column_{c + 1}", [x[c] + eps, -1.0], [x[c + 1] - eps, W + 1.0]))
    regions.append(box_region("surface_east", [x[-1] - eps, -1.0], [x[-1] + eps, W + 1.0]))
    for name, (low, high) in boundary_faces(plan).items():
        regions.append(box_region(name, low, high))

    # ---- the EcoSIM PK
    pks = plist(root, "PKs")
    eco = plist(pks, "EcoSIM for ATS")
    set_param(eco, "EcoSIM precipitation", "true", "bool")
    set_param(eco, "prescribe phenology", "true", "bool")
    set_param(eco, "number of pfts", "1", "int")
    set_param(eco, "engine input file", os.path.relpath(pft_file, ats_run), "string")
    set_param(eco, "wind speed reference height [m]", plan["wind_height"], "double")
    for sub in eco:
        if sub.tag == "ParameterList" and sub.get("name") in ("starting year", "starting day of year [0-364]",
                                                               "starting hour [h]", "latitude [degrees]", "solar noon [h]"):
            param(sub, "file").set("value", rel_forcing)
    ev = plist(root, "state", "evaluators")

    # ---- soil per layer
    def per_layer(values):
        return [(f"layer_{k + 1}", f"layer_{k + 1}", constant_function(values[k])) for k in range(n)]
    replace_child(ev, evaluator("base_porosity", "independent variable", per_layer(plan["phi"]), True))
    replace_child(ev, evaluator("permeability", "independent variable tensor", per_layer(plan["perm"]), True,
                                '<Parameter name="tensor type" type="string" value="scalar"/>'))
    replace_child(ev, evaluator("density_rock", "independent variable",
                                per_layer([1000.0 * plan["particle_density"]] * n), True))
    wrm = plist(root, "state", "model parameters", "WRM parameters")
    smoothing = param(plist(wrm, "mineral"), "smoothing interval width [saturation]").get("value")
    for c in list(wrm):
        wrm.remove(c)
    for k, (alpha, vn, sr) in enumerate(plan["vg"]):
        wrm.append(_el(
            f'<ParameterList name="layer_{k + 1}" type="ParameterList"><Parameter name="region" type="string" value="layer_{k + 1}"/>'
            f'<Parameter name="wrm type" type="string" value="van Genuchten"/>'
            f'<Parameter name="van Genuchten alpha [Pa^-1]" type="double" value="{_fmt(alpha)}"/>'
            f'<Parameter name="van Genuchten n [-]" type="double" value="{_fmt(vn)}"/>'
            f'<Parameter name="residual saturation [-]" type="double" value="{_fmt(sr)}"/>'
            f'<Parameter name="smoothing interval width [saturation]" type="double" value="{smoothing}"/></ParameterList>'))

    # ---- plants: vegetation type, LAI and SAI of each column
    def per_column(inner):
        return [(f"column_{c + 1}", f"column_{c + 1}", inner(c)) for c in range(ncol)]
    replace_child(ev, evaluator("surface-vegetation_type", "independent variable",
                                per_column(lambda c: constant_function(float(plan["rows"][c])))))
    replace_child(ev, evaluator("surface-LAI", "independent variable",
                                per_column(lambda c: tabular_function(rel_forcing, f"LAI c{c + 1} [m2 m^-2]"))))
    replace_child(ev, evaluator("surface-SAI", "independent variable",
                                per_column(lambda c: tabular_function(rel_forcing, f"SAI c{c + 1} [m2 m^-2]"))))

    # ---- forcing files
    for node in root.iter("Parameter"):
        if node.get("name") == "file" and node.get("value") and "daymet" in node.get("value"):
            node.set("value", rel_forcing)
    inc = plist(ev, "surface-incident_shortwave_radiation", "incident shortwave radiation parameters")
    param(inc, "latitude [degrees]").set("value", _fmt(plan["latitude"]))
    set_param(plist(ev, "snow-source_sink"), "wind speed reference height [m]", plan["wind_height"], "double")
    tot = plist(ev, "surface-precipitation_total")
    for c in list(tot):
        tot.remove(c)
    ET.SubElement(tot, "Parameter", name="evaluator type", type="string", value="additive evaluator")
    ET.SubElement(tot, "Parameter", name="dependencies", type="Array(string)",
                  value="{surface-precipitation_rain, surface-precipitation_snow}")
    # the manning coefficient of the template is 1.0 on the whole surface (as the pairs); nothing to change

    # ---- flow and energy: initial state and boundaries
    flow = plist(pks, "subsurface flow")
    ic = plist(flow, "initial conditions")
    for c in list(ic):
        ic.remove(c)
    ET.SubElement(ic, "Parameter", name="initialize faces from cells", type="bool", value="true")
    fn = ET.SubElement(ic, "ParameterList", name="function", type="ParameterList")
    for k in range(n):
        p_fc = P_ATM + plan["psi_fc"] * 1e6                 # pressure at field capacity: suction of PSIFC
        fn.append(_el(region_function(f"layer_{k + 1}", f"layer_{k + 1}", constant_function(p_fc))))
    bcs = plist(flow, "boundary conditions")
    for c in list(bcs):
        bcs.remove(c)
    wt = plan["wt"]
    faces = list(boundary_faces(plan))
    if wt and faces:
        bcs.append(_el(
            '<ParameterList name="pressure" type="ParameterList"><ParameterList name="water table" type="ParameterList">'
            f'<Parameter name="regions" type="Array(string)" value="{{{", ".join(faces)}}}"/>'
            '<ParameterList name="boundary pressure" type="ParameterList"><ParameterList name="function-linear" type="ParameterList">'
            f'<Parameter name="y0" type="double" value="{_fmt(P_ATM)}"/>'
            f'<Parameter name="x0" type="Array(double)" value="{{0.0, 0.0, {_fmt(wt["z"])}}}"/>'
            f'<Parameter name="gradient" type="Array(double)" value="{{0.0, 0.0, {_fmt(-RHO * GRAVITY)}}}"/>'
            '</ParameterList></ParameterList></ParameterList></ParameterList>'))
    sflow = plist(pks, "surface flow", "boundary conditions")
    for c in list(sflow):
        sflow.remove(c)
    if any(plan["runoff"][s] > 0 for s in ("east",)):
        sflow.append(_el('<ParameterList name="zero gradient" type="ParameterList"><ParameterList name="outlet" '
                         'type="ParameterList"><Parameter name="regions" type="Array(string)" value="{surface_east}"/>'
                         '</ParameterList></ParameterList>'))
    energy = plist(pks, "subsurface energy")
    param(plist(energy, "initial conditions"), "value").set("value", _fmt(plan["T0"]))
    bottom = plist(energy, "boundary conditions", "temperature", "bottom", "boundary temperature", "function-constant")
    param(bottom, "value").set("value", _fmt(plan["bottom_T"]))

    # ---- run length and output
    driver = plist(root, "cycle driver")
    param(driver, "end time").set("value", _fmt(float(plan["ndays"])))
    param(driver, "end time units").set("value", "d")
    for name in ("domain", "surface domain", "surface"):
        param(plist(root, "visualization", name), "times start period stop").set("value", "{0.0, 86400.0, -1.0}")

    # ---- observations of every column
    obs = plist(root, "observations")
    props = plist(obs, "surface properties")
    quantities = plist(props, "observed quantities")
    template = [c for c in quantities if c.tag == "ParameterList"]
    for c in template:
        quantities.remove(c)
    for col in range(ncol):
        for q in template:
            node = ET.fromstring(ET.tostring(q))
            node.set("name", f"{q.get('name')}_c{col + 1}")
            param(node, "region").set("value", f"column_{col + 1}")
            quantities.append(node)
    for c in list(obs):
        if c.get("name") == "subsurface properties":
            obs.remove(c)
    ET.indent(root, space="  ")
    header = (f"<!--\nATS-EcoSIM input of the {ncol}-column landscape {plan['test'].name}, generated by tools/ats_pairs.py pair\n"
              f"from {plan['test'].source.name}; do not edit.\n{n} soil layers over {plan['depth']:g} m, EcoSIM partitions "
              f"precipitation,\n{plan['ndays']} days from 1 January ({plan['years'][0]} in the forcing file).\n-->\n\n")
    return header + ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------------------
# standalone side
# ---------------------------------------------------------------------------
def write_grid(plan, path, cfg):
    """the grid file of the namelist with the changes that make the standalone run match the coupled one"""
    src, dst = copy_structure(plan["files"]["grid"], path)
    for name, v in src.variables.items():
        dst[name][...] = v[...]
    src.close()
    nl = dst.dimensions["nlevs"].size
    nunits = dst.dimensions["ntopou"].size

    def layers(value):
        a = np.zeros((nunits, nl))
        a[:, :plan["n"]] = value
        return a
    dst["ATCAG"][...] = plan["T0"] - 273.15                   # EcoSIM's deep source temperature is ATCAG + 0.28 K
    dst["DPTHSX"][...] = 0.0                                  # no initial snow
    dst["ALBS"][...] = cfg["soil_albedo"]
    dst["THI"][...] = layers(-1.0)                            # no initial ice
    for key in ("CORGC", "CORGR", "CORGN", "CORGP", "RSCfL", "RSNfL", "RSPfL", "RSCwL", "RSNwL", "RSPwL", "RSCmL",
                "RSNmL", "RSPmL"):
        dst[key][...] = layers(0.0)                           # mineral soil without organic matter, as the coupled path
    for key in ("RSCf", "RSNf", "RSPf", "RSCw", "RSNw", "RSPw", "RSCm", "RSNm", "RSPm"):
        dst[key][...] = 0.0                                   # no surface litter
    dst.history = ("Generated by tools/ats_pairs.py pair from the grid file of the namelist: start temperature, "
                   "no snow, no ice, no organic matter or litter, soil albedo " + f"{cfg['soil_albedo']:g}")
    dst.close()


def write_phenology(plan, path):
    """the phenology file with the canopy height of the PFT file, which is what the coupled path uses"""
    src, dst = copy_structure(plan["files"]["phenology"], path)
    for name, v in src.variables.items():
        dst[name][...] = v[...]
    src.close()
    sp = netCDF4.Dataset(plan["files"]["phenology"])
    nh1 = np.asarray(sp["NH1"][...]).astype(int)
    sp.close()
    for u, col in enumerate(nh1):
        dst["HTOP"][0, u, 0, :] = plan["heights_table"][col - 1]
    dst.close()


def write_standalone_namelist(plan, pair, cfg):
    text = plan["test"].source.read_text()
    run = pair / "standalone"
    rel = lambda p: os.path.relpath(p, run)
    for path in (plan["files"]["pft"], plan["files"]["ghg"], plan["files"]["climate"]):
        if len(rel(path)) > 128:
            raise CaseError(f"the path {rel(path)!r} is longer than 128 characters; EcoSIM truncates file names")
    years = plan["years"]
    fields = list(cfg["history_fields"]) + [f for f in ("ECO_LAI_col", "ECO_SAI_col") if f not in cfg["history_fields"]]
    ndays = plan["ndays"]
    eco = {
        "case_name": plan["test"].name,
        "pft_file_in": rel(plan["files"]["pft"]),
        "grid_file_in": "../grid.nc",
        "sp_phenol_in": "../sp_phenol.nc",
        "clm_hour_file_in": rel(plan["files"]["climate"]),
        "atm_ghg_in": rel(plan["files"]["ghg"]),
        "start_date": f"{years[0]}0101000000",
        "forc_periods": [years[0], years[-1], 1],
        "hist_fincl1": fields,
        "hist_mfilt": ndays * 24,
        "hist_nhtfrq": 1,
    }
    text = nlmod.rewrite(text, eco, "ecosim")
    text = nlmod.rewrite(text, {"stop_n": ndays, "stop_option": "ndays", "rest_opt": "never"}, "ecosim_time")
    (pair / "standalone.namelist").write_text(text)


# ---------------------------------------------------------------------------
# one pair
# ---------------------------------------------------------------------------
def write_pair(case, plan, cfg, ecosim, ats_src):
    test = plan["test"]
    pair = case.pair_dir(test.name)
    (pair / "ats").mkdir(parents=True, exist_ok=True)
    (pair / "standalone").mkdir(parents=True, exist_ok=True)
    mesh = pair / "hillslope.exo"
    write_mesh(plan, mesh, ats_src)
    forcing = pair / "forcing.h5"
    write_forcing(plan, forcing)
    write_grid(plan, pair / "grid.nc", cfg)
    write_phenology(plan, pair / "sp_phenol.nc")
    write_standalone_namelist(plan, pair, cfg)
    template = TEMPLATE.read_text()
    (pair / "ats_match.xml").write_text(build_xml(plan, pair, mesh, forcing, template, plan["ats_engine"]))
    meta = dict(
        name=test.name, kind="hillslope", columns=plan["ncol"], bare=False, group="landscape",
        pft=plan["codes"], row=plan["rows"], lai=float(np.mean(plan["lai"])), sai=float(np.mean(plan["sai"])),
        height=plan["heights_table"], population=[float(v) for v in plan["pop"]], forcing="forcing.h5",
        ndays=plan["ndays"], years=plan["years"], start_temperature=plan["T0"], bottom_temperature=plan["bottom_T"],
        layers=plan["n"], depth=plan["depth"], dz=[float(v) for v in plan["dz"]],
        dx=[float(v) for v in plan["dx"]], width=plan["width"], x_nodes=[float(v) for v in plan["x_nodes"]],
        z_nodes=[float(v) for v in plan["z_nodes"]], slope_deg=[float(v) for v in plan["slope_deg"]],
        cell_order="layer-major: ATS cell = layer * columns + column (the element blocks of the mesh are the layers)",
        porosity=[float(v) for v in plan["phi"]], water_table_z=plan["wt"]["z"] if plan["wt"] else None,
        vg=[dict(alpha=float(a), n=float(n_), sr=float(sr)) for a, n_, sr in plan["vg"]],
    )
    (pair / "pair.json").write_text(json.dumps(meta, indent=1) + "\n")


def describe(plan):
    """one-line summary of a plan, for the `pair` listing"""
    codes = sorted(set(plan["codes"]))
    return (f"{plan['ncol']} columns of {plan['dx'][0]:g} m ({', '.join(codes)}), slopes "
            f"{', '.join(f'{s:g}' for s in plan['slope_deg'])} deg, {plan['n']} layers over {plan['depth']:g} m; "
            f"{plan['ndays']} d, start {plan['T0']:g} K, bottom {plan['bottom_T']:g} K")


def write_readme(path, plans, cfg):
    first = plans[0]
    wt = first["wt"]
    rows = "\n".join(
        f"| {c + 1} | {first['codes'][c]} | {first['rows'][c]} | {first['dx'][c]:g} | {first['slope_deg'][c]:g} | "
        f"{first['z_nodes'][c]:.2f} → {first['z_nodes'][c + 1]:.2f} | {first['pop'][c]:g} | "
        f"{first['heights_table'][c]:g} (file {np.mean(first['htop'][c]):g}) |" for c in range(first["ncol"]))
    layers = "\n".join(
        f"| {k + 1} | {first['dz'][k]:.3f} | {first['phi'][k]:.4f} | {first['theta_fc'][k]:.4f} | {first['theta_wp'][k]:.4f} | "
        f"{first['vg'][k][0]:.3e} | {first['vg'][k][1]:.3f} | {first['vg'][k][2]:.3f} | {first['perm'][k]:.3e} |"
        for k in range(first["n"]))
    text = f"""# Matched landscapes: ATS-EcoSIM and standalone EcoSIM (namelist tests)

Generated by `tools/ats_pairs.py pair`; do not edit by hand. Each pair is in `pairs/<test>/`.

## Running (per test `<name>`)
    ats --xml_file=../ats_match.xml                  # in pairs/<name>/ats/
    ecosim.f90.x ../standalone.namelist              # in pairs/<name>/standalone/

## Columns of {first['test'].name}
| column | PFT | PFT row | width [m] | slope [deg] | surface z [m] (west → east edge) | population [plants m-2] | height [m] |
|---|---|---|---|---|---|---|---|
{rows}

The surface elevation is integrated from the slopes as EcoSIM does (`StartsMod.F90`); the lowest node is z = 0.
The height is that of the PFT parameter file, which the coupled path uses; the phenology file's own height
is replaced in the standalone copy. The population of the ATS PK is not an input.

## Soil layers (the same in every column)
| layer | dz [m] | porosity | field capacity | wilting point | alpha [Pa-1] | n | residual S | k [m2] |
|---|---|---|---|---|---|---|---|---|
{layers}

Porosity is 1 - bulk density / {first['particle_density']:g} (the EcoSIM particle density), the field capacity and
wilting point are those of the grid file (potentials {first['psi_fc']:g} and {first['psi_wp']:g} MPa), and the van Genuchten
curve of each layer is the one through those two points with a residual saturation of half the saturation at the
wilting point. k is the grid file's SCNV converted as the column pairs do (k = Ksat mu / (rho g)).

## Matched
| item | value |
|---|---|
| geometry | {first['ncol']} columns side by side, width {first['width']:g} m across; ATS mesh `hillslope.exo` |
| forcing | the climate file of the namelist, years {first['years'][0]}-{first['years'][-1]} (365-day years), hourly; rain and snow partitioned by EcoSIM in both runs |
| start | {first['T0']:g} K in both models (the grid file's mean annual temperature is replaced; the standalone has no initial ice), bottom temperature {first['bottom_T']:g} K in ATS = EcoSIM's deep source temperature |
| initial water | field capacity in every layer (grid THW = 1); ATS: the pressure of the field-capacity potential |
| water table | {'a horizontal stationary water table ' + format(wt['depth_at_lowest'], 'g') + ' m below the lowest column surface (z = ' + format(wt['z'], '.3f') + ' m); ATS: hydrostatic pressure on the east and south faces of the columns whose soil reaches it' if wt else 'none'} |
| surface runoff | out of the east end (the grid's RCHQEG = {first['runoff']['east']:g}); ATS: zero-gradient outlet at the east end |
| soil | mineral, no organic matter or litter; soil albedo {cfg['soil_albedo']:g} |
| slope and aspect | EcoSIM-in-ATS takes them from the ATS surface (`SetGroundSlopeAspect`), so the solar geometry of every column is the standalone's |

## Not matched
- **Lateral flow:** EcoSIM's own cell-to-cell surface and subsurface flow and its water-table exchange (the grid's RCHG* factors and
  distance) against ATS 3D Richards and overland flow with a pressure boundary. The standalone's runoff boundary factor `RCHQEG`
  scales its outflow; the ATS outlet does not.
- **Soil water physics and thermal properties:** as for the column pairs (retention curve, conductivity, ATS's own thermal
  conductivity model).
- **Calendar:** the climate file's leap days are not used (365-day years); the standalone years are labelled {first['years'][0]}-{first['years'][-1]}.
- **Longwave:** ATS computes the sky longwave from air temperature and vapor pressure; the standalone has its own estimate.
"""
    Path(path).write_text(text)
