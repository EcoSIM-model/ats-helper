"""Matched pairs for the microbe cases: an ATS-EcoSIM input with the microbial model on, and the standalone
EcoSIM case that matches it.

Unlike the vegetation pairs of pairs.py, these inputs keep their own mesh (the generated column of the
input, uniform layers) and their own forcing (constants, or a daily table that ATS interpolates linearly).
For every test pairs/<test>/ gets:

  ats_match.xml          the input with its data files and the PFT file rebased to the run directory
  grid.nc                standalone grid: the layers of the ATS mesh, soil, organic matter, litter, nutrients
  clim.nc                standalone hourly climate, the forcing ATS sees at the start of every hour
  sp_phenol.nc           prescribed phenology (inputs with `prescribe phenology`)
  standalone.namelist    standalone EcoSIM namelist, microbial model on
  <pft file>, <ghg file> copies, so the file names stay short (EcoSIM truncates them at 128 characters)
  pair.json              what was matched (read by the report)

Run directories: pairs/<test>/ats/ and pairs/<test>/standalone/ (all paths are relative to them).
Needs numpy, h5py and netCDF4.
"""

import json
import math
import re
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import h5py
import netCDF4
import numpy as np

from . import pairs, render, xmledit
from .case import CaseError
from .xmledit import array_values, param, parse_xml, plist

# ---------------------------------------------------------------------------
# settings the ATS input cannot give (overridable with options)
# ---------------------------------------------------------------------------
MICROBE_OPTIONS = {
    "pft_file": "input_data/ecosim_pftpar_20261021.nc",      # used when the input's own `engine input file` is not found
    "ghg_file": "input_data/fatm_hist_GHGs_1750-2023.nc",
    "grid_template": "examples/inputs/blodgett/Blodget_grid_20251115.nc",
    "climate_template": "examples/inputs/blodgett/Blodget.clim.2012-2022.nc",
    "soil_albedo": 0.2,
    "particle_density": 2.66,
    "texture": (400.0, 400.0),         # g sand, silt per kg: a fixed loam, as the vegetation pairs
    # XRADH (hourly longwave) of the climate file: the fork's standalone reads MJ m-2 h-1 without converting
    # (ClimReadMod.F90; the pinned EcoSIM converts W m-2 itself); 0.0036 MJ m-2 h-1 per W m-2, or 1.0 for W m-2
    "xradh_factor": 0.0036,
    # The fork's standalone has no prescribed-phenology input (ldo_sp_mode uses hard-coded monthly LAI/SAI): with
    # False, an input with `prescribe phenology` is paired plant-free (the ATS variant turns phenology off too).
    # Set True for an EcoSIM with the `sp_phenol_in` namelist option (the pinned standalone_match one).
    "sp_phenol_input": False,
    "layers": 19,                      # graded layers when the input has more than the standalone can hold
    "top_dz": 0.10,                    # m, top layer of the graded column (0.05 m, with a 1.9 m bottom layer, made the ATS
                                       # column lose its timestep at day 343; 0.10 m, 1.5 m bottom, completes the year)
    "ph": 7.0,
    "cec": 15.0,                       # cmol kg-1, the box generator's values for a neutral loam
    "aec": 1.0,
}
# What differs between the builds the pairs can target, read from their source trees (--ecosim-src, --ats-src: they must
# be the ones the executables were built from) or forced with --build fork|merged.
FORK_BUILD = dict(name="fork", sp_phenol_input=False, xradh_factor=0.0036, acetate_field="acetate_vr",
                  semi_implicit=False, n_heter=None, min_layers=0)
MERGED_BUILD = dict(name="merged", sp_phenol_input=False, xradh_factor=1.0, acetate_field="Acetate_vr",
                    semi_implicit=True, n_heter=8, min_layers=5)


