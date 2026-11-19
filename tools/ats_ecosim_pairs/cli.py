"""Command line of the ATS-EcoSIM pairs tooling.

    python tools/ats_pairs.py pair      CASE [--only T ...] [--start-temperature K|xml] [--population X=V ...] [...]
    python tools/ats_pairs.py run       CASE [standalone|ats|both] [--jobs N] [--only T ...] [--container NAME]
    python tools/ats_pairs.py summarize CASE [standalone|ats|both] [--only T ...] [--container NAME]
    python tools/ats_pairs.py report    CASE [--only T ...] [--check]
    python tools/ats_pairs.py all       CASE [--container NAME] [--jobs N] [pair options]

CASE is a folder with ATS-EcoSIM inputs (*.xml) and their data, for example cases/pft_tests. For every
input `pair` writes a matched standalone EcoSIM case and a revised ATS input under CASE/pairs/<test>/,
`run` runs both with a watchdog, `report` compares them, and `all` does the three in turn. `run` and
`summarize` need only the standard library; with --container NAME they run inside that Docker container
(where the executables are) while the other commands run on the host (numpy, h5py, netCDF4, matplotlib).
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import case as case_mod
from . import jobs as jobs_mod
from . import render, runner


def _add_common(p, only=True):
    p.add_argument("case", help="case folder with the ATS-EcoSIM inputs (*.xml)")
    if only:
        p.add_argument("--only", nargs="*", help="only these tests (input file names without .xml)")


def _add_pair(p):
    d = case_mod.OPTIONS
    p.add_argument("--ecosim-src", help="EcoSIM source tree: PFT file and templates (default: repos/dev/ecosim or repos/ecosim)")
    p.add_argument("--ats-src", help="ATS source tree, for tools/meshing/meshing_ats (default: repos/dev/ats or the Amanzi one)")
    p.add_argument("--start-temperature", default=str(d["start_temperature"]), metavar="K|xml",
                   help=f"start temperature [K] of both models (default {d['start_temperature']}); the ATS bottom "
                        "temperature becomes this + 0.28 K. 'xml' keeps the input's own")
    p.add_argument("--layers", type=int, default=d["layers"], help=f"graded layers of the column (default {d['layers']})")
    p.add_argument("--top-dz", type=float, default=d["top_dz"], help=f"top layer thickness [m] (default {d['top_dz']})")
    p.add_argument("--particle-density", type=float, default=d["particle_density"], help="standalone particle density [Mg m-3]")
    p.add_argument("--soil-albedo", type=float, default=d["soil_albedo"], help="standalone soil albedo")
    p.add_argument("--wind-height", type=float, default=None, help="wind reference height [m] (default: the input's)")
    p.add_argument("--longwave", choices=["ats"], default=None, help="give the standalone the sky longwave ATS computes")
    p.add_argument("--population", nargs="*", default=[], metavar="TEST|PFT|GROUP=VALUE",
                   help="living population [plants m-2], for example shrub=2 or pft_shrub_lai1=2")
    p.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")


def _add_run(p):
    p.add_argument("--container", help="run inside this Docker container (the workspace is /ats-helper there)")
    p.add_argument("--jobs", type=int, default=1, help="tests run at the same time (default: 1)")
    p.add_argument("--np", type=int, default=1, help="MPI ranks per ATS run (default: 1)")
    p.add_argument("--ats", default="ats", help="ATS executable (default: ats on PATH)")
    p.add_argument("--exe", default=jobs_mod.DEFAULT_STANDALONE_EXE, help="standalone EcoSIM executable")
    p.add_argument("--min-dt-days", type=float, default=1e-5, help="stop an ATS run below this timestep [days]")
    p.add_argument("--max-wall-min", type=float, default=None,
                   help="wall-clock limit per test [min] (default: 60 for standalone, 45 for ats)")
    p.add_argument("--max-log-gb", type=float, default=15, help="log size limit per test [GB]")


def build_parser():
    ap = argparse.ArgumentParser(prog="ats_pairs.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pair", help="write the matched standalone cases and revised ATS inputs")
    _add_common(p)
    _add_pair(p)

    p = sub.add_parser("run", help="run the tests with a watchdog")
    _add_common(p)
    p.add_argument("backend", nargs="?", choices=[*jobs_mod.BACKENDS, "both"], default="both")
    _add_run(p)
    p.add_argument("--dry-run", action="store_true", help="list the runs, start nothing")

    p = sub.add_parser("summarize", help="re-evaluate finished runs from their logs")
    _add_common(p)
    p.add_argument("backend", nargs="?", choices=[*jobs_mod.BACKENDS, "both"], default="both")
    p.add_argument("--container", help="run inside this Docker container (the workspace is /ats-helper there)")

    p = sub.add_parser("report", help="write the comparison report")
    _add_common(p)
    p.add_argument("--out", help="output directory (default: CASE/pairs/report)")
    p.add_argument("--check", action="store_true", help="exit with status 1 if any graded quantity is red")

    p = sub.add_parser("all", help="pair, run both models and report")
    _add_common(p)
    _add_pair(p)
    _add_run(p)
    p.add_argument("--out", help="report directory (default: CASE/pairs/report)")
    p.add_argument("--check", action="store_true", help="exit with status 1 if any graded quantity is red")
    return ap


def in_container(name, argv, case):
    """re-run this command inside a container, where the workspace is /ats-helper"""
    rel = os.path.relpath(Path(case).resolve(), render.WORKSPACE)
    if rel.startswith(".."):
        print(f"error: {case} is outside the workspace, which is what the container mounts", file=sys.stderr)
        return 2
    forwarded, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
        elif a == "--container":
            skip = True
        elif a.startswith("--container="):
            pass
        else:
            forwarded.append(rel if i == 1 else a)          # argv[0] is the command, argv[1] the case
    cmd = "cd /ats-helper && python3 -B tools/ats_pairs.py " + " ".join(shlex.quote(a) for a in forwarded)
    return subprocess.call(["docker", "exec", "-i", name, "bash", "--login", "-c", cmd])


def _options(args):
    start = args.start_temperature
    return {"start_temperature": "xml" if start == "xml" else float(start), "layers": args.layers,
            "top_dz": args.top_dz, "particle_density": args.particle_density, "soil_albedo": args.soil_albedo,
            "wind_height": args.wind_height, "longwave": args.longwave}


def do_pair(case, args, only):
    from . import pairs                                       # needs numpy, h5py, netCDF4
    populations = {}
    for item in args.population:
        key, _, value = item.partition("=")
        try:
            populations[key] = float(value)
        except ValueError:
            print(f"error: --population wants NAME=VALUE, got {item!r}", file=sys.stderr)
            return 2
    try:
        pairs.generate_pairs(case, _options(args), args.ecosim_src, args.ats_src, only, populations, args.dry_run)
    except (case_mod.CaseError, FileNotFoundError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 0


def do_run(case, args, only, backends, summarize=False):
    status = 0
    for backend in backends:
        if summarize:
            job_list = jobs_mod.build_jobs(case, backend, only, require_inputs=False)
            results = runner.summarize_jobs(job_list)
        else:
            job_list = jobs_mod.build_jobs(case, backend, only, args.ats, args.np, args.exe)
            wall = args.max_wall_min if args.max_wall_min is not None else (60 if backend == "standalone" else 45)
            print(f"Case: {case.root}; {backend}; {len(job_list)} tests; jobs={args.jobs}")
            results = runner.run_jobs(job_list, args.jobs, getattr(args, "dry_run", False), min_dt_days=args.min_dt_days,
                                      max_wall_s=wall * 60, max_log_gb=args.max_log_gb)
        # the summary file covers the whole case, so a subset (--only) must not overwrite it
        runner.print_summary(results, None if only else jobs_mod.summary_file(case, backend))
        if any(s not in ("PASSED", "skipped") for _, s, _ in results):
            status = 1
    return status


def do_report(case, args, only):
    from . import report, report_bare                         # needs numpy, h5py, netCDF4, matplotlib
    status = 0
    names = {t.name for t in case.tests(only)}
    bare = [t.name for t in case.tests(only) if t.is_baseline]
    vegetated = {t.name for t in case.tests(only, baseline=False)}
    if vegetated:
        status = report.build_report(case, "matched", vegetated, args.out, args.check)
    for name in bare:
        if report_bare.build(case, name, args.out) and only and name in names:
            status = status or 1                              # an explicit request that could not be met
    if not names:
        print("nothing to report")
        return 1
    return status


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    args = build_parser().parse_args(argv)
    if args.command in ("run", "summarize") and args.container:
        return in_container(args.container, argv, args.case)
    try:
        case = case_mod.load(args.case)
    except case_mod.CaseError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    only = set(args.only) if getattr(args, "only", None) else None
    backends = lambda: jobs_mod.BACKENDS if args.backend == "both" else (args.backend,)

    try:
        if args.command == "pair":
            return do_pair(case, args, only)
        if args.command == "run":
            return do_run(case, args, only, backends())
        if args.command == "summarize":
            return do_run(case, args, only, backends(), summarize=True)
        if args.command == "report":
            return do_report(case, args, only)
        # all: pair on the host, run in the container if one is named, then report
        status = do_pair(case, args, only)
        if status or args.dry_run:
            return status
        run_argv = ["run", args.case, *(["--only", *sorted(only)] if only else []), "--jobs", str(args.jobs),
                    "--np", str(args.np), "--ats", args.ats, "--exe", args.exe, "--min-dt-days", str(args.min_dt_days),
                    "--max-log-gb", str(args.max_log_gb)]
        if args.max_wall_min is not None:
            run_argv += ["--max-wall-min", str(args.max_wall_min)]
        if args.container:
            run_status = in_container(args.container, run_argv, args.case)
        else:
            run_status = main(run_argv)
        if run_status:
            print("some runs did not complete; building the report from the runs that did", file=sys.stderr)
        return do_report(case, args, only) or run_status
    except (case_mod.CaseError, FileNotFoundError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
