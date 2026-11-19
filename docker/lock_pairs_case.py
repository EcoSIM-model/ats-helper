#!/usr/bin/env python3
"""Write cases/pairs/case.json, the lock of a paired-model suite, from a finished run of that suite.

The case pins the three source commits, the checksums of the suite's inputs and the ATS accepted-cycle
count of every pair (from the status.txt that tools/ats_pairs.py run wrote). Run this on the host after
the whole suite passed on the build the sources describe:

    python3 tools/ats_pairs.py run cases/pft_tests/suite.json standalone  --container NAME
    python3 tools/ats_pairs.py run cases/pft_tests/suite.json ats-matched --container NAME
    python3 docker/lock_pairs_case.py \\
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

DESCRIPTION = ("Paired ATS-EcoSIM and standalone EcoSIM runs of the {suite} suite (bare ground and "
               "prescribed-phenology PFT columns, 729 days of hourly Daymet forcing), with the matched start "
               "of both models (profile {profile}).")
LIMITATIONS = [
    "Pins model sources, their recursive submodules, the suite's inputs and each pair's ATS accepted-cycle "
    "count. Ubuntu apt packages and transitive build downloads are not fully locked; Docker image identity "
    "and installed packages are recorded per run.",
    "The cycle counts were produced on Ubuntu ARM64 with one MPI rank. Other architectures or toolchains "
    "can change them without the physics being wrong; the comparison report (tools/ats_pairs.py report) "
    "is the way to judge such a run.",
    "ATS is a commit that Amanzi's recorded ATS pointer does not name; the runner fetches it from its own URL.",
]


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
    parser.add_argument("--suite", default="cases/pft_tests/suite.json")
    parser.add_argument("--profile", default="matched-start")
    parser.add_argument("--amanzi", required=True)
    parser.add_argument("--ats", required=True)
    parser.add_argument("--ecosim", required=True)
    parser.add_argument("--ecosim-src", required=True, help="checkout of the EcoSIM commit")
    parser.add_argument("--out", default="cases/pairs/case.json")
    args = parser.parse_args()

    suite = (ROOT / args.suite).resolve()
    pairs = suite.parent / "pairs"
    tests = sorted(p.name for p in pairs.iterdir() if (p / "ats_match.xml").is_file())
    if not tests:
        raise SystemExit(f"No generated pairs under {pairs}")
    expected = {}
    for name in tests:
        status_of(pairs / name / "standalone/status.txt")
        match = re.search(r"last cycle (\d+)", status_of(pairs / name / "ats/status.txt"))
        if not match:
            raise SystemExit(f"{name}: no cycle count in ats/status.txt")
        expected[name] = {"ats_cycles": int(match.group(1))}

    files = [suite, *sorted(p for sub in ("data", "templates") for p in (suite.parent / sub).glob("*") if p.is_file())]
    pft_name = json.loads(suite.read_text())["standalone"]["pft_file"]
    pft = Path(args.ecosim_src) / pft_name
    case = {
        "schema_version": 1,
        "name": "pairs",
        "kind": "pairs",
        "description": DESCRIPTION.format(suite=suite.parent.name, profile=args.profile),
        "sources": {"amanzi": source(args.amanzi),
                    "ats": {**source(args.ats), "override_amanzi_gitlink": True},
                    "ecosim": source(args.ecosim)},
        "suite": suite.relative_to(ROOT).as_posix(),
        "profile": args.profile,
        "files": {p.relative_to(ROOT).as_posix(): rc.sha256(p) for p in files},
        "pair_inputs_sha256": rc.pair_inputs_sha256(pairs),
        "pft_file": {"path": pft_name, "sha256": rc.sha256(pft)},
        "expected": expected,
        "comparison": {"cycle_tolerance": 0},
        "reference_environment": {"os": "Ubuntu 24.04", "architecture": "aarch64",
                                  "compiler": "GNU C++ / Fortran 13.3", "mpi_ranks": 1},
        "known_limitations": LIMITATIONS,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(case, indent=2) + "\n")
    print(f"wrote {out} ({len(tests)} pairs)")


if __name__ == "__main__":
    sys.exit(main())
