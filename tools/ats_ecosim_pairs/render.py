"""Read what the matched standalone case needs out of an ATS-EcoSIM input.

Standard library only, so it also runs where the model executables are (the Docker container).
"""

import re
from pathlib import Path

from . import xmledit
from .case import CaseError

WORKSPACE = Path(__file__).resolve().parents[2]          # the ats-helper checkout


def default_ecosim_src():
    """the EcoSIM source tree to take the PFT file and templates from: the development checkout if there is one"""
    for sub in ("dev/ecosim", "ecosim"):
        path = WORKSPACE / "repos" / sub
        if (path / "input_data").is_dir():
            return path
    return WORKSPACE / "repos" / "ecosim"


def default_ats_src():
    """the ATS source tree (for its meshing tools)"""
    for sub in ("dev/ats", "amanzi/src/physics/ats"):
        path = WORKSPACE / "repos" / sub
        if (path / "tools" / "meshing").is_dir():
            return path
    return WORKSPACE / "repos" / "amanzi" / "src" / "physics" / "ats"


def end_time_days(text):
    """the cycle driver's end time [d] of an input (its text)"""
    value = re.search(r'name="end time" type="double" value="([0-9.eE+-]+)"', text)
    units = re.search(r'name="end time units" type="string" value="(\w+)"', text)
    if not value:
        raise CaseError("no cycle driver end time in the input")
    return float(value.group(1)) * {"d": 1.0, "s": 1 / 86400.0, "yr": 365.0}[units.group(1) if units else "s"]


def ats_parameters(root, xml_dir):
    """soil, run, canopy and file parameters of an ATS-EcoSIM input (an ElementTree root).

    xml_dir is the folder file names in the input are relative to. Raises CaseError, naming what is
    missing, for an input that is not a single column of the kind the matched pair can reproduce."""
    P, plist, param = xmledit, xmledit.plist, xmledit.param

    def need(what, fn):
        try:
            return fn()
        except (KeyError, ValueError, AttributeError, IndexError, TypeError) as err:
            raise CaseError(f"cannot read {what} from the input ({err}); the matched pair supports a single "
                            "generated column with a constant-property soil") from None

    def maybe(fn):
        try:
            return fn()
        except (KeyError, ValueError, AttributeError, IndexError, TypeError):
            return None

    def number(fn):
        found = maybe(fn)
        return None if found is None else float(found.get("value"))

    pks = need("the PKs", lambda: plist(root, "PKs"))
    ev = need("the evaluators", lambda: plist(root, "state", "evaluators"))
    wrm = need("the van Genuchten parameters", lambda: plist(root, "state", "model parameters", "WRM parameters", "mineral"))
    flow_ic = need("the flow initial conditions", lambda: plist(pks, "subsurface flow", "initial conditions"))
    eco = need("the EcoSIM PK", lambda: plist(pks, "EcoSIM for ATS"))
    gravity = need("gravity", lambda: P.array_values(param(plist(root, "state", "initial conditions", "gravity"), "value")))
    gen = need("a generated mesh", lambda: plist(root, "mesh", "domain", "generate mesh parameters"))
    lo = P.array_values(param(gen, "domain low coordinate"))
    hi = P.array_values(param(gen, "domain high coordinate"))
    ncells = P.array_values(param(gen, "number of cells"))
    if len(ncells) != 3 or ncells[0] != 1 or ncells[1] != 1:
        raise CaseError(f"the mesh has {ncells} cells; the matched pair supports a single column (1 x 1 x N)")
    driver = need("the cycle driver", lambda: plist(root, "cycle driver"))
    if param(driver, "end time units").get("value") != "d":
        raise CaseError("the cycle driver's end time units must be d")

    def text(fn):
        found = maybe(fn)
        return None if found is None else found.get("value")

    prescribed = text(lambda: param(eco, "prescribe phenology"))
    forcing = text(lambda: param(plist(eco, "starting year"), "file"))
    engine = text(lambda: param(eco, "engine input file"))
    out = dict(
        depth=hi[2] - lo[2],
        ndays=float(param(driver, "end time").get("value")),
        porosity=need("base_porosity", lambda: float(P.constant_value(plist(ev, "base_porosity")).get("value"))),
        permeability=need("permeability", lambda: float(P.constant_value(plist(ev, "permeability")).get("value"))),
        rock_density=need("density_rock", lambda: float(P.constant_value(plist(ev, "density_rock")).get("value"))),
        vg_alpha=float(param(wrm, "van Genuchten alpha [Pa^-1]").get("value")),
        vg_n=float(param(wrm, "van Genuchten n [-]").get("value")),
        sr=float(param(wrm, "residual saturation [-]").get("value")),
        head=float(param(flow_ic, "hydrostatic head [m]").get("value")),
        rho=float(param(flow_ic, "hydrostatic water density [kg m^-3]").get("value")),
        g=-gravity[2],
        T0=need("the subsurface energy initial temperature",
                lambda: float(param(plist(pks, "subsurface energy", "initial conditions"), "value").get("value"))),
        bottom_T=number(lambda: param(plist(pks, "subsurface energy", "boundary conditions", "temperature", "bottom",
                                            "boundary temperature", "function-constant"), "value")),
        pk_wind_height=number(lambda: param(eco, "wind speed reference height [m]")),
        psi_fc=need("field capacity", lambda: float(param(eco, "field capacity [Mpa]").get("value"))),
        psi_wp=need("wilting point", lambda: float(param(eco, "wilting point [Mpa]").get("value"))),
        prescribed=prescribed is not None and prescribed.lower() == "true",
        lai=maybe(lambda: float(P.constant_value(plist(ev, "surface-LAI")).get("value"))),
        sai=maybe(lambda: float(P.constant_value(plist(ev, "surface-SAI")).get("value"))),
        vegetation_type=maybe(lambda: int(float(P.constant_value(plist(ev, "surface-vegetation_type")).get("value")))),
        forcing=str((Path(xml_dir) / forcing).resolve()) if forcing and not Path(forcing).is_absolute() else forcing,
        engine_file=engine,
        npfts=maybe(lambda: int(param(eco, "number of pfts").get("value"))),
    )
    if out["forcing"] is None:
        raise CaseError("the EcoSIM PK has no `starting year` file: the hourly forcing file cannot be found")
    if engine is None:
        raise CaseError("the EcoSIM PK has no `engine input file` (the PFT parameter file)")
    if out["prescribed"] and out["npfts"] not in (None, 1):
        raise CaseError(f"the input has {out['npfts']} PFTs; the matched pair supports one PFT per column")
    if out["prescribed"] and None in (out["lai"], out["sai"], out["vegetation_type"]):
        raise CaseError("prescribed phenology needs constant `surface-LAI`, `surface-SAI` and "
                        "`surface-vegetation_type` evaluators in the input")
    return out
