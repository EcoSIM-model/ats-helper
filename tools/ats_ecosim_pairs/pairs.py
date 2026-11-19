"""Matched standalone EcoSIM cases (and matched ATS variants) for the tests of a suite.

Built on make_standalone_match.py of ats-regression-tests (13_ecosim), which matches one ATS-EcoSIM column
to one standalone EcoSIM column. For every test of the manifest this writes, under <suite>/pairs/:

  common/column_graded<N>.exo       column mesh for the ATS variants (N graded layers)
  common/grid_match.nc              standalone grid, site and soil (EcoSIM grid file)
  common/clim_match.nc              standalone hourly climate, from the suite's forcing file
  common/<ghg file>                 atmospheric composition (copy from EcoSIM input_data)
  <test>/ats_match.xml              the test's ATS input on the graded column, EcoSIM partitions rain/snow
  <test>/sp_phenol.nc               standalone prescribed phenology: PFT, constant LAI, SAI, height, population
  <test>/standalone.namelist        standalone EcoSIM namelist
  README.md                         what is and is not matched

Run directories (all paths in the files are relative to them):
  pairs/<test>/ats/                 ats --xml_file=../ats_match.xml
  pairs/<test>/standalone/          ecosim.f90.x ../standalone.namelist

Values come from the rendered ATS input (soil, column, run length, canopy, EcoSIM PK settings), from the
forcing file (site, calendar) and from the manifest's "standalone" section (layers, years, constants).
Anything fixed here is listed in the README it writes. Needs numpy, h5py and netCDF4.
"""

import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import h5py
import netCDF4
import numpy as np

from . import render, xmledit
from .xmledit import array_values, constant_value, param, parse_xml, plist  # noqa: F401


# ---------------------------------------------------------------------------
# soil and column
# ---------------------------------------------------------------------------
def layer_thicknesses(depth, nlayers, dz_top):
    lo, hi = 1.0, 2.0
    for _ in range(200):
        r = 0.5 * (lo + hi)
        total = dz_top * (r**nlayers - 1.0) / (r - 1.0)
        lo, hi = (r, hi) if total < depth else (lo, r)
    dz = dz_top * r**np.arange(nlayers)
    return dz * depth / dz.sum()


def vg_saturation(pc, p):
    """ATS van Genuchten (Mualem m = 1 - 1/n), pc capillary pressure [Pa]"""
    m = 1.0 - 1.0 / p["vg_n"]
    se = (1.0 + (p["vg_alpha"] * np.maximum(pc, 0.0))**p["vg_n"])**(-m)
    return p["sr"] + (1.0 - p["sr"]) * se


def water_viscosity(T):
    """ATS viscosity_water.cc"""
    kav1, kbv1, kcv1, kbv2, kcv2, kT1 = 998.333, -8.1855, 0.00585, 1.3272, -0.001053, 293.15
    dT = kT1 - T
    if T < kT1:
        A = kav1 + (kbv1 + kcv1 * dT) * dT
        xi = 1301.0 * (1.0 / A - 1.0 / kav1)
    else:
        xi = (kbv2 + kcv2 * dT) * dT / (T - 168.15)
    return 0.001 * 10.0**xi


def soil(p, dz, cfg):
    depth = p["depth"]
    depth_bottom = np.cumsum(dz)
    z_center = depth - (depth_bottom - 0.5 * dz)          # ATS mesh: bottom z=0, top z=depth
    s = dict(dz=dz, depth_bottom=depth_bottom, z_center=z_center)
    # EcoSIM derives porosity from bulk density with its own particle density, so
    # match porosity rather than the ATS rock density
    s["bulk_density"] = cfg["particle_density"] * (1.0 - p["porosity"])                # Mg m-3
    s["fc"] = p["porosity"] * vg_saturation(-p["psi_fc"] * 1e6, p)
    s["wp"] = p["porosity"] * vg_saturation(-p["psi_wp"] * 1e6, p)
    mu = water_viscosity(p["T0"])
    s["ksat_mm_h"] = p["permeability"] * p["rho"] * p["g"] / mu * 1000.0 * 3600.0
    # ATS "hydrostatic head" is relative to the surface: water table at depth + head
    pc = p["rho"] * p["g"] * (z_center - (depth + p["head"]))  # p = p_atm + rho g (z_wt - z)
    s["theta0"] = p["porosity"] * vg_saturation(pc, p)
    s["theta0_source"] = "analytic ATS hydrostatic state (head %g m)" % p["head"]
    return s