def _text(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def detect_build(ecosim, ats_src=None, mode="auto"):
    """the features of the executables the pairs will run on, from their source trees"""
    if mode == "fork":
        return dict(FORK_BUILD)
    if mode == "merged":
        return dict(MERGED_BUILD)
    f90 = Path(ecosim) / "f90src"
    b = dict(FORK_BUILD)
    b["name"] = "fork"
    # Prescribed phenology (and so a matched canopy) is only usable if the standalone keeps its microbial model on with
    # it; its driver (EcoSIMAPI.F90) turns the microbial model off in that mode, so a microbe pair stays plant-free.
    driver = _text(Path(ecosim) / "drivers" / "ecosim" / "EcoSIMAPI.F90")
    sp_turns_microbes_off = re.search(r"if\(ldo_sp_mode\)then(?:(?!endif).){0,400}?microbial_model\s*=\s*\.false\.", driver, re.S)
    if (f90 / "IOutils" / "Read4SatePhenolMod.F90").is_file() and not sp_turns_microbes_off:
        b["sp_phenol_input"] = True
    if "3600.e-6" in _text(f90 / "IOutils" / "ClimReadMod.F90").split("RadLWClm(JJ,II)=")[-1][:80]:
        b["xradh_factor"] = 1.0                                                # the standalone converts W m-2 itself
    if (f90 / "Microbial_bgc" / "Box_Micmodel" / "FungiMod.F90").is_file():   # the restructured microbial model: 8 heterotroph guilds
        b["n_heter"] = 8
    if "AREA_3D(3,L,NY,NX)" in _text(f90 / "HydroTherm" / "SnowPhys" / "SnowBalanceMod.F90"):
        b["min_layers"] = 5                                                    # snow layers index past a column of fewer soil layers
    if any("fname='Acetate_vr'" in _text(x) for x in (f90 / "IOutils" / "HistData").glob("*.F90")):
        b["acetate_field"] = "Acetate_vr"
    ats = Path(ats_src or render.default_ats_src())
    if "semi-implicit surface heat exchange" in _text(ats / "src" / "pks" / "ecosim" / "EcoSIM_ATS_interface.cc"):
        b["semi_implicit"] = True
    if (f90 / "IOutils" / "Read4SatePhenolMod.F90").is_file() or b["semi_implicit"] or b["n_heter"]:
        b["name"] = "merged"
    return b


# ground chemistry of the box generator (generate_box_microbe_grid.py): all mineral pools beyond N, P
GROUND = {"CCA": 40.0, "CMG": 10.0, "CNA": 0.05, "CKA": 0.0, "CSO4": 48.0, "CCL": 35.0, "CALPO": 50.0,
          "CFEPO": 50.0, "CCAPD": 0.0, "CCAPH": 0.0, "CALOH": 1000.0, "CFEOH": 1000.0, "CCACO": 0.0, "CCASO": 0.0,
          "GKC4": 1.0, "GKCH": 0.25, "GKCA": 0.25, "GKCM": 0.6, "GKCN": 0.16, "GKCK": 3.0}
# litter seeded by the coupling at start-up, as fractions of the layer's organic carbon, nitrogen and phosphorus
# (ATSEcoSIMInitMod.F90): woody, fine, manure
LITTER_FRACTIONS = {"w": 0.05, "f": 0.10, "m": 0.02}
MOLAR_DENSITY_WATER = 55500.0         # mol m-3 (ATS liquid at the start temperature, close enough for a seed)
ATOMIC_MASS = {"N": 14.007, "P": 30.974, "C": 12.011}
SPECIES_N = {"NH4": 1, "NO3": 1, "NO2": 1, "NH3": 1}
SPECIES_P = {"H1PO4": 1, "H2PO4": 1}
DAYS_PER_YEAR = 365
STANDALONE_SLOTS = 20                 # the standalone fixes JZ = 20 (GridMod.F90): at most 19 layers and one unused slot

# standalone history: the fork's names (HistDataType.F90). Totals per layer; the fork has no per-group biomass.
HISTORY_FIELDS = [
    "PSI_vr", "VSPore_vr", "TEMP_vr",
    "tMicrobeC_vr", "tMicrobeN_vr", "tMicrobeP_vr", "tSOC_vr", "tSON_vr", "POM_C_vr", "MAOM_C_vr",
    "litrC_vr", "litrN_vr", "litrP_vr",
    "NH4_vr", "NO3_vr", "DOC_vr", "DON_vr", "DOP_vr", "acetate_vr",
    "CO2w_conc_vr", "CH4w_conc_vr", "O2w_conc_vr", "N2Ow_conc_vr", "NH3w_conc_vr", "H2w_conc_vr", "N2w_conc_vr",
    "Arw_conc_vr",
    "tMICROB_C_col", "tMICROB_N_col", "tMICROB_P_col", "tNH4_col", "tNO3_col", "DOC_soil_col", "DON_soil_col",
    "ECO_HR_CO2_col", "HR_CO2_litr_col", "CH4_DIF_FLX_col", "N2O_SEMIS_FLX_col",
]
PLANT_FIELDS = {"RootH2OUptake_vr"}


# ---------------------------------------------------------------------------
# reading the input
# ---------------------------------------------------------------------------
def is_microbe_input(root):
    """True if the EcoSIM PK of the input has the microbial model on"""
    try:
        eco = plist(root, "PKs", "EcoSIM for ATS")
        return param(eco, "microbe model").get("value").lower() == "true"
    except KeyError:
        return False


def _first_constant(node):
    vals = [p for p in node.iter("Parameter") if p.get("name") == "value"]
    return float(vals[0].get("value")) if vals else None


def resolve_data(xml_dir, value):
    """a data file named in the input: relative to the input, else the same file name in the case's data/ folder"""
    path = Path(value)
    if path.is_absolute():
        return path
    for cand in ((Path(xml_dir) / path), Path(xml_dir) / "data" / path.name):
        if cand.is_file():
            return cand.resolve()
    raise CaseError(f"data file {value!r} not found next to the input ({xml_dir}) or in {Path(xml_dir) / 'data'}")


def _forcing_function(evaluator, xml_dir):
    """('constant', value) or ('table', file, x header, y header) of an independent-variable evaluator"""
    tab = [n for n in evaluator.iter("ParameterList") if n.get("name") == "function-tabular"]
    if tab:
        t = tab[0]
        f = param(t, "file").get("value")
        form = [p for p in t if p.get("name") == "forms"]
        return ("table", str(resolve_data(xml_dir, f)), param(t, "x header").get("value"), param(t, "y header").get("value"),
                bool(form))
    const = [n for n in evaluator.iter("ParameterList") if n.get("name") == "function-constant"]
    if const:
        return ("constant", _first_constant(const[0]))
    if evaluator.find("./Parameter[@name='value']") is not None:
        return ("constant", float(evaluator.find("./Parameter[@name='value']").get("value")))
    raise CaseError(f"cannot read the forcing function of {evaluator.get('name')!r}")


def _pk_list(root):
    return plist(root, "PKs")


def read_input(xml_path, case_root, data_dirs=()):
    """everything the matched pair needs, from an ATS-EcoSIM input with the microbial model; raises CaseError"""
    xml_path = Path(xml_path)
    xml_dir = xml_path.parent
    root = parse_xml(xml_path.read_text()).getroot()
    P = xmledit

    def need(what, fn):
        try:
            return fn()
        except (KeyError, ValueError, AttributeError, IndexError, TypeError) as err:
            raise CaseError(f"{xml_path.name}: cannot read {what} from the input ({err})") from None

    pks = need("the PKs", lambda: plist(root, "PKs"))
    ev = need("the evaluators", lambda: plist(root, "state", "evaluators"))
    eco = need("the EcoSIM PK", lambda: plist(pks, "EcoSIM for ATS"))
    if not is_microbe_input(root):
        raise CaseError(f"{xml_path.name}: the EcoSIM PK does not have `microbe model` = true")
    gen = need("a generated mesh", lambda: plist(root, "mesh", "domain", "generate mesh parameters"))
    lo, hi = array_values(param(gen, "domain low coordinate")), array_values(param(gen, "domain high coordinate"))
    ncells = [int(v) for v in array_values(param(gen, "number of cells"))]
    if len(ncells) != 3 or ncells[0] != 1 or ncells[1] != 1:
        raise CaseError(f"{xml_path.name}: the mesh has {ncells} cells; the pair supports one column (1 x 1 x N)")
    area = (hi[0] - lo[0]) * (hi[1] - lo[1])
    driver = need("the cycle driver", lambda: plist(root, "cycle driver"))
    units = param(driver, "end time units").get("value")
    ndays = float(param(driver, "end time").get("value")) * {"d": 1.0, "s": 1 / 86400.0, "yr": 365.0}[units]
    wrm = need("the van Genuchten parameters", lambda: plist(root, "state", "model parameters", "WRM parameters", "mineral"))
    flow_ic = need("the flow initial conditions", lambda: plist(pks, "subsurface flow", "initial conditions"))
    energy_ic = need("the energy initial conditions", lambda: plist(pks, "subsurface energy", "initial conditions"))
    gravity = need("gravity", lambda: array_values(param(plist(root, "state", "initial conditions", "gravity"), "value")))

    def maybe(fn):
        try:
            return fn()
        except (KeyError, ValueError, AttributeError, IndexError, TypeError):
            return None

    def site(name, header, default=None):
        """a site or calendar parameter of the EcoSIM PK: a value, or a sublist read from a file (one-element dataset)"""
        node = next((c for c in eco if c.get("name") == name), None)
        if node is None:
            return default
        if node.tag == "Parameter":
            return float(node.get("value"))
        data = resolve_data(xml_dir, param(node, "file").get("value"))
        hd = maybe(lambda: param(node, "header").get("value")) or header
        with h5py.File(data, "r") as h:
            return float(np.asarray(h[hd][...]).ravel()[0])

    cal = dict(year=site("starting year", "start year"), doy=site("starting day of year [0-364]", "start day of year [0-364]"),
               hour=site("starting hour [h]", "start hour [h]", 0.0), latitude=site("latitude [degrees]", "latitude [deg]"),
               noon=site("solar noon [h]", "solar noon [h]"))
    if cal["year"] is None or cal["doy"] is None:
        raise CaseError(f"{xml_path.name}: the EcoSIM PK needs `starting year` and `starting day of year [0-364]`")
    if cal["hour"] != 0.0:
        raise CaseError(f"{xml_path.name}: the pair starts the standalone at 00:00; the input starts at hour {cal['hour']:g}")
    p = dict(
        name=xml_path.stem, xml=str(xml_path), ncells=ncells[2], depth=hi[2] - lo[2], area=area, ndays=ndays,
        porosity=need("base_porosity", lambda: float(P.constant_value(plist(ev, "base_porosity")).get("value"))),
        permeability=need("permeability", lambda: float(P.constant_value(plist(ev, "permeability")).get("value"))),
        rock_density=need("density_rock", lambda: float(P.constant_value(plist(ev, "density_rock")).get("value"))),
        vg_alpha=float(param(wrm, "van Genuchten alpha [Pa^-1]").get("value")),
        vg_n=float(param(wrm, "van Genuchten n [-]").get("value")),
        sr=float(param(wrm, "residual saturation [-]").get("value")),
        g=-gravity[2], rho=1000.0,
        T0=need("the initial temperature", lambda: float(param(energy_ic, "value").get("value"))),
        bottom_T=maybe(lambda: float(next(
            q for q in plist(pks, "subsurface energy", "boundary conditions", "temperature").iter("Parameter")
            if q.get("name") == "value").get("value"))),
        psi_fc=float(param(eco, "field capacity [Mpa]").get("value")),
        psi_wp=float(param(eco, "wilting point [Mpa]").get("value")),
        prescribed=param(eco, "prescribe phenology").get("value").lower() == "true",
        engine_file=param(eco, "engine input file").get("value"),
        n_components=int(param(eco, "number of mole fraction components").get("value")),
        n_heter=int(maybe(lambda: param(eco, "number of heterotrophic guilds").get("value")) or 7),
        n_autor=int(maybe(lambda: param(eco, "number of autotrophic guilds").get("value")) or 6),
        start_doy=int(cal["doy"]), start_year=int(cal["year"]), site_latitude=cal["latitude"], solar_noon=cal["noon"],
        precip_by_ecosim=param(eco, "EcoSIM precipitation").get("value").lower() == "true",
        atmosphere={k: float(param(eco, f"atmospheric {k}").get("value"))
                    for k in ("N2", "O2", "CO2", "CH4", "N2O", "H2", "NH3")
                    if maybe(lambda k=k: param(eco, f"atmospheric {k}")) is not None},
    )
    # flow IC: a hydrostatic head, or a constant pressure (atmospheric = saturated)
    head = maybe(lambda: float(param(flow_ic, "hydrostatic head [m]").get("value")))
    pressure = maybe(lambda: float(param(flow_ic, "value").get("value")))
    p["head"], p["pressure"] = head, pressure
    if head is None and pressure is None:
        raise CaseError(f"{xml_path.name}: the flow initial condition is neither a hydrostatic head nor a constant pressure")
    # transported species and their initial mole fractions (constant per species)
    tr = need("the subsurface transport PK", lambda: plist(pks, "subsurface transport"))
    names = [s.strip() for s in param(tr, "component names").get("value").strip("{}").replace("\n", " ").split(",")]
    p["components"] = names
    ics = []
    ic_fn = maybe(lambda: plist(tr, "initial conditions", "function", "ic subsurface", "function"))
    if ic_fn is not None:
        for k in range(1, len(names) + 1):
            node = maybe(lambda k=k: plist(ic_fn, f"dof {k} function"))
            ics.append(_first_constant(node) if node is not None else None)
    p["initial_mole_fractions"] = dict(zip(names, ics)) if ics else {}
    p["transport"] = {"names": names,
                      "bc": maybe(lambda: [q.get("name") for q in plist(tr, "boundary conditions")]) or []}
    if p["n_components"] != len(names):
        raise CaseError(f"{xml_path.name}: {p['n_components']} mole fraction components but {len(names)} transported species")
    # vegetation (prescribed phenology): constant LAI, SAI and PFT row
    if p["prescribed"]:
        p["lai"] = maybe(lambda: float(P.constant_value(plist(ev, "surface-LAI")).get("value")))
        p["sai"] = maybe(lambda: float(P.constant_value(plist(ev, "surface-SAI")).get("value")))
        p["vegetation_type"] = maybe(lambda: int(float(P.constant_value(plist(ev, "surface-vegetation_type")).get("value"))))
        npft = maybe(lambda: int(param(eco, "number of pfts").get("value")))
        if None in (p["lai"], p["sai"], p["vegetation_type"]) or npft not in (None, 1):
            raise CaseError(f"{xml_path.name}: prescribed phenology needs constant surface-LAI, surface-SAI and "
                            "surface-vegetation_type evaluators and one PFT")
    # forcing: constant, or a table ATS interpolates (linear unless the table says otherwise)
    forcing = {}
    for key, name in (("air_temperature", "surface-air_temperature"), ("shortwave", "surface-incoming_shortwave_radiation"),
                      ("wind", "surface-wind_speed"), ("vapor_pressure", "surface-vapor_pressure_air"),
                      ("rain", "surface-precipitation_rain"), ("snow", "surface-precipitation_snow")):
        e = maybe(lambda name=name: plist(ev, name))
        if e is None:
            if key == "snow":
                forcing[key] = ("constant", 0.0)
                continue
            raise CaseError(f"{xml_path.name}: no {name} evaluator")
        forcing[key] = _forcing_function(e, xml_dir)
        if forcing[key][0] == "table" and forcing[key][4]:
            raise CaseError(f"{xml_path.name}: the forcing table of {name} has its own interpolation form; "
                            "only the default (linear) is supported")
    p["forcing"] = forcing
    longwave = maybe(lambda: plist(ev, "surface-incoming_longwave_radiation"))
    p["longwave"] = None
    if longwave is not None:
        try:
            p["longwave"] = _forcing_function(longwave, xml_dir)
        except CaseError:                      # a computed evaluator: ATS derives it from air temperature and vapor pressure
            kind = maybe(lambda: param(longwave, "evaluator type").get("value")) or ""
            if "longwave" not in kind and "incoming" not in kind:
                raise
            p["longwave"] = ("ats", kind)
    p["wind_height"] = maybe(lambda: float(param(eco, "wind speed reference height [m]").get("value")))
    src = maybe(lambda: param(plist(pks, "surface energy"), "source term").get("value"))
    p["surface_energy_coupled"] = src is not None and src.lower() == "true"
    return p


# ---------------------------------------------------------------------------
# physical setup shared by the writers
# ---------------------------------------------------------------------------
def plan(xml_path, case_root, cfg, ecosim):
    """the validated plan of one input"""
    p = read_input(xml_path, case_root)
    named = ecosim / "input_data" / Path(p["engine_file"]).name
    pft_file = Path(cfg["pft_file_path"]) if cfg.get("pft_file_path") else (named if named.is_file() else ecosim / cfg["pft_file"])
    if not pft_file.is_file():
        raise CaseError(f"{Path(xml_path).name}: PFT parameter file not found: {pft_file} (see --pft-file)")
    ghg = ecosim / cfg["ghg_file"]
    if not ghg.is_file():
        raise CaseError(f"GHG file not found: {ghg}")
    if p["ndays"] != int(p["ndays"]):
        raise CaseError(f"{Path(xml_path).name}: the run is {p['ndays']:g} days; the pair needs a whole number of days")
    build = {k: cfg[k] for k in ("name", "sp_phenol_input", "xradh_factor", "acetate_field", "semi_implicit", "n_heter",
                                 "min_layers") if k in cfg}
    # The merged standalone stops on its energy conservation check when it starts mid-year (verified: the same box
    # passes from 1 January), and the forcing of the inputs starts on 1 January: both models start on day 0 there.
    want = cfg.get("start_day", "auto")
    day = 0 if want == "auto" and build.get("name") == "merged" else (None if want in ("auto", "input") else int(want))
    if day is not None and day != p["start_doy"]:
        p["input_start_doy"], p["start_doy"], p["calendar_shifted"] = p["start_doy"], day, True
    if build.get("n_heter"):
        p["n_heter"] = build["n_heter"]
    regraded = p["ncells"] > STANDALONE_SLOTS - 1
    if p["ncells"] < build.get("min_layers", 0):               # a build whose snow code needs this many soil layers
        p["input_ncells"], p["layers_raised"] = p["ncells"], True
        p["ncells"] = build["min_layers"]
        dz = np.full(p["ncells"], p["depth"] / p["ncells"])
    elif regraded:
        nl = int(cfg["layers"])
        if not 1 <= nl <= STANDALONE_SLOTS - 1:
            raise CaseError(f"--layers must be 1..{STANDALONE_SLOTS - 1}: the standalone holds {STANDALONE_SLOTS} layer slots")
        dz = pairs.layer_thicknesses(p["depth"], nl, cfg["top_dz"])
        p["input_ncells"] = p["ncells"]
        p["ncells"] = nl
    else:
        dz = np.full(p["ncells"], p["depth"] / p["ncells"])
    s = pairs.soil({**p, "head": p["head"] if p["head"] is not None else 0.0}, dz, cfg)
    if p["head"] is None:                      # constant pressure: atmospheric at the cell means saturated
        s["theta0"] = np.full(p["ncells"], p["porosity"])
        s["theta0_source"] = f"saturated: the input's initial pressure is {p['pressure']:g} Pa (atmospheric)"
    if not p["porosity"] > s["fc"] > s["wp"] > p["porosity"] * p["sr"]:
        raise CaseError(f"{Path(xml_path).name}: field capacity {s['fc']:.4f} and wilting point {s['wp']:.4f} are not "
                        "between the porosity and the residual water content")
    veg = None
    if p["prescribed"] and not cfg.get("sp_phenol_input"):
        p["phenology_dropped"] = True                       # the pair is plant-free; see the README
    if p["prescribed"] and cfg.get("sp_phenol_input"):
        names, heights = pairs.pft_table(pft_file)
        row = p["vegetation_type"]
        if not 1 <= row <= len(names):
            raise CaseError(f"{Path(xml_path).name}: vegetation type {row} is not a row of {pft_file.name} (1..{len(names)})")
        code, group = names[row - 1], pairs.PFT_GROUPS.get(names[row - 1], names[row - 1])
        given = cfg.get("populations", {}).get(group) or cfg.get("populations", {}).get(code)
        population = given if given is not None else pairs.POPULATIONS.get(group, pairs.ASSUMED_POPULATION)
        veg = dict(code=code, row=row, group=group, lai=p["lai"], sai=p["sai"], height=float(heights[row - 1]),
                   population=float(population), assumed=given is None and group not in pairs.POPULATIONS)
    nyears = math.ceil((p["start_doy"] + p["ndays"]) / DAYS_PER_YEAR)
    years = [p["start_year"] + i for i in range(nyears)]
    lat = p["site_latitude"] if p["site_latitude"] is not None else 38.8837
    for f in ([] if p["site_latitude"] is not None else p["forcing"].values()):
        if f[0] == "table":
            with h5py.File(f[1], "r") as h:
                lat = float(h.attrs.get("DayMet latitude [deg]", lat))
            break
    return dict(p=p, s=s, dz=dz, veg=veg, pft_file=pft_file, ghg=ghg, years=years, latitude=lat, test=Path(xml_path),
                regraded=regraded, build=build)


def layer_values(p, cfg):
    """organic matter and nutrient seeds of the layers, as the coupled start-up makes them"""
    n = p["ncells"]
    L = np.arange(1, n + 1, dtype=float)                       # EcoSIM layer 1 is the surface layer
    corgc = 20.0 * np.exp(-0.3 * L)                            # ATSEcoSIMInitMod.F90: a_CORGC = 20 exp(-0.3 L)
    return corgc, corgc / 12.0, corgc / 150.0


def mole_fraction_to_soil_conc(p, s, species, mw):
    """g of the element per Mg of dry soil of a dissolved species with a given mole fraction in the water"""
    theta = s["theta0"]
    mol_per_m3 = p["initial_mole_fractions"].get(species, 0.0) * MOLAR_DENSITY_WATER * theta
    return mol_per_m3 * mw / s["bulk_density"]


# ---------------------------------------------------------------------------
# standalone files
# ---------------------------------------------------------------------------
def write_grid(path, pl, cfg, template):
    """grid file with one layer per ATS cell (and one unused slot), made from the template's variables"""
    p, s = pl["p"], pl["s"]
    n = p["ncells"]
    t = netCDF4.Dataset(template)
    d = netCDF4.Dataset(path, "w", format=t.file_format)
    d.setncatts({k: t.getncattr(k) for k in t.ncattrs()})
    for name, dim in t.dimensions.items():
        d.createDimension(name, STANDALONE_SLOTS if name == "nlevs" else (None if dim.isunlimited() else len(dim)))
    for name, v in t.variables.items():
        fill = v.getncattr("_FillValue") if "_FillValue" in v.ncattrs() else None
        nv = d.createVariable(name, v.dtype, v.dimensions, fill_value=fill)
        nv.setncatts({k: v.getncattr(k) for k in v.ncattrs() if k != "_FillValue"})
        if "nlevs" in v.dimensions:                        # layer arrays: the template's first layer for every layer
            nv[...] = np.repeat(np.asarray(v[..., :1]), STANDALONE_SLOTS, axis=-1)
        else:
            nv[...] = v[...]
    t.close()

    def layers(values):
        a = np.zeros(STANDALONE_SLOTS)
        a[:n] = values
        return a[None, :]
    flat = lambda v: layers(np.full(n, v))
    corgc, corgn, corgp = layer_values(p, cfg)
    set_ = {"ALATG": pl["latitude"], "ATCAG": p["T0"] - 273.15, "IETYPG": 0, "IDTBLG": 0,
            "RCHQNG": 0, "RCHQEG": 0, "RCHQSG": 0, "RCHQWG": 0, "RCHGNUG": 0, "RCHGEUG": 0, "RCHGSUG": 0,
            "RCHGWUG": 0, "RCHGDG": 0, "ASPX": 0, "SL0": 0, "DPTHSX": 0, "ALBS": cfg["soil_albedo"],
            "PSIFC": p["psi_fc"], "PSIWP": p["psi_wp"], "NUI": 1, "NJ": n, "NL1": 0, "NL2": 0}
    atm = p["atmosphere"]                                   # ppm in the grid file
    for key, name in (("O2", "OXYEG"), ("N2", "Z2GEG"), ("CO2", "CO2EIG"), ("CH4", "CH4EG"), ("N2O", "Z2OEG"),
                      ("NH3", "ZNH3EG")):
        if key in atm and name in d.variables:
            set_[name] = atm[key] * 1e6
    for k, v in set_.items():
        d[k][...] = v
    d["DHI"][...] = math.sqrt(p["area"])
    d["DVI"][...] = math.sqrt(p["area"])
    d["CDPTH"][...] = layers(s["depth_bottom"])
    d["BKDSI"][...] = layers(s["bulk_density"])
    d["FC"][...] = layers(s["fc"])
    d["WP"][...] = layers(s["wp"])
    d["SCNV"][...] = layers(s["ksat_mm_h"])
    d["SCNH"][...] = layers(s["ksat_mm_h"])
    d["FHOL"][...] = flat(0.0)
    d["ROCK"][...] = flat(0.0)
    d["THW"][...] = layers(s["theta0"])
    d["THI"][...] = flat(-1.0)                              # no initial ice (THI = 0 would put ice at WP)
    d["CSAND"][...] = flat(cfg["texture"][0])
    d["CSILT"][...] = flat(cfg["texture"][1])
    d["PH"][...] = flat(cfg["ph"])
    d["CEC"][...] = flat(cfg["cec"])
    d["AEC"][...] = flat(cfg["aec"])
    # organic matter exactly as the coupled start-up sets it
    d["CORGC"][...] = layers(corgc)
    d["CORGN"][...] = layers(corgn)
    d["CORGP"][...] = layers(corgp)
    d["CORGR"][...] = layers(0.1 * corgc)
    # litter seeded in the layers (the coupling writes 5 % woody, 10 % fine, 2 % manure of the layer's organic C, N, P)
    for tag, frac in LITTER_FRACTIONS.items():
        for el, vals in (("C", corgc), ("N", corgn), ("P", corgp)):
            name = f"RS{el}{tag}L"
            if name in d.variables:
                d[name][...] = layers(frac * vals)
    for name in ("RSCf", "RSNf", "RSPf", "RSCw", "RSNw", "RSPw", "RSCm", "RSNm", "RSPm"):   # no surface litter
        d[name][...] = 0.0
    # inorganic nutrients: the seed is the ATS initial mole fractions converted to soil concentrations
    nh4 = sum(mole_fraction_to_soil_conc(p, s, sp, ATOMIC_MASS["N"]) for sp in ("NH4",))
    no3 = sum(mole_fraction_to_soil_conc(p, s, sp, ATOMIC_MASS["N"]) for sp in ("NO3",))
    po4 = sum(mole_fraction_to_soil_conc(p, s, sp, ATOMIC_MASS["P"]) for sp in ("H1PO4", "H2PO4"))
    d["CNH4"][...] = layers(nh4)
    d["CNO3"][...] = layers(no3)
    d["CPO4"][...] = layers(po4)
    for k in ("CAL", "CFE"):
        d[k][...] = np.full((1, STANDALONE_SLOTS), -1.0)           # computed from the soil chemistry
    for k, v in GROUND.items():
        if k in d.variables:
            d[k][...] = flat(v)
    d.close()


def interpolate(table, t):
    """linear interpolation of a forcing table at times t [s] (ATS function-tabular)"""
    path, xh, yh = table[1], table[2], table[3]
    with h5py.File(path, "r") as f:
        x, y = f[xh][:], f[yh][:]
    return np.interp(t, x, y)


def hourly_forcing(p, ndays):
    """hourly series of the forcing ATS sees at the start of every hour, in the units of the standalone"""
    nh = int(ndays) * 24
    t = 3600.0 * np.arange(nh)
    out = {}
    for key, f in p["forcing"].items():
        out[key] = np.full(nh, f[1]) if f[0] == "constant" else interpolate(f, t)
    if not p["surface_energy_coupled"]:
        # ATS exchanges no surface energy with EcoSIM here (and holds the soil at the input's temperature), but EcoSIM
        # always solves its surface energy balance: the standalone gets air saturated at the air temperature (no
        # evaporative cooling) and no XRADH, so EcoSIM estimates the sky longwave itself. Measured on the box, the
        # soil then stays within 2 K of the 298 K ATS holds; any XRADH tried heated it by 17-20 K (microbial heat).
        tc = out["air_temperature"] - 273.15
        out["vapor_pressure"] = 611.2 * np.exp(17.67 * tc / (tc + 243.5))
        return out, None
    lw = p["longwave"]
    longwave = None
    if lw is not None:
        if lw[0] == "ats":
            longwave = pairs.ats_sky_longwave(out["air_temperature"], out["vapor_pressure"])
        else:
            longwave = np.full(nh, lw[1]) if lw[0] == "constant" else interpolate(lw, t)
    return out, longwave


def write_climate(path, pl, template, ndays, xradh_factor=1.0):
    p = pl["p"]
    years = pl["years"]
    nh = int(ndays) * 24
    f, longwave = hourly_forcing(p, ndays)
    arrays = {
        "TMPH": f["air_temperature"] - 273.15,
        "WINDH": f["wind"],
        "RAINH": (f["rain"] + f["snow"]) * 1000.0 * 3600.0,          # m s-1 -> mm h-1 (snow as water equivalent)
        "DWPTH": f["vapor_pressure"] / 1000.0,                         # Pa -> kPa
        "SRADH": f["shortwave"],
    }
    t = netCDF4.Dataset(template)
    d = netCDF4.Dataset(path, "w", format=t.file_format)
    d.setncatts({k: t.getncattr(k) for k in t.ncattrs()})
    for name, dim in t.dimensions.items():
        d.createDimension(name, None if dim.isunlimited() else len(dim))
    for name, v in t.variables.items():
        fill = v.getncattr("_FillValue") if "_FillValue" in v.ncattrs() else None
        nv = d.createVariable(name, v.dtype, v.dimensions, fill_value=fill)
        nv.setncatts({k: v.getncattr(k) for k in v.ncattrs() if k != "_FillValue"})
    t.close()
    if longwave is not None:
        x = d.createVariable("XRADH", "f8", ("year", "day", "hour", "ngrid"))
        x.long_name, x.units = "Incoming longwave radiation (hourly)", "W m-2"
        arrays["XRADH"] = longwave * xradh_factor
    ny = len(years)
    for key, series in arrays.items():
        buf = np.empty((ny, 366, 24, 1))
        buf[...] = series[0]
        for k in range(nh):                                         # hour k of the run -> (year, day of year, hour)
            day = p["start_doy"] + k // 24
            buf[day // DAYS_PER_YEAR, day % DAYS_PER_YEAR, k % 24, 0] = series[k]
        for yi in range(ny):                                         # day 366 repeats day 365 (unused)
            buf[yi, 365, :, 0] = buf[yi, 364, :, 0]
        d[key][0:ny, ...] = buf
    d["year"][0:ny] = years
    d["Z0G"][0:ny, :] = p["wind_height"] if p["wind_height"] is not None else 3.0
    d["IFLGW"][0:ny, :] = 0
    d["ZNOONG"][0:ny, :] = p["solar_noon"] if p.get("solar_noon") is not None else 12.0
    d["PHRG"][0:ny, :] = 7.0
    for k in ("CN4RIG", "CNORIG", "CPORG", "CALRG", "CFERG", "CCARG", "CMGRG", "CNARG", "CKARG", "CSORG", "CCLRG"):
        d[k][0:ny, :] = 0.0
    d.close()


NAMELIST = """&regression_test
 cells = 1
/

&ecosim
case_name          = '{name}'
do_regression_test = .false.
pft_file_in        = '../{pft}'
grid_file_in       = '../grid.nc'
pft_mgmt_in        = 'NO'
{phenology_file}
clm_hour_file_in   = '../clim.nc'
clm_factor_in      = 'NO'
soil_mgmt_in       = 'NO'
atm_ghg_in         = '../{ghg}'
atm_co2_fix        = {co2_ppm}

lverbose        = .false.
ldo_sp_mode     = {plants}
disp_planttrait = .true.
plant_model     = {plants}
microbial_model = .true.
soichem_model   = .false.

start_date   = '{year0}{month:02d}{day:02d}000000'
grid_mode    = 3
continue_run = .false.
forc_periods = {year0},{year1},1

hist_fincl1 = {history}

NPXS=30,30,30
NPYS=10,10,10
NCYC_LITR=30
NCYC_SNOW=20
hist_mfilt  = 24                      ! one file per day: EcoSIM does not close a file at a year change, so a file that
hist_nhtfrq = 1                       ! spans one loses its record count and cannot be read
/

&bbgcforc
/

&Microbes
/

&ecosim_time
rest_opt    = 'never'
rest_frq    = 1
delta_time  = 3600.
stop_n      = {ndays}                 ! days counted from 1 January of the start year (start day of year + run length)
stop_option = 'ndays'
diag_frq    = 1
diag_opt    = 'nsteps'
/
"""

_MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def calendar_date(doy):
    """(month, day) of a 0-based day of a 365-day year"""
    for m, nd in enumerate(_MONTH_DAYS, start=1):
        if doy < nd:
            return m, doy + 1
        doy -= nd
    raise ValueError("day of year out of range")


def write_namelist(path, pl, pft_name, ghg_name, history):
    p = pl["p"]
    plants = pl["veg"] is not None
    if not plants:
        history = [h for h in history if h not in PLANT_FIELDS]
    month, day = calendar_date(p["start_doy"])
    Path(path).write_text(NAMELIST.format(
        name=p["name"], pft=pft_name, ghg=ghg_name, co2_ppm=p["atmosphere"].get("CO2", 4.15e-4) * 1e6,
        phenology_file="sp_phenol_in       = '../sp_phenol.nc'" if plants else "! no sp_phenol_in: no plants",
        plants=".true." if plants else ".false.", year0=pl["years"][0], year1=pl["years"][-1], month=month, day=day,
        history=",".join(f"'{h}'" for h in history), nhours=int(p["ndays"]) * 24,
        ndays=int(p["ndays"]) + p["start_doy"]))          # the standalone counts days from 1 January of the start year


def set_mesh_file(root, mesh_path, ats_run):
    """the subsurface mesh is read from an exodus file instead of generated"""
    dom = plist(root, "mesh", "domain")
    for c in list(dom):
        if c.get("name") in ("mesh type", "generate mesh parameters", "build columns from set"):
            dom.remove(c)
    rm = ET.Element("ParameterList", name="read mesh file parameters", type="ParameterList")
    ET.SubElement(rm, "Parameter", name="file", type="string", value=os.path.relpath(mesh_path, ats_run))
    ET.SubElement(rm, "Parameter", name="format", type="string", value="Exodus II")
    dom.insert(0, rm)
    dom.insert(0, ET.Element("Parameter", name="mesh type", type="string", value="read mesh file"))


def write_ats_variant(pl, pair_dir, pft_name, xml_dir, mesh_path=None):
    """the input with its data files and the PFT file relative to the ATS run directory"""
    ats_run = pair_dir / "ats"
    text = Path(pl["p"]["xml"]).read_text()
    tree = parse_xml(text)
    root = tree.getroot()
    for node in root.iter("Parameter"):
        name, value = node.get("name"), node.get("value")
        if name == "engine input file":
            node.set("value", os.path.relpath(pair_dir / pft_name, ats_run))
        elif name == "file" and value and not os.path.isabs(value):
            node.set("value", os.path.relpath(resolve_data(xml_dir, value), ats_run))
    notes = []
    eco0 = plist(root, "PKs", "EcoSIM for ATS")

    def set_param(node, name, type_, value):
        found = [c for c in node if c.tag == "Parameter" and c.get("name") == name]
        if found:
            found[0].set("value", value)
        else:
            node.insert(len(list(node.findall("Parameter"))), ET.Element("Parameter", name=name, type=type_, value=value))

    b = pl.get("build", {})
    if b.get("semi_implicit"):
        # this ATS exchanges the surface heat semi-implicitly: the energy PK must take the linearized source
        try:
            se = plist(root, "PKs", "surface energy")
            coupled = param(se, "source term").get("value").lower() == "true"
        except KeyError:
            se, coupled = None, False
        if coupled:
            key = param(se, "source key")
            if key.get("value") == "surface-ecosim_source":
                key.set("value", "surface-ecosim_source_linearized")
                notes.append("the surface energy source is surface-ecosim_source_linearized")
            set_param(eco0, "semi-implicit surface heat exchange", "bool", "true")
        else:
            set_param(eco0, "semi-implicit surface heat exchange", "bool", "false")      # no energy source to linearize
            notes.append("semi-implicit surface heat exchange is off (no surface energy source)")
    if b.get("n_heter") and pl["p"]["n_heter"] != int(param(eco0, "number of heterotrophic guilds").get("value")):
        set_param(eco0, "number of heterotrophic guilds", "int", str(pl["p"]["n_heter"]))
        notes.append(f"{pl['p']['n_heter']} heterotrophic guilds")
    if pl["p"].get("layers_raised"):
        gen = plist(root, "mesh", "domain", "generate mesh parameters")
        param(gen, "number of cells").set("value", "{1, 1, %d}" % pl["p"]["ncells"])
        notes.append(f"{pl['p']['ncells']} soil layers (the input has {pl['p']['input_ncells']}; this build's snow code needs 5)")
    if pl["p"].get("calendar_shifted"):
        for c in [c for c in eco0 if c.get("name") == "starting day of year [0-364]"]:
            eco0.remove(c)
        set_param(eco0, "starting day of year [0-364]", "int", str(pl["p"]["start_doy"]))
        notes.append(f"EcoSIM's calendar starts on day {pl['p']['start_doy']} (the input: {pl['p']['input_start_doy']})")
    if pl["p"].get("phenology_dropped"):
        param(plist(root, "PKs", "EcoSIM for ATS"), "prescribe phenology").set("value", "false")
    if mesh_path is not None:                               # after the data files: its path is already relative to the run
        set_mesh_file(root, mesh_path, ats_run)
    eco = plist(root, "PKs", "EcoSIM for ATS")
    if not [c for c in eco if c.get("name") == "visualize ecosim state"]:         # the biomass the report reads
        eco.insert(len(list(eco.findall("Parameter"))),
                   ET.Element("Parameter", name="visualize ecosim state", type="Array(string)",
                              value="{heter_biomass, autor_biomass}"))
    for name in ("domain", "surface domain"):
        vis = None
        try:
            vis = plist(root, "visualization", name)
        except KeyError:
            pass
        if vis is not None:
            param(vis, "times start period stop").set("value", "{0.0, 86400.0, -1.0}")
    mesh_note = (f"the column is regraded to {pl['p']['ncells']} layers (the standalone holds {STANDALONE_SLOTS} layer slots), "
                 if mesh_path is not None else "")
    if pl["p"].get("phenology_dropped"):
        mesh_note += "prescribed phenology is turned off (the standalone has no matching input), "
    if notes:
        mesh_note += "build migration: " + "; ".join(notes) + ", "
    header = (f"<!--\nATS-EcoSIM input of {pl['p']['name']} for the matched pair. Generated by tools/ats_pairs.py pair\n"
              f"from {Path(pl['p']['xml']).name}: {mesh_note}data files and the PFT file are relative to this run directory,\n"
              "daily visualization. Nothing else is changed. Do not edit.\n-->\n\n")
    ET.indent(root, space="  ")
    return header + ET.tostring(root, encoding="unicode") + "\n"


def write_pair(case, pl, cfg, ecosim, ats_src=None):
    p, s = pl["p"], pl["s"]
    pair = case.pair_dir(p["name"])
    (pair / "ats").mkdir(parents=True, exist_ok=True)
    (pair / "standalone").mkdir(parents=True, exist_ok=True)
    # the output of an earlier run of this pair is of another calendar or build: a mix of its history files with the new
    # ones would be read as one run
    stale = [f for f in (pair / "standalone").iterdir() if f.suffix == ".nc" or f.name in ("rpointer.esim", "out.log", "status.txt")]
    for f in stale:
        try:
            f.unlink()
        except OSError as err:
            print(f"warning: could not remove {f} ({err}); remove the standalone output by hand before running")
    pft_name, ghg_name = pl["pft_file"].name, pl["ghg"].name
    if len(pft_name) + 3 > 128 or len(ghg_name) + 3 > 128:
        raise CaseError("file names must stay under 128 characters (EcoSIM truncates them)")
    shutil.copy(pl["pft_file"], pair / pft_name)
    shutil.copy(pl["ghg"], pair / ghg_name)
    write_grid(pair / "grid.nc", pl, cfg, ecosim / cfg["grid_template"])
    write_climate(pair / "clim.nc", pl, ecosim / cfg["climate_template"], p["ndays"], cfg["xradh_factor"])
    veg = pl["veg"]
    if veg:
        pairs.write_sp_phenol(pair / "sp_phenol.nc", veg["code"], veg["lai"], veg["sai"], veg["height"],
                              veg["population"], pl["years"][0])
    history = [cfg.get("acetate_field", "acetate_vr") if h == "acetate_vr" else h for h in cfg["history_fields"]]
    write_namelist(pair / "standalone.namelist", pl, pft_name, ghg_name, history)
    mesh = None
    if pl["regraded"]:
        mesh = pair / "column.exo"
        pairs.write_mesh(pl["dz"], p["depth"], p["ncells"], mesh, ats_src or render.default_ats_src())
    (pair / "ats_match.xml").write_text(write_ats_variant(pl, pair, pft_name, Path(p["xml"]).parent, mesh))
    corgc, _, _ = layer_values(p, cfg)
    meta = dict(
        name=p["name"], kind="microbe", bare=veg is None, group=veg["group"] if veg else None,
        pft=veg["code"] if veg else None, row=veg["row"] if veg else None, lai=veg["lai"] if veg else 0.0,
        sai=veg["sai"] if veg else 0.0, population=veg["population"] if veg else None,
        population_assumed=veg["assumed"] if veg else None, ndays=p["ndays"], years=pl["years"],
        start_doy=p["start_doy"], start_year=p["start_year"], layers=p["ncells"], depth=p["depth"], area=p["area"],
        dz=[float(x) for x in pl["dz"]], porosity=p["porosity"], T0=p["T0"], bottom_T=p["bottom_T"],
        head=p["head"], pressure=p["pressure"], theta0=[float(x) for x in s["theta0"]],
        components=p["components"], initial_mole_fractions=p["initial_mole_fractions"],
        n_heter=p["n_heter"], n_autor=p["n_autor"], atmosphere=p["atmosphere"],
        pft_file=str(pl["pft_file"]), pft_file_named=p["engine_file"], forcing={k: list(v[:4]) if v[0] == "table" else list(v)
                                                                               for k, v in p["forcing"].items()},
        corgc_layer1=float(corgc[0]), source_xml=Path(p["xml"]).name, regraded=pl["regraded"],
        build=pl["build"].get("name"), layers_raised=bool(p.get("layers_raised")),
        calendar_shifted=bool(p.get("calendar_shifted")), input_start_doy=p.get("input_start_doy", p["start_doy"]),
        thermal_neutral=not p["surface_energy_coupled"], phenology_dropped=bool(p.get("phenology_dropped")),
        input_lai=p.get("lai"), input_sai=p.get("sai"), input_vegetation_type=p.get("vegetation_type"),
        input_layers=p.get("input_ncells", p["ncells"]),
    )
    (pair / "pair.json").write_text(json.dumps(meta, indent=1) + "\n")
    return pair


def write_readme(path, plans, cfg):
    rows = "\n".join(
        f"| {pl['p']['name']} | {pl['p']['ncells']} x {pl['dz'][0]:g} m | {pl['p']['ndays']:g} | "
        f"{pl['veg']['code'] + ' (LAI ' + format(pl['veg']['lai'], 'g') + ')' if pl['veg'] else 'none'} | "
        f"{len(pl['p']['components'])} | {pl['p']['T0']:g} | {pl['p']['start_doy']} / {pl['p']['start_year']} |"
        for pl in plans)
    first = plans[0]
    p0 = first["p"]
    calendar_text = f"both models start on day {p0['start_doy']} of the year {p0['start_year']}; 365-day years"
    if p0.get("calendar_shifted"):
        calendar_text = (f"both models start on day {p0['start_doy']} of the year {p0['start_year']}; 365-day years (the input's "
                         f"`starting day of year` is {p0['input_start_doy']}: the merged standalone stops on its energy "
                         "check when it starts mid-year, and the input forcing starts on 1 January)")
    text = f"""# Matched pairs: ATS-EcoSIM and standalone EcoSIM, microbe cases

Generated by `tools/ats_pairs.py pair`; do not edit by hand. Each pair is in `pairs/<test>/`.

## Running (per test `<name>`)
    ats --xml_file=../ats_match.xml                  # in pairs/<name>/ats/
    ecosim.f90.x ../standalone.namelist              # in pairs/<name>/standalone/

| test | column | days | plants | transported species | start T [K] | start day of year / year |
|---|---|---|---|---|---|---|
{rows}

## Matched
| item | how |
|---|---|
| column | the layers of the ATS mesh, 1 m2. The standalone fixes 20 layer slots (`JZ = 20`, GridMod.F90), so an input with more than 19 layers is regraded in BOTH models (graded column, top layer {cfg['top_dz']:g} m); an input with up to 19 layers keeps its own mesh |
| soil | porosity from the ATS bulk density with EcoSIM's particle density {cfg['particle_density']:g} Mg m-3; field capacity and wilting point from the ATS van Genuchten curve; Ksat from the ATS permeability |
| initial water | the ATS initial state (hydrostatic head, or saturated for a constant pressure) |
| organic matter | `a_CORGC = 20 exp(-0.3 L)` of the coupled start-up (L = layer index from the surface), N = C/12, P = C/150; litter seeded as 5 % woody, 10 % fine and 2 % manure of it; no surface litter |
| inorganic nutrients | NH4, NO3 and phosphate of the ATS initial mole fractions are written to the grid (`CNH4`, `CNO3`, `CPO4`) as soil concentrations, but see Not matched: the fork's standalone ignores them with the microbial model on |
| calendar | {calendar_text} |
| forcing | the value ATS sees at the start of every hour (a constant, or the daily table interpolated linearly) in the standalone hourly climate |
| surface energy | where ATS couples no surface energy to EcoSIM (the box: `source term` false) the standalone gets near-neutral forcing: air saturated at the air temperature and EcoSIM's own sky-longwave estimate, so its energy balance changes the soil temperature little (the box soil stays within about 2 K of ATS's 298 K); otherwise the input's forcing |
| atmosphere | O2, N2, CO2, CH4, N2O, NH3 of the EcoSIM PK in the grid file; CO2 also as `atm_co2_fix` |
| model | microbial model on in both; prescribed phenology (`ldo_sp_mode`) where the input has it; EcoSIM time step 1 h in both |

## Not matched
- **Solute transport:** ATS transports the 13 or 17 species with its own advection and diffusion between cells and the atmosphere boundary; the standalone solves its own diffusion, dissolution and advection of the dissolved and gaseous species. The comparison of the two is the point of the report.
- **Mineral nitrogen and phosphorus at start:** with the microbial model on, the fork's standalone ignores the grid's `CNH4`, `CNO3` and `CPO4` (a seed 20 times larger gave identical output: NH4 and NO3 are zero from the first sample), so its mineral N and P pools start at zero, whereas ATS starts from the input's mole fractions.
- **Dissolved organic matter and gases at start:** ATS initialises DOC, DON, DOP, acetate and the gases in the water from the input's mole fractions every step it couples; the standalone derives its own initial dissolved organic pools from the organic matter, and its gas concentrations from the atmosphere.
- **Root-turnover litter recharge:** the coupling adds litter from prescribed roots when the microbial model and phenology are on; the standalone has no such term.
- **Microbial biomass by functional group:** the fork's standalone history gives total microbial C, N and P per layer; the ATS side gives all groups, pools and complexes.
- **Soil water and heat:** EcoSIM's own solver and soil thermal properties in the standalone, ATS Richards and the three-phase thermal model in the coupled run; the coupled run replaces EcoSIM's soil state every hour.
- **Plants:** where the input has prescribed phenology the pair is plant-free in both models (the ATS variant sets `prescribe phenology` false). The fork's standalone has no prescribed-phenology input (`ldo_sp_mode` uses fixed monthly LAI and SAI), and the merged standalone's driver turns its microbial model off whenever prescribed phenology is on (`EcoSIMAPI.F90`), so a canopy and microbes cannot be matched in it; with plants, ATS would also add root-turnover litter that the standalone lacks.
- **PFT file:** `{first['pft_file'].name}` replaces the input's own `{Path(first['p']['engine_file']).name}` when that file is not available.
"""
    Path(path).write_text(text)


def generate_pairs(case, options=None, ecosim_src=None, only=None, dry_run=False, ats_src=None):
    options = dict(options or {})
    ecosim = Path(ecosim_src or render.default_ecosim_src()).resolve()
    build = detect_build(ecosim, ats_src, options.pop("build", "auto"))
    cfg = {**MICROBE_OPTIONS, "history_fields": HISTORY_FIELDS, **build, **options}
    print(f"build features ({build['name']}): phenology input {build['sp_phenol_input']}, XRADH factor {build['xradh_factor']}, "
          f"semi-implicit heat {build['semi_implicit']}, heterotroph guilds {build['n_heter'] or 'from the input'}, "
          f"minimum soil layers {build['min_layers'] or 'none'}")
    names = case.xml_names()
    if only:
        unknown = sorted(set(only) - set(names))
        if unknown:
            raise CaseError(f"unknown tests {unknown}; {case.root} has {names}")
        names = [n for n in names if n in only]
    print(f"case {case.name}: {len(names)} microbe inputs; EcoSIM source {ecosim}")
    plans = []
    for name in names:
        pl = plan(case.root / f"{name}.xml", case.root, cfg, ecosim)
        plans.append(pl)
        p = pl["p"]
        what = (f"{pl['veg']['code']} LAI {pl['veg']['lai']:g}" if pl["veg"] else
                ("no plants (the input's prescribed phenology is dropped in both models)" if p.get("phenology_dropped")
                 else "no plants"))
        layers = (f"{p['ncells']} graded layers ({pl['dz'][0]:g}..{pl['dz'][-1]:g} m; the input has {p['input_ncells']})"
                  if pl["regraded"] else f"{p['ncells']} layers of {pl['dz'][0]:g} m")
        print(f"  {name:<44} {layers}, {p['ndays']:g} d, {what}, "
              f"{len(p['components'])} species, start day {p['start_doy']}/{p['start_year']}")
        if Path(pl["p"]["engine_file"]).name != pl["pft_file"].name:
            print(f"    note: the input's PFT file {Path(p['engine_file']).name} is not available; using {pl['pft_file'].name}")
    if dry_run:
        print("dry run: nothing written")
        return plans
    for pl in plans:
        write_pair(case, pl, cfg, ecosim, ats_src)
        print("wrote", case.pair_dir(pl["p"]["name"]))
    if not only:
        write_readme(case.pairs_dir / "README.md", plans, cfg)
        print("wrote", case.pairs_dir / "README.md")
    return plans
