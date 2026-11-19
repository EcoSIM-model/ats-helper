"""Suite manifest: what is specific to one suite of ATS-EcoSIM tests.

A manifest is a JSON file next to the suite's templates and data (see cases/pft_tests/suite.json
and tools/README.md). Unknown keys are errors, so a misspelled key is never ignored silently.

Sections:
  name, description
  ats         template      ATS input every test is made from (path relative to the manifest)
              baseline      {"name", "template"}: an extra test that is the template as it is
              forcing       hourly forcing file (HDF5) shared by the tests
              data_dir      directory the inputs refer to as "<data_prefix>" (default "../data/")
  name_pattern              test name, filled from the sweep entry, for example "pft_{group}_lai{lai}"
  sweep       list of entries with a "group" and any other fields; a list-valued field expands into
              one test per value (several list-valued fields give all combinations)
  xml_edits   edits applied to the template for every sweep test (see xmledit.py)
  profiles    named edit lists applied on request (--profile NAME), for example the settings of a build
  standalone  settings of the matched standalone EcoSIM case (years, layers, constants, ...)
  compare     thresholds ("quantity": [green, amber]) overriding the defaults of the report
"""

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

TOP_KEYS = {"name", "description", "ats", "name_pattern", "sweep", "xml_edits", "profiles", "standalone", "compare"}
ATS_KEYS = {"template", "baseline", "forcing", "data_dir", "data_prefix"}
STANDALONE_KEYS = {"pft_file", "years", "layers", "top_dz", "particle_density", "soil_albedo", "wind_height",
                   "history_fields", "grid_template", "climate_template", "ghg_file", "longwave"}
COMPARE_KEYS = {"thresholds"}
STANDALONE_DEFAULTS = {
    "years": [1981, 1982], "layers": 19, "top_dz": 0.05, "particle_density": 2.66, "soil_albedo": 0.2,
    "wind_height": None,        # None: the EcoSIM PK's "wind speed reference height [m]" of the input (default 3.0)
    "history_fields": ["PSI_vr", "VSPore_vr", "rIceFLP_vr", "RootH2OUptake_vr"],
    "grid_template": "examples/inputs/blodgett/Blodget_grid_20251115.nc",
    "climate_template": "examples/inputs/blodgett/Blodget.clim.2012-2022.nc",
    "ghg_file": "input_data/fatm_hist_GHGs_1750-2023.nc",
    "longwave": None,           # "ats": give the standalone the sky longwave ATS computes (clear sky, from T and vapor pressure)
}


class ManifestError(ValueError):
    pass


def tag(value):
    """a number in a test name: 1 -> '1', 0.5 -> '0p5'"""
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return str(value).replace(".", "p")


@dataclass
class Test:
    name: str
    group: str
    params: dict
    axis: tuple = (None, None)          # (field name, value) of the first expanded field
    is_baseline: bool = False


@dataclass
class Manifest:
    path: Path
    data: dict
    root: Path = field(init=False)

    def __post_init__(self):
        self.root = self.path.parent

    # -- sections ------------------------------------------------------------------------------
    @property
    def name(self):
        return self.data["name"]

    @property
    def ats(self):
        return self.data["ats"]

    @property
    def xml_edits(self):
        return self.data.get("xml_edits", [])

    @property
    def profiles(self):
        return self.data.get("profiles", {})

    @property
    def standalone(self):
        return {**STANDALONE_DEFAULTS, **self.data.get("standalone", {})}

    @property
    def thresholds(self):
        return self.data.get("compare", {}).get("thresholds", {})

    @property
    def forcing(self):
        return self.root / self.ats["forcing"]

    @property
    def data_dir(self):
        return self.root / self.ats["data_dir"]

    @property
    def data_prefix(self):
        return self.ats.get("data_prefix", "../data/")

    @property
    def inputs_dir(self):
        return self.root / "input"

    @property
    def pairs_dir(self):
        return self.root / "pairs"

    def template(self, test):
        rel = self.ats["baseline"]["template"] if test.is_baseline else self.ats["template"]
        return self.root / rel

    def profile_edits(self, profile):
        if profile is None:
            return []
        if profile not in self.profiles:
            raise ManifestError(f"{self.path}: no profile {profile!r}; defined: {sorted(self.profiles)}")
        return self.profiles[profile]

    # -- tests ---------------------------------------------------------------------------------
    def tests(self, only=None, baseline=True):
        """the tests in manifest order: the sweep (expanded), then the baseline"""
        out = []
        for entry in self.data.get("sweep", []):
            axes = [k for k, v in entry.items() if isinstance(v, list)]
            for combo in itertools.product(*[entry[k] for k in axes]):
                params = {**entry, **dict(zip(axes, combo))}
                name = self.data["name_pattern"].format(
                    **{k: (tag(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                       for k, v in params.items()})
                out.append(Test(name, params["group"], params, (axes[0], combo[0]) if axes else (None, None)))
        base = self.ats.get("baseline")
        if baseline and base:
            out.append(Test(base["name"], base["name"], {"group": base["name"]}, is_baseline=True))
        names = [t.name for t in out]
        if len(set(names)) != len(names):
            raise ManifestError(f"{self.path}: test names are not unique: {sorted(n for n in names if names.count(n) > 1)}")
        if only:
            unknown = sorted(set(only) - set(names))
            if unknown:
                raise ManifestError(f"unknown tests {unknown}; the suite has {names}")
            out = [t for t in out if t.name in only]
        return out


def _check(d, allowed, where):
    unknown = sorted(set(d) - set(allowed))
    if unknown:
        raise ManifestError(f"{where}: unknown keys {unknown}; allowed: {sorted(allowed)}")


def load(path):
    path = Path(path).resolve()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as err:
        raise ManifestError(f"cannot read manifest {path}: {err}")
    _check(data, TOP_KEYS, str(path))
    for key in ("name", "ats", "name_pattern", "sweep"):
        if key not in data:
            raise ManifestError(f"{path}: missing required key {key!r}")
    _check(data["ats"], ATS_KEYS, f"{path}: ats")
    for key in ("template", "forcing", "data_dir"):
        if key not in data["ats"]:
            raise ManifestError(f"{path}: ats.{key} is required")
    if "baseline" in data["ats"]:
        _check(data["ats"]["baseline"], {"name", "template"}, f"{path}: ats.baseline")
    _check(data.get("standalone", {}), STANDALONE_KEYS, f"{path}: standalone")
    _check(data.get("compare", {}), COMPARE_KEYS, f"{path}: compare")
    for i, entry in enumerate(data["sweep"]):
        if "group" not in entry:
            raise ManifestError(f"{path}: sweep entry {i} has no 'group'")
    m = Manifest(path, data)
    for rel in (data["ats"]["template"], (data["ats"].get("baseline") or {}).get("template")):
        if rel and not (m.root / rel).is_file():
            raise ManifestError(f"{path}: template not found: {m.root / rel}")
    m.tests()                                   # validates the name pattern and uniqueness
    return m