def pft_table(pft_file):
    """PFT codes (1-based rows) and canopy heights [m] of the EcoSIM PFT parameter file"""
    d = netCDF4.Dataset(pft_file)
    names = [b"".join(row).decode().strip("\x00 ") for row in d["pfts"][:]]
    heights = np.asarray(d["CANHT"][:], dtype=float)
    d.close()
    return names, heights


# ---------------------------------------------------------------------------
# outputs: ATS side
# ---------------------------------------------------------------------------
def write_mesh(dz, depth, nlayers, path, ats_src):
    sys.path.append(str(Path(ats_src) / "tools" / "meshing" / "meshing_ats"))
    try:
        import meshing_ats
    except ImportError as err:
        raise SystemExit(f"Cannot import the ATS meshing tools from {ats_src}/tools/meshing/meshing_ats "
                         f"({err}). Use --ats-src, or run inside the ATS container.")
    m2 = meshing_ats.Mesh2D.from_Transect(np.array([0.0, 1.0]), np.array([depth, depth]))
    m3 = meshing_ats.Mesh3D.extruded_Mesh2D(m2, ["constant"] * nlayers, list(dz),
                                            [1] * nlayers, [1001] * nlayers)
    if os.path.exists(path):
        os.remove(path)
    m3.write_exodus(path)


def write_ats_variant(test, ats_text, dz, depth, pair_dir, mesh_path, ndays, years, nlayers):
    """The test's ATS input on the graded column, with EcoSIM partitioning rain and snow."""
    ats_run = pair_dir / "ats"
    tree = parse_xml(ats_text)
    root = tree.getroot()
    # mesh: the graded exodus column (relative to the run directory)
    dom = plist(root, "mesh", "domain")
    for c in list(dom):
        if c.get("name") in ("mesh type", "generate mesh parameters"):
            dom.remove(c)
    mt = ET.Element("Parameter", name="mesh type", type="string", value="read mesh file")
    rm = ET.Element("ParameterList", name="read mesh file parameters", type="ParameterList")
    ET.SubElement(rm, "Parameter", name="file", type="string",
                  value=os.path.relpath(mesh_path, ats_run))
    ET.SubElement(rm, "Parameter", name="format", type="string", value="Exodus II")
    dom.insert(0, rm)
    dom.insert(0, mt)
    # regions follow the mesh
    regions = plist(root, "regions")
    sub = plist(regions, "subsurface domain")
    for c in list(sub):
        sub.remove(c)
    ET.SubElement(sub, "ParameterList", name="region: all", type="ParameterList")
    top = plist(regions, "top_cell_obs", "region: box")
    param(top, "low coordinate").set("value", "{-10.0, -10.0, %.6f}" % (depth - dz[0]))
    param(top, "high coordinate").set("value", "{10.0, 10.0, %.6f}" % depth)
    # EcoSIM gets total precipitation and partitions it, as the standalone does
    eco = plist(root, "PKs", "EcoSIM for ATS")
    param(eco, "EcoSIM precipitation").set("value", "true")
    ev = plist(root, "state", "evaluators")
    tot = plist(ev, "surface-precipitation_total")
    for c in list(tot):
        tot.remove(c)
    ET.SubElement(tot, "Parameter", name="evaluator type", type="string", value="additive evaluator")
    ET.SubElement(tot, "Parameter", name="dependencies", type="Array(string)",
                  value="{surface-precipitation_rain, surface-precipitation_snow}")
    # daily visualization, as the comparison reads
    for name in ("domain", "surface domain", "surface"):
        param(plist(root, "visualization", name), "times start period stop").set(
            "value", "{0.0, 86400.0, -1.0}")
    header = (f"<!--\nATS-EcoSIM input of {test} on the graded standalone-match column. Generated by\n"
              f"make_pft_standalone_pairs.py from the suite input; do not edit.\n"
              f"{nlayers} graded layers over {depth:g} m, EcoSIM partitions precipitation,\n"
              f"{ndays:g} days from 1 January. The standalone pair labels its years {years[0]}-{years[-1]}.\n"
              f"-->\n\n")
    ET.indent(root, space="  ")
    return header + ET.tostring(root, encoding="unicode") + "\n"


# ---------------------------------------------------------------------------
# outputs: standalone side
# ---------------------------------------------------------------------------
def copy_structure(src, dst):
    """new NETCDF3_CLASSIC file with the dimensions, variables and attributes of src"""
    s = netCDF4.Dataset(src)
    d = netCDF4.Dataset(dst, "w", format=s.file_format)
    d.setncatts({k: s.getncattr(k) for k in s.ncattrs()})
    for name, dim in s.dimensions.items():
        d.createDimension(name, None if dim.isunlimited() else len(dim))
    for name, v in s.variables.items():
        fill = v.getncattr("_FillValue") if "_FillValue" in v.ncattrs() else None
        nv = d.createVariable(name, v.dtype, v.dimensions, fill_value=fill)
        nv.setncatts({k: v.getncattr(k) for k in v.ncattrs() if k != "_FillValue"})
    return s, d


