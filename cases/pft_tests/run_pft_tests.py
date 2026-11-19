#!/usr/bin/env python3
"""Compatibility wrapper for the ATS side of the PFT suite; the work is done by tools/ats_pairs.py.

    python run_pft_tests.py --dry-run                 # write input/*.xml
    python run_pft_tests.py --dev-pk --jobs 4         # write them with the dev-build settings and run
    python run_pft_tests.py --matched --jobs 4        # run the matched variants pairs/<test>/ats_match.xml
    python run_pft_tests.py --summarize [--matched]   # re-evaluate finished runs from their logs

These are the same as `ats_pairs.py inputs|run|summarize cases/pft_tests/suite.json ...`; see
tools/README.md. --dev-pk is the manifest's "dev-pk" profile.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from ats_ecosim_pairs import cli, render  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ats", default="ats", help="ATS executable (default: ats on PATH)")
    ap.add_argument("--np", type=int, default=1, help="MPI ranks per test (default: 1)")
    ap.add_argument("--dry-run", action="store_true", help="write the inputs, run nothing")
    ap.add_argument("--ecosim-src", default=str(render.DEFAULT_ECOSIM_SRC), help="EcoSIM source tree (PFT file)")
    ap.add_argument("--dev-pk", action="store_true", help="the manifest's dev-pk profile (development build settings)")
    ap.add_argument("--only", nargs="*", help="only these tests")
    ap.add_argument("--jobs", type=int, default=1, help="tests run at the same time (default: 1)")
    ap.add_argument("--min-dt-days", type=float, default=1e-5, help="stop a test below this timestep [days]")
    ap.add_argument("--max-wall-min", type=float, default=45, help="wall-clock limit per test [min]")
    ap.add_argument("--max-log-gb", type=float, default=15, help="log size limit per test [GB]")
    ap.add_argument("--matched", action="store_true", help="the matched variants in pairs/<test>/ats/")
    ap.add_argument("--summarize", action="store_true", help="re-evaluate existing runs, run nothing")
    a = ap.parse_args()
    suite = str(HERE / "suite.json")
    only = ["--only", *a.only] if a.only else []
    backend = "ats-matched" if a.matched else "ats"
    if a.summarize:
        return cli.main(["summarize", suite, backend, *only])
    run = ["run", suite, backend, *only, "--ats", a.ats, "--np", str(a.np), "--jobs", str(a.jobs),
           "--min-dt-days", str(a.min_dt_days), "--max-wall-min", str(a.max_wall_min), "--max-log-gb", str(a.max_log_gb)]
    if a.matched:
        return cli.main(run + (["--dry-run"] if a.dry_run else []))
    rc = cli.main(["inputs", suite, *only, "--ecosim-src", a.ecosim_src] + (["--profile", "dev-pk"] if a.dev_pk else []))
    return rc if a.dry_run else (rc or cli.main(run))


if __name__ == "__main__":
    sys.exit(main())
