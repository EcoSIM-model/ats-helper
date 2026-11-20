#!/usr/bin/env python3
"""Write <case folder>/case.json, the lock of a paired-model case folder, from a finished run of its pairs.

The case pins the three source commits, the checksums of the folder's inputs and data, and the ATS
accepted-cycle count of every pair (from the status.txt that `ats_pairs.py run` wrote). Run this on the host
after the whole folder passed on a build of the sources it names:

    python3 tools/ats_pairs.py all cases/pft_tests --container NAME
    python3 docker/lock_pairs_case.py --case-dir cases/pft_tests \\
        --amanzi https://github.com/jinyun1tang/amanzi.git@<commit> \\
        --ats https://github.com/AndrewGraus/ats.git@<commit> \\
        --ecosim https://github.com/EcoSIM-model/EcoSIM.git@<commit> \\
        --ecosim-src repos/dev/ecosim

--ecosim-src is a checkout of the EcoSIM commit above; its PFT parameter file is checksummed.
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reproduce_case", ROOT / "docker/reproduce_case.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

DESCRIPTION = ("Paired ATS-EcoSIM and standalone EcoSIM runs of the inputs in {case_dir} (bare ground and "
               "prescribed-phenology PFT columns, 729 days of hourly Daymet forcing), each pair written by "
               "tools/ats_pairs.py from its input with the matched start of both models.")
LIMITATIONS = [
    "Pins model sources, their recursive submodules, the case folder's inputs and data, and each pair's ATS "
    "accepted-cycle count. Ubuntu apt packages and transitive build downloads are not fully locked; Docker image "
    "identity and installed packages are recorded per run.",
    "The cycle counts were produced on Ubuntu ARM64 with one MPI rank. Other architectures or toolchains "
    "can change them without the physics being wrong; the comparison report (tools/ats_pairs.py report) "
    "is the way to judge such a run.",
    "ATS is a commit that Amanzi's recorded ATS pointer does not name; the runner fetches it from its own URL.",
]
PAIR_OPTIONS = ("start_temperature", "layers", "top_dz")       # recorded in pair.json by `pair`


def source(spec_text):
    url, _, commit = spec_text.rpartition("@")
    if not url or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SystemExit(f"Expected URL@<40-hex commit>, got {spec_text!r}")
    return {"url": url, "commit": commit}


def status_of(path):
    text = path.read_text().strip() if path.is_file() else "missing"
    if not text.startswith("PASSED"):
        raise SystemExit(f"{path}: {text.split('|')[0].strip()}; every run must have passed")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case-dir", default="cases/pft_tests")
    parser.add_argument("--amanzi", required=True)
    parser.add_argument("--ats", required=True)
    parser.add_argument("--ecosim", required=True)
    parser.add_argument("--ecosim-src", required=True, help="checkout of the EcoSIM commit")
    args = parser.parse_args()

    case_dir = (ROOT / args.case_dir).resolve()
    pairs = case_dir / "pairs"
    names = [p.stem for p in sorted(case_dir.glob("*.xml"))]
    if not names:
        raise SystemExit(f"No inputs in {case_dir}")
    expected, options = {}, {}
    for name in names:
        meta_file = pairs / name / "pair.json"
        if not meta_file.is_file():
            raise SystemExit(f"{meta_file} not found: run `ats_pairs.py pair` and run the pairs first")
        meta = json.loads(meta_file.read_text())
        used = {k: round(meta[k], 6) for k in PAIR_OPTIONS}
        if options and used != options:
            raise SystemExit(f"{name}: pair options {used} differ from the other pairs' {options}")
        options = used
        status_of(pairs / name / "standalone/status.txt")
        match = re.search(r"last cycle (\d+)", status_of(pairs / name / "ats/status.txt"))
        if not match:
            raise SystemExit(f"{name}: no cycle count in ats/status.txt")
        expected[name] = {"ats_cycles": int(match.group(1))}

    pft_name = "input_data/ecosim_pftpar_20261021.nc"
    namelist = (pairs / names[0] / "standalone.namelist").read_text()
    found = re.search(r"pft_file_in\s*=\s*'[^']*/(input_data/[^'/]+)'", namelist)
    if found:
        pft_name = found.group(1)
    pft = Path(args.ecosim_src) / pft_name
    case = {
        "schema_version": 1,
        "name": case_dir.name,
        "kind": "pairs",
        "description": DESCRIPTION.format(case_dir=args.case_dir),
        "sources": {"amanzi": source(args.amanzi),
                    "ats": {**source(args.ats), "override_amanzi_gitlink": True},
                    "ecosim": source(args.ecosim)},
        "options": options,
        "files": {rel.as_posix(): rc.sha256(case_dir / rel) for rel in rc.case_input_files(case_dir)},
        "pft_file": {"path": pft_name, "sha256": rc.sha256(pft)},
        "expected": expected,
        "comparison": {"cycle_tolerance": 0},
        "reference_environment": {"os": "Ubuntu 24.04", "architecture": "aarch64",
                                  "compiler": "GNU C++ / Fortran 13.3", "mpi_ranks": 1},
        "known_limitations": LIMITATIONS,
    }
    out = case_dir / "case.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(case, indent=2) + "\n")
    print(f"wrote {out} ({len(names)} pairs)")


if __name__ == "__main__":
    sys.exit(main())
