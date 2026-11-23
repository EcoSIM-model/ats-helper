"""A case folder: model inputs and the data they refer to; every input is one test.

    cases/<case>/
        <test>.xml            an ATS-EcoSIM input of one column; file names in it are relative to this folder
        <test>.namelist       or an EcoSIM namelist of a (multi-column) landscape; the ATS-EcoSIM input is
                              generated from it. Its file names are relative to this folder
        data/...              forcing and other data the inputs refer to
        pairs/                generated: one matched pair per input (written by `pair`, not committed)

Nothing but inputs and data belongs in the folder; the scripts are in tools/. For every input `pair`
writes pairs/<test>/ (matched standalone EcoSIM case, revised ATS input, metadata); `run`, `summarize`
and `report` read them. Standard library only, so it also runs where the model executables are.
"""

import json
from dataclasses import dataclass
from pathlib import Path

# Settings of the matched standalone case that the ATS input cannot give. Overridden on the command line.
OPTIONS = {
    "layers": 19,                 # graded layers of the matched column (ATS and standalone)
    "top_dz": 0.05,               # thickness of the top layer [m]
    "start_temperature": 274.15,  # K, both models; a number, or "xml" to keep the input's own
    "particle_density": 2.66,     # Mg m-3, EcoSIM derives porosity from bulk density with it
    "soil_albedo": 0.2,
    "wind_height": None,          # None: the EcoSIM PK's "wind speed reference height [m]" of the input
    "longwave": None,             # "ats": give the standalone the sky longwave ATS computes
    "years": [1981, 1982],        # labels of the standalone years (365-day calendar)
    "days": 729,                  # run length [d] of a namelist test (an ATS input gives its own); at most 365 per year
    "history_fields": ["PSI_vr", "VSPore_vr", "rIceFLP_vr", "RootH2OUptake_vr"],
    "grid_template": "examples/inputs/blodgett/Blodget_grid_20251115.nc",
    "climate_template": "examples/inputs/blodgett/Blodget.clim.2012-2022.nc",
    "ghg_file": "input_data/fatm_hist_GHGs_1750-2023.nc",
    "pft_file": "input_data/ecosim_pftpar_20261021.nc",   # standalone PFT file when the input names none (bare ground)
}
BOTTOM_OFFSET = 0.28              # K; EcoSIM's deep source temperature is the initial temperature + 0.28 K


class CaseError(ValueError):
    pass


@dataclass
class Test:
    name: str
    source: Path                  # the ATS-EcoSIM input (kind "xml") or the EcoSIM namelist (kind "namelist")
    group: str = None             # vegetation group of the PFT, from the pair's metadata
    axis: tuple = ("lai", None)
    is_baseline: bool = False     # bare ground: no prescribed phenology
    kind: str = "xml"
    columns: int = 1              # columns of the model (from the pair's metadata)

    @property
    def xml(self):
        return self.source


class Case:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise CaseError(f"case folder not found: {root}")
        self.name = self.root.name
        self.pairs_dir = self.root / "pairs"
        stems = [p.stem for p in sorted(self.root.glob("*.xml")) + sorted(self.root.glob("*.namelist"))]
        if not stems:
            raise CaseError(f"{self.root}: no ATS-EcoSIM input (*.xml) or EcoSIM namelist (*.namelist) in the case folder")
        duplicates = sorted({s for s in stems if stems.count(s) > 1})
        if duplicates:
            raise CaseError(f"{self.root}: {duplicates} exist as both .xml and .namelist; every test needs a unique name")

    @property
    def thresholds(self):
        return {}

    def sources(self):
        """{test name: (input file, kind)} in name order"""
        found = {p.stem: (p, "xml") for p in self.root.glob("*.xml")}
        found.update({p.stem: (p, "namelist") for p in self.root.glob("*.namelist")})
        return dict(sorted(found.items()))

    def xml_names(self):
        return list(self.sources())

    def pair_dir(self, name):
        return self.pairs_dir / name

    def meta(self, name):
        """metadata `pair` wrote for a test, or None before it ran"""
        path = self.pair_dir(name) / "pair.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def tests(self, only=None, baseline=True, kind=None):
        """the tests in file-name order; vegetation group, LAI and bare-ground flag come from the pairs"""
        sources = self.sources()
        names = list(sources)
        if only:
            unknown = sorted(set(only) - set(names))
            if unknown:
                raise CaseError(f"unknown tests {unknown}; {self.root} has {names}")
            names = [n for n in names if n in only]
        out = []
        for name in names:
            meta = self.meta(name)
            bare = bool(meta and meta["bare"])
            if bare and not baseline:
                continue
            path, test_kind = sources[name]
            if kind and test_kind != kind:
                continue
            out.append(Test(name, path, meta["group"] if meta else None,
                            ("lai", meta["lai"] if meta else None), bare, test_kind, meta.get("columns", 1) if meta else 1))
        return out


def load(path):
    return Case(path)
