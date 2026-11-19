"""Render the ATS inputs of a suite from its manifest, and read parameters out of a rendered input.

Standard library only, so it also runs where the model executables are (the Docker container).
"""

import os
import re
from pathlib import Path

from . import xmledit
from .manifest import ManifestError

WORKSPACE = Path(__file__).resolve().parents[2]          # the ats-helper checkout
DEFAULT_ECOSIM_SRC = WORKSPACE / "repos" / "dev" / "ecosim"


def pft_file_path(m, ecosim_src):
    """the PFT parameter file inside the EcoSIM source tree, or None if the suite has none"""
    rel = m.standalone.get("pft_file")
    if not rel:
        return None
    path = Path(ecosim_src).resolve() / rel
    if not path.is_file():
        raise FileNotFoundError(f"PFT parameter file not found: {path} (see --ecosim-src)")
    return path


def render_test(m, test, run_dir, ecosim_src=DEFAULT_ECOSIM_SRC, profile=None):
    """The ATS input of a test as text, for a run directory.

    The template with the suite's edits for this test (the baseline is the template as it is), the
    template's references to the data directory rewritten relative to the run directory, then the
    edits of the profile, if any.
    """
    text = m.template(test).read_text()
    variables = {**test.params, "name": test.name}
    pft = pft_file_path(m, ecosim_src)
    if pft is not None:
        variables["pft_file"] = str(pft)
    if not test.is_baseline:
        text = xmledit.edit_text(text, m.xml_edits, variables, run_dir)
    rel = os.path.relpath(m.data_dir, run_dir)
    text = text.replace('"' + m.data_prefix, '"' + rel + "/")
    return xmledit.edit_text(text, m.profile_edits(profile), variables, run_dir)


def end_time_days(text):
    """the cycle driver's end time [d] of an input (its text)"""
    value = re.search(r'name="end time" type="double" value="([0-9.eE+-]+)"', text)
    units = re.search(r'name="end time units" type="string" value="(\w+)"', text)
    if not value:
        raise ManifestError("no cycle driver end time in the input")
    return float(value.group(1)) * {"d": 1.0, "s": 1 / 86400.0, "yr": 365.0}[units.group(1) if units else "s"]


def generate_inputs(m, ecosim_src=DEFAULT_ECOSIM_SRC, profile=None, only=None, out_dir=None, dry_run=False):
    """write <out_dir>/<test>.xml for every test (default: the suite's input/ directory)"""
    out_dir = Path(out_dir) if out_dir else m.inputs_dir
    written = []
    for test in m.tests(only):
        text = render_test(m, test, m.root / test.name, ecosim_src, profile)
        path = out_dir / f"{test.name}.xml"
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        what = "baseline, " + m.ats["baseline"]["template"] if test.is_baseline else \
            ", ".join(f"{k}={v}" for k, v in test.params.items() if k not in ("population", "population_assumed"))
        print(f"  {path.name}  ({what})")
        written.append((test, path))
    return written


def ats_parameters(root):
    """soil, run and canopy parameters of a rendered ATS input (an ElementTree root) for the standalone case"""
    P, plist, param = xmledit, xmledit.plist, xmledit.param
    pks = plist(root, "PKs")
    ev = plist(root, "state", "evaluators")
    wrm = plist(root, "state", "model parameters", "WRM parameters", "mineral")
    flow_ic = plist(pks, "subsurface flow", "initial conditions")
    eco = plist(pks, "EcoSIM for ATS")
    gravity = P.array_values(param(plist(root, "state", "initial conditions", "gravity"), "value"))
    gen = plist(root, "mesh", "domain", "generate mesh parameters")
    lo = P.array_values(param(gen, "domain low coordinate"))
    hi = P.array_values(param(gen, "domain high coordinate"))
    driver = plist(root, "cycle driver")
    assert param(driver, "end time units").get("value") == "d"
    try:                                                    # fixed temperature at the bottom of the column
        bottom_T = float(param(plist(pks, "subsurface energy", "boundary conditions", "temperature", "bottom",
                                     "boundary temperature", "function-constant"), "value").get("value"))
    except KeyError:
        bottom_T = None
    try:                                                    # the PK's own default is 3 m
        wind = float(param(eco, "wind speed reference height [m]").get("value"))
    except KeyError:
        wind = None
    return dict(
        depth=hi[2] - lo[2],
        ndays=float(param(driver, "end time").get("value")),
        porosity=float(P.constant_value(plist(ev, "base_porosity")).get("value")),
        permeability=float(P.constant_value(plist(ev, "permeability")).get("value")),
        rock_density=float(P.constant_value(plist(ev, "density_rock")).get("value")),
        vg_alpha=float(param(wrm, "van Genuchten alpha [Pa^-1]").get("value")),
        vg_n=float(param(wrm, "van Genuchten n [-]").get("value")),
        sr=float(param(wrm, "residual saturation [-]").get("value")),
        head=float(param(flow_ic, "hydrostatic head [m]").get("value")),
        rho=float(param(flow_ic, "hydrostatic water density [kg m^-3]").get("value")),
        g=-gravity[2],
        T0=float(param(plist(pks, "subsurface energy", "initial conditions"), "value").get("value")),
        bottom_T=bottom_T,
        pk_wind_height=wind,
        psi_fc=float(param(eco, "field capacity [Mpa]").get("value")),
        psi_wp=float(param(eco, "wilting point [Mpa]").get("value")),
        lai=float(P.constant_value(plist(ev, "surface-LAI")).get("value")),
        sai=float(P.constant_value(plist(ev, "surface-SAI")).get("value")),
    )