def write_grid(path, s, p, site, template, cfg):
    t, d = copy_structure(template, path)
    for name, v in t.variables.items():                       # start from the template
        d[name][...] = v[...]
    t.close()
    n = cfg["layers"]

    def layers(values):
        a = np.zeros(d.dimensions["nlevs"].size)
        a[:n] = values
        return a[None, :]
    set_ = {
        # IETYPG = 0 keeps the PFT code's own climate suffix (as the ERT prescribed-phenology grid)
        "ALATG": site["latitude [deg]"], "ATCAG": p["T0"] - 273.15, "IETYPG": 0, "IDTBLG": 0,
        "RCHQNG": 0, "RCHQEG": 0, "RCHQSG": 0, "RCHQWG": 0,
        "RCHGNUG": 0, "RCHGEUG": 0, "RCHGSUG": 0, "RCHGWUG": 0, "RCHGDG": 0,
        "ASPX": 0, "SL0": 0, "DPTHSX": 0, "ALBS": cfg["soil_albedo"],
        "PSIFC": p["psi_fc"], "PSIWP": p["psi_wp"],
        "NUI": 1, "NJ": n, "NL1": 0, "NL2": 0,
    }
    for k, v in set_.items():
        d[k][...] = v
    d["DHI"][...] = 1.0
    d["DVI"][...] = 1.0
    d["CDPTH"][...] = layers(s["depth_bottom"])
    d["BKDSI"][...] = layers(s["bulk_density"])
    d["FC"][...] = layers(s["fc"])
    d["WP"][...] = layers(s["wp"])
    d["SCNV"][...] = layers(s["ksat_mm_h"])
    d["SCNH"][...] = layers(s["ksat_mm_h"])
    d["FHOL"][...] = layers(0.0)
    d["ROCK"][...] = layers(0.0)
    d["THW"][...] = layers(s["theta0"])
    d["THI"][...] = layers(-1.0)     # no ice; THI = 0 means ice at WP (StartsMod.F90:489)
    # texture: a fixed loam (not matched: ATS thermal properties are separate)
    d["CSAND"][...] = layers(400.0)
    d["CSILT"][...] = layers(400.0)
    # mineral soil: no organic matter or litter (as the coupled path, CSoilOrgM = 0)
    for k in ["CORGC", "CORGR", "CORGN", "CORGP", "RSCfL", "RSNfL", "RSPfL", "RSCwL",
              "RSNwL", "RSPwL", "RSCmL", "RSNmL", "RSPmL"]:
        d[k][...] = layers(0.0)
    for k in ["RSCf", "RSNf", "RSPf", "RSCw", "RSNw", "RSPw", "RSCm", "RSNm", "RSPm"]:
        d[k][...] = 0.0
    d.close()


def ats_sky_longwave(temp_k, vapor_pressure_pa):
    """incoming longwave [W m-2] of the ATS evaluator (seb_physics_funcs.cc, IncomingLongwaveRadiation)"""
    e_air = (vapor_pressure_pa / 100.0) ** (temp_k / 2016.0)
    return 1.08 * (1.0 - np.exp(-e_air)) * 5.670374419e-8 * temp_k ** 4


def forcing_years(forcing, nyears, longwave=None):
    """Hourly forcing as EcoSIM's (year, day, hour, grid) arrays.

    The coupled EcoSIM call at the start of an hour gets the ATS forcing there (the value
    stamped at the start of the hour, the mean of that hour) and computes that hour.
    Standalone hour J of day d covers the same hour, so it gets record 24 (d - 1) + J - 1.
    The 365-day years are stored in the 366-day arrays; day 366 repeats day 365 and is unused.
    """
    nh = 365 * 24
    with h5py.File(forcing, "r") as f:
        t = f["time [s]"][:]
        data = {k: f[k][:] for k in f if f[k].shape[0] > 1 and k != "time [s]"}
    if len(t) < nyears * nh or not np.allclose(t[:nyears * nh], 3600.0 * np.arange(nyears * nh)):
        raise ValueError(f"{Path(forcing).name}: need {nyears * nh} hourly records from time 0")

    def yearly(a):
        out = np.empty((nyears, 366, 24, 1))
        for y in range(nyears):
            days = a[y * nh:(y + 1) * nh].reshape(365, 24)
            out[y, :365, :, 0] = days
            out[y, 365, :, 0] = days[-1]
        return out
    out = {}
    if longwave == "ats":              # W m-2, like SRADH (ClimReadMod converts); XRADH replaces the sky-emissivity estimate
        out["XRADH"] = yearly(ats_sky_longwave(data["air temperature [K]"], data["vapor pressure air [Pa]"]))
    elif longwave:
        raise ValueError(f"standalone.longwave must be null or 'ats', not {longwave!r}")
    return {**out,
        "TMPH": yearly(data["air temperature [K]"] - 273.15),
        "WINDH": yearly(data["wind speed [m s^-1]"]),
        "RAINH": yearly((data["precipitation rain [m s^-1]"] + data["precipitation snow [m SWE s^-1]"])
                        * 1000.0 * 3600.0),
        "DWPTH": yearly(data["vapor pressure air [Pa]"] / 1000.0),
        "SRADH": yearly(data["incoming shortwave radiation [W m^-2]"]),
    }


