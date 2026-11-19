#!/usr/bin/env python3
"""Compatibility wrapper: write the matched standalone EcoSIM cases and ATS variants of the PFT suite.

    python make_pft_standalone_pairs.py --dev-pk [--dry-run] [--tests T ...] [--population shrub=2]

Same as `ats_pairs.py pairs cases/pft_tests/suite.json [--profile dev-pk]`; see tools/README.md.
Needs numpy, h5py and netCDF4 (the host Python).
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from ats_ecosim_pairs import cli, render  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="output directory (default: pairs/)")
    ap.add_argument("--ecosim-src", default=str(render.DEFAULT_ECOSIM_SRC), help="EcoSIM source tree")
    ap.add_argument("--ats-src", default=str(render.WORKSPACE / "repos" / "dev" / "ats"), help="ATS source tree (meshing tools)")
    ap.add_argument("--tests", nargs="*", help="only these tests")
    ap.add_argument("--population", nargs="*", default=[], metavar="GROUP=VALUE", help="override a group's population")
    ap.add_argument("--dev-pk", action="store_true", help="the manifest's dev-pk profile in the ATS variants")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")
    a = ap.parse_args()
    argv = ["pairs", str(HERE / "suite.json"), "--ecosim-src", a.ecosim_src, "--ats-src", a.ats_src]
    argv += ["--out", a.out] if a.out else []
    argv += ["--only", *a.tests] if a.tests else []
    argv += ["--population", *a.population] if a.population else []
    argv += ["--profile", "dev-pk"] if a.dev_pk else []
    argv += ["--dry-run"] if a.dry_run else []
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
