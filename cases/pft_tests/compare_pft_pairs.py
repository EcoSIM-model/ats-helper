#!/usr/bin/env python3
"""Compatibility wrapper: the comparison report of the PFT suite (ATS-EcoSIM against standalone EcoSIM).

    python compare_pft_pairs.py [--ats-kind matched|suite] [--only T ...] [--out DIR] [--check]

Same as `ats_pairs.py report cases/pft_tests/suite.json`; see tools/README.md.
Needs numpy, h5py, netCDF4 and matplotlib (the host Python). Writes pairs/report/index.html.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from ats_ecosim_pairs import cli  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ats-kind", choices=["matched", "suite"], default="matched", help="ATS side")
    ap.add_argument("--only", nargs="*", help="only these tests")
    ap.add_argument("--out", help="output directory (default: pairs/report)")
    ap.add_argument("--check", action="store_true", help="exit with status 1 if any graded quantity is red")
    a = ap.parse_args()
    argv = ["report", str(HERE / "suite.json"), "--ats-kind", a.ats_kind]
    argv += ["--only", *a.only] if a.only else []
    argv += ["--out", a.out] if a.out else []
    argv += ["--check"] if a.check else []
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