def write_climate(path, site, years, template, forcing, wind_height, longwave=None):
    n = len(years)
    t, d = copy_structure(template, path)
    t.close()
    for k, v in forcing_years(forcing, n, longwave).items():
        if k not in d.variables:       # XRADH: hourly longwave, not in the template
            x = d.createVariable(k, "f8", ("year", "day", "hour", "ngrid"))
            x.long_name, x.units = "Incoming longwave radiation (hourly)", "W m-2"
        d[k][0:n, ...] = v
    d["year"][0:n] = list(years)
    d["Z0G"][0:n, :] = wind_height
    d["IFLGW"][0:n, :] = 0
    d["ZNOONG"][0:n, :] = site["solar noon [h]"]
    d["PHRG"][0:n, :] = 7.0
    for k in ["CN4RIG", "CNORIG", "CPORG", "CALRG", "CFERG", "CCARG", "CMGRG", "CNARG", "CKARG",
              "CSORG", "CCLRG"]:
        d[k][0:n, :] = 0.0
    d.close()


def write_sp_phenol(path, code, lai, sai, height, population, year):
    """Prescribed phenology for one column with one PFT and constant LAI and SAI.

    Same layout as examples/inputs/ERT/ERT6_sp_phenol_*.nc with one topographic unit.
    """
    d = netCDF4.Dataset(path, "w", format="NETCDF3_CLASSIC")
    d.title = f"Prescribed phenology of the PFT test {code}: constant LAI and SAI"
    d.source = "Generated by make_pft_standalone_pairs.py from the PFT test suite"
    d.createDimension("ntopou", 1)
    d.createDimension("maxpfts", 1)
    d.createDimension("nchar1", 10)
    d.createDimension("month", 12)
    d.createDimension("year", None)
    for name, long_name in (("NH1", "Starting column from the west"), ("NV1", "Starting row from the north"),
                            ("NH2", "Ending column at the east"), ("NV2", "Ending row at the south"),
                            ("NZ", "Number of prescribed PFT slots per topo unit")):
        v = d.createVariable(name, "i4", ("ntopou",))
        v.long_name, v.units = long_name, "1"
        v[:] = 1
    v = d.createVariable("year", "i4", ("year",))
    v.long_name, v.units = "Forcing-year record; one record repeats for all years", "year"
    v[0] = year
    v = d.createVariable("pft_type", "S1", ("year", "ntopou", "maxpfts", "nchar1"))
    v.long_name = "Prescribed PFT code; grid IETYPG=0 preserves the supplied climate suffix"
    chars = np.array(list(code.ljust(10)), "S1")
    v[0, 0, 0, :] = chars
    v = d.createVariable("PlantPopuLive_pft", "f8", ("year", "ntopou", "maxpfts"))
    v.long_name, v.units, v.valid_min = "Living population density", "plants m-2", 0.0
    v[0, 0, 0] = population
    for name, long_name, units, value in (("LAI", "Monthly living leaf area index", "m2 m-2", lai),
                                          ("SAI", "Monthly live stem surface area index", "m2 m-2", sai),
                                          ("HTOP", "Monthly canopy top height", "m", height)):
        v = d.createVariable(name, "f4", ("year", "ntopou", "maxpfts", "month"))
        v.long_name, v.units = long_name, units
        v[0, 0, 0, :] = value
    d.close()


