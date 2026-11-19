#!/usr/bin/env python3
"""Compatibility wrapper: run the standalone EcoSIM half of each PFT test pair.

    python run_pft_standalone.py --jobs 4 [--only T ...] [--summarize]

Same as `ats_pairs.py run|summarize cases/pft_tests/suite.json standalone`; see tools/README.md.
The executable is in the EcoSIM install of the dev build, so run it in the dev container
(or add --container ats-dev to ats_pairs.py).
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from ats_ecosim_pairs import cli, jobs  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", default=jobs.DEFAULT_STANDALONE_EXE, help="standalone EcoSIM executable")
    ap.add_argument("--only", nargs="*", help="only these tests")
    ap.add_argument("--jobs", type=int, default=1, help="tests run at the same time (default: 1)")
    ap.add_argument("--max-wall-min", type=float, default=60, help="wall-clock limit per test [min]")
    ap.add_argument("--summarize", action="store_true", help="re-evaluate existing runs, run nothing")
    a = ap.parse_args()
    suite, only = str(HERE / "suite.json"), (["--only", *a.only] if a.only else [])
    if a.summarize:
        return cli.main(["summarize", suite, "standalone", *only])
    return cli.main(["run", suite, "standalone", *only, "--exe", a.exe, "--jobs", str(a.jobs),
                     "--max-wall-min", str(a.max_wall_min)])


if __name__ == "__main__":
    sys.exit(main())
