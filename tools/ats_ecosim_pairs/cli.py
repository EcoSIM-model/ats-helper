"""Command line of the ATS-EcoSIM pairs tooling.

    python tools/ats_pairs.py inputs    SUITE [--profile P] [--only T ...] [--out DIR] [--dry-run]
    python tools/ats_pairs.py pairs     SUITE [--profile P] [--only T ...] [--population G=V ...] [--dry-run]
    python tools/ats_pairs.py run       SUITE ats|ats-matched|standalone [--jobs N] [--only T ...] [--container NAME]
    python tools/ats_pairs.py summarize SUITE ats|ats-matched|standalone [--only T ...] [--container NAME]
    python tools/ats_pairs.py report    SUITE [--ats-kind matched|suite] [--only T ...] [--check]

SUITE is the manifest, for example cases/pft_tests/suite.json. `run` and `summarize` need only the
standard library; with --container NAME they run inside that Docker container (where the executables are)
while the other commands run on the host (they need numpy, h5py, netCDF4 and matplotlib).
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import jobs as jobs_mod
from . import manifest as manifest_mod
from . import render, runner


def _add_common(p, only=True):
    p.add_argument("suite", help="suite manifest (JSON)")
    if only:
        p.add_argument("--only", nargs="*", help="only these tests")


def build_parser():
    ap = argparse.ArgumentParser(prog="ats_pairs.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inputs", help="write the suite's ATS inputs")
    _add_common(p)
    p.add_argument("--profile", help="named edit list of the manifest, for example dev-pk")
    p.add_argument("--out", help="output directory (default: <suite>/input)")
    p.add_argument("--ecosim-src", default=str(render.DEFAULT_ECOSIM_SRC), help="EcoSIM source tree (PFT file)")
    p.add_argument("--dry-run", action="store_true", help="print what would be written, write nothing")

    p = sub.add_parser("pairs", help="write the matched standalone cases and ATS variants")
    _add_common(p)
    p.add_argument("--profile", help="named edit list applied to the ATS variants, for example dev-pk")
    p.add_argument("--out", help="output directory (default: <suite>/pairs)")
    p.add_argument("--ecosim-src", default=str(render.DEFAULT_ECOSIM_SRC), help="EcoSIM source tree")
    p.add_argument("--ats-src", default=str(render.WORKSPACE / "repos" / "dev" / "ats"),
                   help="ATS source tree, for tools/meshing/meshing_ats")
    p.add_argument("--population", nargs="*", default=[], metavar="GROUP=VALUE",
                   help="override the living population [plants m-2] of a group, for example shrub=2")
    p.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")

    for name, help_ in (("run", "run the tests with a watchdog"), ("summarize", "re-evaluate finished runs from their logs")):
        p = sub.add_parser(name, help=help_)
        _add_common(p)
        p.add_argument("backend", choices=jobs_mod.BACKENDS)
        p.add_argument("--container", help="run inside this Docker container (the workspace is /ats-helper there)")
        if name == "run":
            p.add_argument("--jobs", type=int, default=1, help="tests run at the same time (default: 1)")
            p.add_argument("--np", type=int, default=1, help="MPI ranks per ATS run (default: 1)")
            p.add_argument("--ats", default="ats", help="ATS executable (default: ats on PATH)")
            p.add_argument("--exe", default=jobs_mod.DEFAULT_STANDALONE_EXE, help="standalone EcoSIM executable")
            p.add_argument("--min-dt-days", type=float, default=1e-5, help="stop an ATS run below this timestep [days]")
            p.add_argument("--max-wall-min", type=float, default=None,
                           help="wall-clock limit per test [min] (default: 45 for ats, 60 for standalone)")
            p.add_argument("--max-log-gb", type=float, default=15, help="log size limit per test [GB]")
            p.add_argument("--dry-run", action="store_true", help="list the runs, start nothing")

    p = sub.add_parser("report", help="write the comparison report")
    _add_common(p)
    p.add_argument("--ats-kind", choices=["matched", "suite"], default="matched",
                   help="ATS side: the matched variants pairs/<test>/ats (default) or the suite runs <test>/")
    p.add_argument("--out", help="output directory (default: <suite>/pairs/report)")
    p.add_argument("--check", action="store_true", help="exit with status 1 if any graded quantity is red")
    return ap


def in_container(name, argv, suite):
    """re-run this command inside a container, where the workspace is /ats-helper"""
    rel = os.path.relpath(Path(suite).resolve(), render.WORKSPACE)
    forwarded, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
        elif a == "--container":
            skip = True
        elif a.startswith("--container="):
            pass
        else:
            forwarded.append(rel if i == 1 else a)          # argv[0] is the command, argv[1] the suite
    cmd = "cd /ats-helper && python3 -B tools/ats_pairs.py " + " ".join(shlex.quote(a) for a in forwarded)
    return subprocess.call(["docker", "exec", "-i", name, "bash", "--login", "-c", cmd])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    args = build_parser().parse_args(argv)
    if args.command in ("run", "summarize") and args.container:
        return in_container(args.container, argv, args.suite)
    try:
        m = manifest_mod.load(args.suite)
    except manifest_mod.ManifestError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    only = set(args.only) if getattr(args, "only", None) else None

    if args.command == "inputs":
        print(f"Suite    : {m.path}\nProfile  : {args.profile or 'none'}\nOutput   : {args.out or m.inputs_dir}\n")
        render.generate_inputs(m, args.ecosim_src, args.profile, only, args.out, args.dry_run)
        return 0

    if args.command == "pairs":
        from . import pairs                                   # needs numpy, h5py, netCDF4
        population = {}
        for item in args.population:
            group, value = item.split("=")
            if group not in {t.group for t in m.tests(baseline=False)}:
                print(f"error: unknown group {group!r}", file=sys.stderr)
                return 2
            population[group] = float(value)
        pairs.generate_pairs(m, args.ecosim_src, args.ats_src, args.out, args.profile, only, population, args.dry_run)
        return 0

    if args.command == "report":
        from . import report                                  # needs numpy, h5py, netCDF4, matplotlib
        return report.build_report(m, args.ats_kind, only, args.out, args.check)

    if args.command == "run":
        job_list = jobs_mod.build_jobs(m, args.backend, only, args.ats, args.np, args.exe)
        wall = args.max_wall_min if args.max_wall_min is not None else (60 if args.backend == "standalone" else 45)
        print(f"Suite: {m.path}; backend {args.backend}; {len(job_list)} tests; jobs={args.jobs}")
        results = runner.run_jobs(job_list, args.jobs, args.dry_run, min_dt_days=args.min_dt_days,
                                  max_wall_s=wall * 60, max_log_gb=args.max_log_gb)
    else:
        job_list = jobs_mod.build_jobs(m, args.backend, only, require_inputs=False)
        results = runner.summarize_jobs(job_list)
    # the summary file covers the whole suite, so a subset (--only) must not overwrite it
    runner.print_summary(results, None if only else jobs_mod.summary_file(m, args.backend))
    return 0