NAMELIST = """&regression_test
 cells = 1
/

&ecosim
case_name          = '{name}'
do_regression_test = .false.
pft_file_in        = '{pft_file}'
grid_file_in       = '../../common/grid_match.nc'
pft_mgmt_in        = 'NO'
{phenology_file}
clm_hour_file_in   = '../../common/clim_match.nc'
clm_factor_in      = 'NO'
soil_mgmt_in       = 'NO'
atm_ghg_in         = '../../common/{ghg}'

lverbose        = .false.
ldo_sp_mode     = {plants}
disp_planttrait = .true.
plant_model     = {plants}
microbial_model = .false.
soichem_model   = .false.

start_date   = '{year0}0101000000'
grid_mode    = 3
continue_run = .false.
forc_periods = {year0},{year1},1

hist_fincl1 = {history}

NPXS=30,30,30
NPYS=10,10,10
NCYC_LITR=30
NCYC_SNOW=20
hist_mfilt  = {nhours}
hist_nhtfrq = 1
/

&bbgcforc
/

&Microbes
/

&ecosim_time
rest_opt    = 'never'
rest_frq    = 1
delta_time  = 3600.
stop_n      = {ndays}
stop_option = 'ndays'
diag_frq    = 1
diag_opt    = 'nsteps'
/
"""


# history fields that exist only with plants
PLANT_HISTORY = {"RootH2OUptake_vr"}


def write_namelist(path, name, pft_file_rel, ndays, years, ghg, history, plants=True):
    """standalone namelist; plants=False is the bare-ground case (no plant model, as the coupling sets it
    when prescribed phenology is off)"""
    assert len(pft_file_rel) <= 128, "EcoSIM truncates file names at 128 characters"
    if not plants:
        history = [f for f in history if f not in PLANT_HISTORY]
    Path(path).write_text(NAMELIST.format(
        name=name, pft_file=pft_file_rel, year0=years[0], year1=years[-1], ghg=ghg,
        history=",".join(f"'{f}'" for f in history), ndays=int(ndays), nhours=int(ndays) * 24,
        plants=".true." if plants else ".false.",
        phenology_file="sp_phenol_in       = '../sp_phenol.nc'      ! PFT, population and geometry" if plants
        else "! no sp_phenol_in: bare ground"))


STANDALONE_ROUGHNESS = 0.02         # m, SoilSurfRoughness_col of a mineral soil in the standalone (Hour1Mod.F90)


def write_readme(path, p, s, site, years, plan, cfg, wind_height, wind_source, bare=()):
    n = cfg["layers"]
    rows = "\n".join(f"| {i+1} | {s['dz'][i]:.4f} | {s['depth_bottom'][i]:.4f} | {s['theta0'][i]:.5f} |"
                     for i in range(n))
    tests = "\n".join(
        f"| {t['name']} | {t['code']} | {t['row']} | {t['lai']:g} | {t['sai']:g} | {t['height']:g} | "
        f"{t['population']:g}{' (assumed)' if t['assumed'] else ''} |" for t in plan)
    bare_text = ""
    if bare:
        bare_text = (f"- **Bare ground ({', '.join(bare)}):** the same column without plants. The standalone runs with "
                     "`plant_model = .false.` and no phenology file, which is what the coupling sets when prescribed "
                     "phenology is off (`ATSEcoSIMInitMod.F90`); `RootH2OUptake_vr` is left out of its history. "
                     f"The ATS variant uses a soil surface roughness of {STANDALONE_ROUGHNESS:g} m, which the standalone "
                     "hard-codes for a mineral soil; the original input has 0.01 m. The rest of the column, forcing "
                     "and EcoSIM settings are those of the other pairs.\n")
    bottom = (f"ATS fixes {p['bottom_T']:g} K at the bottom" if p["bottom_T"] is not None
              else "ATS has no fixed bottom temperature in this input")
    text = f"""# PFT test pairs: ATS-EcoSIM and standalone EcoSIM

Generated by `make_pft_standalone_pairs.py`; do not edit by hand. Nothing here has been run yet.

## Running (per test `<name>`)
    ats --xml_file=../ats_match.xml                  # in pairs/<name>/ats/
    ecosim.f90.x ../standalone.namelist              # in pairs/<name>/standalone/

The standalone namelist uses relative paths: EcoSIM truncates file names to 128 characters.

## Tests
Bare ground pairs: {', '.join(bare) or 'none'} (see Not matched).

| test | PFT | PFT row (= ATS vegetation type) | LAI | SAI | height [m] | population [plants m-2] |
|---|---|---|---|---|---|---|
{tests}

LAI and SAI are constant in time in both models (the standalone's monthly tables hold one value).
Height is the PFT file's canopy height (`CANHT`), which the coupled path uses. Population: trees
and grass use the ERT_SatePhenol values; the shrub value is an assumption, and what the coupled
path uses is not established.

## Matched
| item | value |
|---|---|
| plant code | prescribed phenology (`ldo_sp_mode`), same EcoSIM path as the coupling; PFT parameters from the same file |
| column | {n} graded layers over {p['depth']:g} m, 1 m2, top layer {s['dz'][0]:.3f} m (the ATS variants use the same layers) |
| porosity | {p['porosity']:g}: EcoSIM bulk density {s['bulk_density']:.4f} Mg m-3 with its particle density {cfg['particle_density']:g} (ATS rock density {p['rock_density']:g} kg m-3 is not matched) |
| retention | FC = {s['fc']:.5f} at {p['psi_fc']:g} MPa, WP = {s['wp']:.5f} at {p['psi_wp']:g} MPa, from the ATS van Genuchten curve (alpha {p['vg_alpha']:g} Pa-1, n {p['vg_n']:g}, sr {p['sr']:g}) |
| saturated conductivity | {s['ksat_mm_h']:.4f} mm h-1 = k rho g / mu, k = {p['permeability']:g} m2, mu at {p['T0']:g} K (ATS viscosity_water) |
| boundaries | closed: standalone `grid_mode=3` (no lateral flow or runoff), `RCHGDG=0` (no-flow bottom), no water table; ATS has no flow BCs |
| initial water | {s['theta0_source']} |
| forcing | `{cfg['forcing_rel']}`; standalone hour J of day d = the record at the start of that hour; precipitation = rain + snow, partitioned by EcoSIM in both runs (ATS variants: "EcoSIM precipitation" = true) |
| dates | both start on 1 January and run {p['ndays']:g} days with hourly steps |
| site | latitude {site['latitude [deg]']:g} and solar noon {site['solar noon [h]']:g} h from the forcing file; soil albedo {cfg['soil_albedo']:g} as hard-coded in the coupled path; no organic matter or litter |
| wind height | {wind_height:g} m: {wind_source}; `Z0G` of the standalone climate file |

## Not matched
- **Calendar labels:** the forcing file has a 365-day calendar, ATS takes start year {int(site['start year'])} from it, and the standalone years are labelled {years[0]} and {years[1]} (non-leap) so that both runs see the same days. Only the label and the GHG record of the year differ.
- **Soil water physics:** EcoSIM's own soil water solver and its retention curve, which agrees with van Genuchten only at FC and WP, against ATS Richards with van Genuchten. In the coupled run, ATS's soil state replaces EcoSIM's every hour.
- **Micropore conductivity:** EcoSIM has no temperature correction; ATS uses its temperature-dependent viscosity. Ksat is matched at {p['T0']:g} K.
- **Initial temperature and ice:** both start at {p['T0'] - 273.15:.2f} C, but they are not the same state. The standalone has no initial ice (`THI = -1`), so a start below the freezing point is liquid water far below it: in the first hour it freezes, releases latent heat and warms the whole column to about -0.5 C. ATS starts from a consistent frozen state and stays cold. Measured on the bare-ground pair with a start at -13 C: the standalone top layer was near -1 C under snow while ATS was near -12 C (about 10 K, for the first winter), and the first melt-out was 11 days apart. Starting both above the freezing point (ATS has no ice there either) removes this.
- **Bottom heat boundary:** {bottom}. EcoSIM instead exchanges heat with a deep source temperature `TKSD = ATCAG + 0.28 K` placed 1 m below the column bottom (conductivity `TCNDG`, 2.25 W m-1 K-1), and `ATCAG` is the initial temperature written to the grid file above, not a climate value. With a start at -13 C the two boundaries were 25 K apart (EcoSIM about -13 C, ATS 12 C) and the bottom layers drifted 17 K apart in two years. To match them, set the ATS bottom temperature to the initial temperature plus 0.28 K.
- **Thermal properties:** EcoSIM derives the soil conductivity from texture (a fixed loam here) as a weighted mean of the solid, water, ice and air terms; ATS uses the three-phase wet/dry Kersten model (`thermal conductivity` parameters of the input). On the same states the ATS values are about 1.5-1.7 times EcoSIM's for moist and frozen soil and less than half of it for dry soil (porosity 0.5, ATS 1.8 / 0.28 W m-1 K-1 saturated / dry: dry 0.28 vs 0.64, saturation 0.62 1.48 vs 0.93, saturated 1.8 vs 1.2, frozen with ice saturation 0.7 3.4 vs 2.0 W m-1 K-1). ATS parameters that follow EcoSIM's values (saturated 1.2, dry 0.64, unsaturated alpha unfrozen 1.38, frozen 1.0) changed the mean temperatures of the bare-ground pair by 0.3 K or less (first winter -1.2 to -1.5 C, year 2 skin bias 0.4 to 0.3 K) but added inadmissible-iterate warnings.
- **Sky longwave:** ATS computes incoming longwave from air temperature and vapor pressure with a clear-sky emissivity (no cloud term, about 194 W m-2 in the first winter); the standalone estimates it with a cloud term from its shortwave (about 189 W m-2 clear sky, up to 215 overcast). `standalone.longwave = "ats"` in the manifest gives the standalone the ATS values.
- **Hard-coded in the coupled path** (`ATSEcoSIMAdvanceMod.F90`): FC/WP water contents 0.5/0.25 and root-uptake conductivity 0.000571. The plant water and energy state is initialized once and then carried from hour to hour, as in the standalone.
{bare_text}
## Layers
| layer | dz [m] | depth to bottom [m] | initial theta [m3 m-3] |
|---|---|---|---|
{rows}
"""
    Path(path).write_text(text)


def check_file(path, expect):
    d = netCDF4.Dataset(path)
    for name, (lo, hi) in expect.items():
        v = np.ma.filled(d[name][...].astype(float), np.nan)
        v = v[np.isfinite(v)]
        if v.size and (v.min() < lo or v.max() > hi):
            raise ValueError(f"{path}:{name} range {v.min():g}..{v.max():g} outside {lo:g}..{hi:g}")
    d.close()


# ---------------------------------------------------------------------------
def generate_pairs(m, ecosim_src=render.DEFAULT_ECOSIM_SRC, ats_src=None, out=None, profile=None,
                   only=None, population=None, dry_run=False):
    """write the matched pairs of a suite (see the module description)"""
    cfg = dict(m.standalone)
    cfg["forcing"] = str(m.forcing)
    cfg["forcing_rel"] = os.path.relpath(m.forcing, m.root)       # as written in the README
    ecosim = Path(ecosim_src).resolve()
    out = Path(out).resolve() if out else m.pairs_dir
    ats_src = ats_src or render.WORKSPACE / "repos" / "dev" / "ats"
    years = cfg["years"]
    sweep = m.tests(only, baseline=False)
    base = [t for t in m.tests(only) if t.is_baseline]           # the bare-ground baseline, if the suite has one
    if not sweep and not base:
        raise SystemExit("no tests selected")
    pft_file = render.pft_file_path(m, ecosim)
    if pft_file is None:
        raise SystemExit("the manifest's standalone.pft_file is required for matched pairs")
    names, heights = pft_table(pft_file)
    with h5py.File(m.forcing, "r") as f:
        site = {k: float(f[k][0]) for k in ("latitude [deg]", "solar noon [h]", "start year",
                                             "start day of year [0-364]", "start hour [h]")}
    assert site["start day of year [0-364]"] == 0 and site["start hour [h]"] == 0, \
        "standalone runs start at 00:00 on 1 January"

    # soil and column are the same for every test: take them from the first rendered input
    probe = parse_xml(render.render_test(m, (sweep or base)[0], out / "probe" / "ats", ecosim, profile)).getroot()
    p = render.ats_parameters(probe)
    dz = layer_thicknesses(p["depth"], cfg["layers"], cfg["top_dz"])
    s = soil(p, dz, cfg)
    assert abs(dz.sum() - p["depth"]) < 1e-9 and len(dz) == cfg["layers"]
    assert p["porosity"] > s["fc"] > s["wp"] > p["porosity"] * p["sr"], (s["fc"], s["wp"])
    assert np.all((s["theta0"] > 0.0) & (s["theta0"] < 1.0)), "THW must be strictly in (0,1)"
    assert p["ndays"] <= 365 * len(years), "forcing years cover the run"
    if cfg["wind_height"] is not None:
        wind, wind_source = float(cfg["wind_height"]), "from the manifest"
    elif p["pk_wind_height"] is not None:
        wind, wind_source = p["pk_wind_height"], "the EcoSIM PK's `wind speed reference height [m]` of the input"
    else:
        wind, wind_source = 3.0, "the EcoSIM PK's default `wind speed reference height [m]`, which the input does not set"

    plan = []
    for test in sweep:
        q = test.params
        code, row = q["pft"], int(q["vegetation_type"])
        assert names[row - 1] == code, f"{test.name}: PFT row {row} is {names[row - 1]!r}, expected {code!r}"
        pop = (population or {}).get(test.group, q["population"])
        plan.append(dict(name=test.name, test=test, veg=test.group, code=code, row=row, lai=float(test.axis[1]),
                         sai=p["sai"], height=float(heights[row - 1]), population=float(pop),
                         assumed=bool(q.get("population_assumed")) and test.group not in (population or {})))

    print(f"layers: {cfg['layers']} over {p['depth']:g} m, dz {dz[0]:.4f} .. {dz[-1]:.4f} m")
    print(f"FC {s['fc']:.5f}  WP {s['wp']:.5f}  Ksat {s['ksat_mm_h']:.4f} mm/h  bulk density {s['bulk_density']:.4f}")
    print(f"initial theta {s['theta0'].min():.4f} .. {s['theta0'].max():.4f} ({s['theta0_source']})")
    print(f"run {p['ndays']:g} days; standalone years {tuple(years)}; PFT file {pft_file}; wind height {wind:g} m")
    for t in plan:
        print(f"  {t['name']:<24} {t['code']}  row {t['row']:>2}  LAI {t['lai']:<4g} SAI {t['sai']:g}  "
              f"height {t['height']:g} m  population {t['population']:g}")
    for t in base:
        bare_p = render.ats_parameters(parse_xml(render.render_test(m, t, out / t.name / "ats", ecosim, profile)).getroot())
        for k in ("depth", "ndays", "porosity", "permeability", "vg_alpha", "vg_n", "sr", "head", "T0", "bottom_T",
                  "psi_fc", "psi_wp"):
            assert bare_p[k] == p[k], f"{t.name}: {k} is {bare_p[k]} but the column of the pairs has {p[k]}"
        print(f"  {t.name:<24} bare ground (no plants), same soil column")
    if dry_run:
        print("dry run: nothing written")
        return plan

    common = out / "common"
    common.mkdir(parents=True, exist_ok=True)
    mesh = common / f"column_graded{cfg['layers']}.exo"
    write_mesh(dz, p["depth"], cfg["layers"], mesh, ats_src)
    write_grid(common / "grid_match.nc", s, p, site, ecosim / cfg["grid_template"], cfg)
    write_climate(common / "clim_match.nc", site, years, ecosim / cfg["climate_template"], m.forcing, wind,
                  cfg["longwave"])
    ghg = Path(cfg["ghg_file"]).name
    shutil.copy(ecosim / cfg["ghg_file"], common / ghg)
    check_file(common / "grid_match.nc",
               {"CDPTH": (0, p["depth"] + 1e-6), "FC": (0, 1), "WP": (0, 1), "THW": (0, 1)})
    check_file(common / "clim_match.nc",
               {"TMPH": (-60, 50), "SRADH": (0, 1400), "DWPTH": (0, 7), "RAINH": (0, 100), "WINDH": (0, 50)})

    for t in plan:
        pair = out / t["name"]
        (pair / "ats").mkdir(parents=True, exist_ok=True)
        (pair / "standalone").mkdir(parents=True, exist_ok=True)
        text = render.render_test(m, t["test"], pair / "ats", ecosim, profile)     # its own ATS input
        (pair / "ats_match.xml").write_text(
            write_ats_variant(t["name"], text, dz, p["depth"], pair, mesh, p["ndays"], years, cfg["layers"]))
        write_sp_phenol(pair / "sp_phenol.nc", t["code"], t["lai"], t["sai"], t["height"], t["population"], years[0])
        write_namelist(pair / "standalone.namelist", t["name"], os.path.relpath(pft_file, pair / "standalone"),
                       p["ndays"], years, ghg, cfg["history_fields"])
        print("wrote", pair)
    for t in base:
        pair = out / t.name
        (pair / "ats").mkdir(parents=True, exist_ok=True)
        (pair / "standalone").mkdir(parents=True, exist_ok=True)
        text = write_ats_variant(t.name, render.render_test(m, t, pair / "ats", ecosim, profile), dz, p["depth"], pair,
                                 mesh, p["ndays"], years, cfg["layers"])
        # the standalone sets the roughness of a mineral soil to 0.02 m (Hour1Mod.F90), the coupling takes it from the input
        text = xmledit.edit_text(text, [{"set": "PKs/EcoSIM for ATS/soil surface roughness height [m]",
                                         "value": STANDALONE_ROUGHNESS}])
        (pair / "ats_match.xml").write_text(text)
        write_namelist(pair / "standalone.namelist", t.name, os.path.relpath(pft_file, pair / "standalone"),
                       p["ndays"], years, ghg, cfg["history_fields"], plants=False)
        print("wrote", pair, "(bare ground)")
    if only:
        print("README.md not rewritten for a subset of the tests")
    else:
        write_readme(out / "README.md", p, s, site, years, plan, cfg, wind, wind_source, [t.name for t in base])
        print("wrote", out / "README.md")
    return plan
