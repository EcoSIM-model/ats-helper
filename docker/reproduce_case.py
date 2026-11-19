#!/usr/bin/env python3
"""Run the source-locked cases shipped with ats-helper, inside Docker."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_file(directory, name):
    path = (directory / name).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError(f"Case file escapes its directory: {name}")
    return path


def load_case(directory):
    case = json.loads((directory / "case.json").read_text())
    if case["schema_version"] != 1 or case["name"] != directory.name:
        raise ValueError("Unsupported or mismatched case definition")
    if set(case["sources"]) != {"amanzi", "ats", "ecosim"}:
        raise ValueError("Case must lock Amanzi, ATS, and EcoSIM")
    for source in case["sources"].values():
        if not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
            raise ValueError("Sources must use full commit hashes")
    if case.get("kind") == "pairs":
        load_pairs_case(case)
        return case
    for name, expected in case["files"].items():
        if sha256(case_file(directory, name)) != expected:
            raise ValueError(f"Checksum mismatch: {name}")
    for name in (case["input"], case["reference_checkpoint"]):
        if name not in case["files"]:
            raise ValueError(f"Missing checksum: {name}")
    if case["runtime"]["mpi_ranks"] != 1:
        raise ValueError("This column-case runner currently supports one MPI rank")
    return case


def command(args, **kwargs):
    print("+ " + " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def verify_checkout(path, source, check_origin=True, ignore_submodules=False):
    """HEAD is the locked commit, the origin is the locked URL, and nothing is edited.

    check_origin=False and ignore_submodules=True serve an ATS commit that Amanzi does not record:
    the ATS submodule keeps Amanzi's URL as its origin, and Amanzi sees its pointer as moved."""
    if git(path, "rev-parse", "HEAD") != source["commit"]:
        raise RuntimeError(f"Revision differs in {path}; preserve it and use another case volume")
    if check_origin and git(path, "remote", "get-url", "origin") != source["url"]:
        raise RuntimeError(f"Unexpected origin in {path}")
    status = ["status", "--porcelain", "--untracked-files=normal"]
    if ignore_submodules:
        status.append("--ignore-submodules=all")
    if git(path, *status):
        raise RuntimeError(f"Local changes in {path}; refusing to overwrite them")


# Amanzi's and ATS's CMake derive their version strings from `git describe --tags`,
# and fail to configure when no tag is reachable. Shallow clones have no history, and
# GitHub forks do not copy upstream tags, so tags are taken from these repositories
# when the pinned origin cannot describe the commit.
VERSION_TAG_SOURCES = {
    "amanzi": "https://github.com/amanzi/amanzi.git",
    "ats": "https://github.com/amanzi/ats.git",
}


def describable(path):
    return subprocess.run(["git", "-C", str(path), "describe", "--tags"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def ensure_version_history(path, name):
    """Fetch full history and tags so `git describe --tags` works; HEAD is unchanged."""
    shallow = git(path, "rev-parse", "--is-shallow-repository") == "true"
    if not shallow and describable(path):
        return
    head = git(path, "rev-parse", "HEAD")
    fetch = ["git", "-C", path, "fetch", "--no-recurse-submodules"]
    if shallow:
        command(fetch + ["--unshallow", "--tags", "origin"])
    else:
        command(fetch + ["--tags", "origin"])
    upstream = VERSION_TAG_SOURCES.get(name)
    if not describable(path) and upstream:
        command(fetch + [upstream, "refs/tags/*:refs/tags/*"])
    if git(path, "rev-parse", "HEAD") != head:
        raise RuntimeError(f"Fetching version history moved HEAD in {path}")
    if not describable(path):
        raise RuntimeError(f"No reachable tag for {name} in {path}; its version cannot be derived")


ATS_PATH = "src/physics/ats"
GITHUB_SSH = ["-c", "url.https://github.com/.insteadOf=git@github.com:"]


def submodule_paths(path):
    """submodule paths recorded in a checkout's .gitmodules"""
    listing = subprocess.run(["git", "-C", str(path), "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
                             capture_output=True, text=True).stdout
    return [line.split(maxsplit=1)[1] for line in listing.splitlines()]


def checkout_pinned_ats(ats, source):
    """Put ATS on the locked commit when Amanzi does not record it (source['override_amanzi_gitlink']).

    The commit is fetched, with its history, from the case's own URL; the submodule keeps Amanzi's URL
    as its origin. Branch tips are never followed."""
    if git(ats, "rev-parse", "HEAD") != source["commit"]:
        fetch = ["git", "-C", ats, "fetch", "--no-recurse-submodules"]
        if git(ats, "rev-parse", "--is-shallow-repository") == "true":
            fetch.append("--unshallow")
        command(fetch + [source["url"], source["commit"]])
        command(["git", "-C", ats, "checkout", "--detach", source["commit"]])
    command(["git", *GITHUB_SSH, "-C", ats, "submodule", "update", "--init", "--recursive", "--depth", "1"])


def prepare_sources(base, case):
    sources = base / "sources"
    sources.mkdir(exist_ok=True)
    ats = sources / "amanzi" / ATS_PATH
    override = bool(case["sources"]["ats"].get("override_amanzi_gitlink"))
    for name in ("amanzi", "ecosim"):
        path, source = sources / name, case["sources"][name]
        # An ATS commit that Amanzi does not record leaves Amanzi's ATS pointer moved by design.
        moved_ats = override and name == "amanzi"
        if not path.exists():
            command(["git", "clone", "--no-checkout", "--depth", "1", source["url"], path])
        # A clone interrupted before checkout can resume. A different committed
        # checkout or edits are never reset to the requested revision.
        if subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", "HEAD"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            current = git(path, "rev-parse", "HEAD")
            unpopulated = not any(p.name != ".git" for p in path.iterdir())
            if current != source["commit"] and not unpopulated:
                raise RuntimeError(f"Unexpected existing revision in {path}; leaving it untouched")
            if not unpopulated:
                verify_checkout(path, source, ignore_submodules=moved_ats)
        if git(path, "remote", "get-url", "origin") != source["url"]:
            raise RuntimeError(f"Unexpected origin in {path}")
        if not any(p.name != ".git" for p in path.iterdir()):
            command(["git", "-C", path, "fetch", "--depth", "1", "origin", source["commit"]])
            command(["git", "-C", path, "checkout", "--detach", source["commit"]])
        # Recursive initialization follows gitlinks, never moving branch tips.
        # EcoSIM records a GitHub SSH URL for ecosim-agent. Public recursive
        # downloads must also work in Docker without forwarding SSH credentials.
        update = ["git", *GITHUB_SSH, "-C", path, "submodule", "update", "--init", "--recursive", "--depth", "1"]
        if moved_ats and ats.exists() and git(ats, "rev-parse", "HEAD") == case["sources"]["ats"]["commit"]:
            # Already on the locked ATS: update only the other submodules, so it is not reset to Amanzi's.
            update += ["--", *[q for q in submodule_paths(path) if q != ATS_PATH]]
            if len(update) > update.index("--") + 1:
                command(update)
        else:
            command(update)
        verify_checkout(path, source, ignore_submodules=moved_ats)
        status = git(path, "submodule", "status", "--recursive")
        if any(line.startswith(("-", "+", "U")) and not (moved_ats and f" {ATS_PATH}" in line)
               for line in status.splitlines()):
            raise RuntimeError(f"Incomplete or mismatched recursive submodules in {path}")
    if override:
        checkout_pinned_ats(ats, case["sources"]["ats"])
    verify_checkout(ats, case["sources"]["ats"], check_origin=not override)
    for name, path in (("amanzi", sources / "amanzi"), ("ats", ats)):
        ensure_version_history(path, name)
        verify_checkout(path, case["sources"][name], check_origin=not (override and name == "ats"),
                        ignore_submodules=override and name == "amanzi")
    return {name: {"commit": git(path, "rev-parse", "HEAD"),
                   "origin": git(path, "remote", "get-url", "origin"),
                   "recursive_submodules": git(path, "submodule", "status", "--recursive")}
            for name, path in (("amanzi", sources / "amanzi"), ("ats", ats),
                               ("ecosim", sources / "ecosim"))}


# ---------------------------------------------------------------------------------------------
# Paired-model cases (kind "pairs"): a suite of ATS-EcoSIM / standalone EcoSIM pairs under
# cases/<suite>/, run with tools/ats_pairs.py's runner instead of one ATS input.

PAIR_INPUT_NAMES = ("ats_match.xml", "standalone.namelist", "sp_phenol.nc")
PFT_FILE_RE = re.compile(r"[^\s\"'<>=]*/input_data/(ecosim_pftpar_[0-9]+\.nc)")


def pair_input_files(pairs_dir):
    """the generated input files of a suite's pairs directory (never run outputs, logs or reports)"""
    files = []
    for path in sorted(pairs_dir.rglob("*")):
        rel = path.relative_to(pairs_dir)
        if not path.is_file():
            continue
        if rel.parts[0] == "common" or rel == Path("README.md") or (len(rel.parts) == 2 and rel.name in PAIR_INPUT_NAMES):
            files.append(rel)
    return files


def pair_inputs_sha256(pairs_dir):
    """one checksum over the names and contents of all pair input files"""
    digest = hashlib.sha256()
    for rel in pair_input_files(pairs_dir):
        digest.update(f"{rel.as_posix()}\0{sha256(pairs_dir / rel)}\n".encode())
    return digest.hexdigest()


def load_pairs_case(case):
    for key in ("suite", "profile", "files", "pair_inputs_sha256", "pft_file", "expected", "comparison", "known_limitations"):
        if key not in case:
            raise ValueError(f"Pairs case lacks {key!r}")
    suite = case_file(ROOT, case["suite"])
    if not suite.is_file():
        raise ValueError(f"Missing suite: {case['suite']}")
    for name, expected in case["files"].items():
        if sha256(case_file(ROOT, name)) != expected:
            raise ValueError(f"Checksum mismatch: {name}")
    if case["suite"] not in case["files"]:
        raise ValueError("The suite file needs a checksum")
    if pair_inputs_sha256(suite.parent / "pairs") != case["pair_inputs_sha256"]:
        raise ValueError("The generated pair inputs differ from the locked ones; regenerate and re-lock the case")


def localize_pair_inputs(pairs_dir, ecosim_src):
    """Point every PFT parameter file reference at the locked EcoSIM checkout.

    The committed inputs name the file through the developer's checkout (repos/dev/ecosim); a
    reproduction builds EcoSIM at the locked commit in its own volume. Only that path changes."""
    found = 0
    for path in [*pairs_dir.glob("*/ats_match.xml"), *pairs_dir.glob("*/standalone.namelist")]:
        text = path.read_text()
        new, n = PFT_FILE_RE.subn(lambda m: f"{ecosim_src}/input_data/{m.group(1)}", text)
        if n:
            path.write_text(new)
        found += n
    if not found:
        raise RuntimeError(f"No PFT parameter file reference found in the pair inputs under {pairs_dir}")


def copy_suite(case, run):
    """copy the suite's inputs (not its outputs) under the run directory, keeping relative paths valid"""
    suite = case_file(ROOT, case["suite"])
    target = run / suite.parent.relative_to(ROOT)
    for sub in ("data", "templates"):
        if (suite.parent / sub).is_dir():
            shutil.copytree(suite.parent / sub, target / sub)
    shutil.copy2(suite, target / suite.name)
    pairs = suite.parent / "pairs"
    for rel in pair_input_files(pairs):
        (target / "pairs" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pairs / rel, target / "pairs" / rel)
    return target / suite.name


def verify_pairs(case, results, pairs_dir, only=None):
    """every run finished cleanly and the ATS accepted-cycle counts equal the locked ones
    (for the tests in `only`, or all of them)"""
    from ats_ecosim_pairs import runner

    issues, tests = [], {}
    tolerance = case["comparison"]["cycle_tolerance"]
    for backend, rows in results.items():
        for name, status, detail in rows:
            row = tests.setdefault(name, {})
            row[backend] = {"status": status, "detail": detail}
            if status != "PASSED":
                issues.append(f"{name} {backend}: {status}: {detail}")
    for name, want in case["expected"].items():
        if only and name not in only:
            continue
        row = tests.setdefault(name, {})
        log = pairs_dir / name / "ats" / "out.log"
        last = runner.last_cycle(log) if log.is_file() else None
        got = int(last[0]) if last else None
        row["ats_cycles"] = {"expected": want["ats_cycles"], "got": got}
        if got is None or abs(got - want["ats_cycles"]) > tolerance:
            issues.append(f"{name}: ATS accepted cycles {got}, locked {want['ats_cycles']} (tolerance {tolerance})")
    return {"passed": not issues, "issues": issues, "tests": tests}


def run_pairs(case, run, ecosim_src, only=None):
    """run every pair of the suite (standalone EcoSIM, then ATS) on a copy of the locked inputs;
    `only` restricts it to some tests, which is for trying the workflow, not for reproducing"""
    from ats_ecosim_pairs import jobs as jobs_mod, manifest, runner

    expected_pft = case["pft_file"]
    pft = Path(ecosim_src) / "input_data" / Path(expected_pft["path"]).name
    if sha256(pft) != expected_pft["sha256"]:
        raise RuntimeError(f"{pft} differs from the locked PFT parameter file")
    suite = copy_suite(case, run)
    localize_pair_inputs(suite.parent / "pairs", ecosim_src)
    m = manifest.load(suite)
    # Open MPI refuses to start as root otherwise.
    os.environ.setdefault("OMPI_ALLOW_RUN_AS_ROOT", "1")
    os.environ.setdefault("OMPI_ALLOW_RUN_AS_ROOT_CONFIRM", "1")
    parallel = int(os.environ.get("PAIRS_JOBS", min(4, os.cpu_count() or 1)))
    results = {}
    for backend in ("standalone", "ats-matched"):
        batch = jobs_mod.build_jobs(m, backend, only=only)
        results[backend] = runner.run_jobs(batch, parallel=parallel, max_wall_s=120 * 60)
    return results, suite.parent / "pairs"


def cycle_records(stdout):
    return [(int(cycle), float(time), float(dt)) for cycle, time, dt in re.findall(
        r"Cycle = (\d+),\s+Time \[days\] = ([\d.eE+\-]+),\s+dt \[days\] = ([\d.eE+\-]+)", stdout)]


def verify_results(directory, case, run):
    import h5py
    import numpy as np

    issues, comparisons = [], {}
    stdout = (run / "model.stdout").read_text(errors="replace")
    records = cycle_records(stdout)
    expected = case["runtime"]
    if [row[0] for row in records] != list(range(expected["accepted_steps"])):
        issues.append("Cycle sequence differs from the baseline (including retried steps)")
    failures = len(re.findall(r"step failed with error code", stdout, re.I))
    if failures != expected["failed_steps"]:
        issues.append(f"Failed nonlinear steps: {failures}")
    with h5py.File(case_file(directory, case["reference_checkpoint"])) as ref, \
            h5py.File(run / "checkpoint_final.h5") as actual:
        final_time = float(actual.attrs["time"])
        if not np.isclose(final_time, expected["end_time_s"], rtol=0, atol=1e-6):
            issues.append(f"Final time differs: {final_time}")
        for attr, value in (("cycle", expected["accepted_steps"]), ("mpi_num_procs", expected["mpi_ranks"])):
            if int(actual.attrs[attr]) != value:
                issues.append(f"Checkpoint attribute differs: {attr}")
        datasets = lambda f: {name: obj for name, obj in f.items() if isinstance(obj, h5py.Dataset)}
        old, new = datasets(ref), datasets(actual)
        if old.keys() != new.keys():
            issues.append("Checkpoint dataset names differ")
        for name in sorted(old.keys() & new.keys()):
            a, b = old[name][...], new[name][...]
            if a.shape != b.shape:
                issues.append(f"Dataset shape differs: {name}")
                continue
            if not np.issubdtype(a.dtype, np.number):
                okay = np.array_equal(a, b)
                delta = None
            else:
                okay = bool(np.isfinite(b).all() and np.allclose(a, b, **case["comparison"], equal_nan=False))
                delta = float(np.max(np.abs(b.astype(float) - a.astype(float)))) if a.size else 0.0
            comparisons[name] = {"passed": okay, "max_absolute_difference": delta}
            if not okay:
                issues.append(f"Checkpoint values differ: {name}")
    return {"passed": not issues, "issues": issues, "final_time_s": final_time,
            "logged_steps": len(records), "failed_steps": failures, "datasets": comparisons}


def make_plot(directory, case, run):
    import h5py
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    records = cycle_records((run / "model.stdout").read_text(errors="replace"))
    with (run / "timesteps.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["cycle_start", "time_start_days", "dt_days"])
        writer.writerows(records)
    fig, axes = plt.subplots(1, 3, figsize=(13, 5), layout="constrained")
    if records:
        _, times, dts = np.array(records).T
        axes[0].plot(times, dts * 1440, lw=.8)
    axes[0].set(xlabel="Simulation time (days)", ylabel="Timestep (minutes)", title="Logged timesteps")
    for path, label, style in ((run / "checkpoint_final.h5", "Reproduced", "-"),
                               (case_file(directory, case["reference_checkpoint"]), "Reference", "--")):
        with h5py.File(path) as f:
            # Bundled input generates 100 equal cells over a 10 m vertical column.
            depth = 10 - (np.arange(100) + .5) * .1
            axes[1].plot(f["temperature.cell.0"][...].ravel() - 273.15, depth, style, label=label)
            for field, color in (("saturation_liquid", "#007f83"), ("saturation_ice", "#0072b2")):
                axes[2].plot(f[field + "@current.cell.0"][...].ravel(), depth, style,
                             color=color, label=f"{label}: {field.removeprefix('saturation_')}")
    axes[1].set(xlabel="Soil temperature (°C)", ylabel="Depth (m)", title="Day 200: soil temperature")
    axes[2].set(xlabel="Saturation", ylabel="Depth (m)", title="Day 200: water phases")
    for ax in axes:
        ax.grid(alpha=.25)
    for ax in axes[1:]:
        ax.invert_yaxis()
        ax.legend(fontsize=8)
    fig.suptitle(case["name"])
    fig.savefig(run / "results.png", dpi=150)
    fig.savefig(run / "results.pdf")
    plt.close(fig)


def write_report(run, report, case):
    (run / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    esc = html.escape
    issues = report.get("verification", {}).get("issues", [])
    links = "<a href='report.json'>Full run record</a> · " + (
        "" if case.get("kind") == "pairs" else "<a href='model.stdout'>Model log</a> · ") + \
        "<a href='verification.json'>Comparison</a>"
    body = (f"<h1>{esc(case['name'])}</h1><p>Status: <b>{esc(report['status'])}</b></p>"
            f"<p>{esc(case['description'])}</p><p>{links}</p>")
    tests = report.get("verification", {}).get("tests")
    if tests:
        rows = "".join(
            f"<tr><td>{esc(name)}</td><td>{esc(row.get('standalone', {}).get('status', '-'))}</td>"
            f"<td>{esc(row.get('ats-matched', {}).get('status', '-'))}</td>"
            f"<td>{esc(str(row.get('ats_cycles', {}).get('got')))} / {esc(str(row.get('ats_cycles', {}).get('expected')))}</td></tr>"
            for name, row in sorted(tests.items()))
        body += ("<h2>Pairs</h2><table border='1' cellpadding='4' style='border-collapse:collapse'>"
                 "<tr><th>test</th><th>standalone</th><th>ATS</th><th>ATS cycles (got / locked)</th></tr>" + rows + "</table>"
                 "<p>The inputs and outputs of the run are under <code>cases/</code> in this directory; "
                 "<code>python3 tools/ats_pairs.py report &lt;this directory&gt;/cases/&lt;suite&gt;/suite.json</code> "
                 "builds the comparison report on the host.</p>")
    if (run / "results.png").exists():
        body += "<a href='results.pdf'><img src='results.png' style='width:100%;max-width:1300px'></a>"
    body += "<h2>Source commits</h2><ul>" + "".join(
        f"<li>{esc(name)}: <code>{esc(source['commit'])}</code></li>"
        for name, source in case["sources"].items()) + "</ul>"
    body += "<h2>Baseline limitations</h2><ul>" + "".join(
        f"<li>{esc(note)}</li>" for note in case["known_limitations"]) + "</ul>"
    if issues or report.get("error"):
        body += "<pre>" + esc("\n".join(issues + [report.get("error", "")])) + "</pre>"
    (run / "index.html").write_text("<!doctype html><meta charset='utf-8'><title>Reproduction report</title>"
        "<body style='font-family:system-ui;max-width:1400px;margin:2em auto;padding:0 1em'>" + body + "</body>\n")


def reproduce(name, prepare_only=False):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError("Invalid case name")
    directory = ROOT / "cases" / name
    case = load_case(directory)
    base = Path(os.environ.get("ATS_BASE", "/ats-build"))
    base.mkdir(parents=True, exist_ok=True)
    with (base / ".reproduce.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another reproduction is using this volume") from None
        identity = sha256(directory / "case.json")
        saved = base / "case-lock.json"
        if saved.exists() and json.loads(saved.read_text())["manifest_sha256"] != identity:
            raise RuntimeError("Case volume belongs to a different manifest; use its matching volume")
        saved.write_text(json.dumps({"manifest_sha256": identity, "case": case}, indent=2) + "\n")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        run = ROOT / "build-logs/reproduce" / name / stamp
        run.mkdir(parents=True)
        report = {"case": name, "manifest_sha256": identity, "status": "preparing",
                  "architecture": platform.machine(), "image_id": os.environ.get("ATS_REPRODUCE_IMAGE_ID"),
                  "started_utc": stamp, "output_directory": str(run),
                  "workflow_sha256": {name: sha256(ROOT / name) for name in (
                      "reproduce.sh", "run_docker.sh", "docker/reproduce-case.sh",
                      "docker/reproduce_case.py", "docker/build-ats-ecosim.sh",
                      "docker/ats-env.sh", "docker/ubuntu-compiler.dockerfile")}}
        shutil.copy2(directory / "case.json", run / "case.json")
        print(f"Results: {run}", flush=True)
        try:
            report["sources"] = prepare_sources(base, case)
            if prepare_only:
                report["status"] = "prepared (not built or run)"
                return
            report["status"] = "building"
            (run / "packages.txt").write_text(subprocess.check_output(
                ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"], text=True))
            # Load fresh library paths in each child, including after installation.
            command(["bash", "--login", ROOT / "docker/build-ats-ecosim.sh"])
            report["executable_sha256"] = sha256(Path(os.environ["AMANZI_DIR"]) / "bin/ats")
            report["status"] = "running"
            if case.get("kind") == "pairs":
                only = os.environ.get("PAIRS_ONLY", "").split() or None
                results, pairs_dir = run_pairs(case, run, base / "sources/ecosim", only)
                report["status"] = "checking"
                verification = verify_pairs(case, results, pairs_dir, only)
                report["verification"] = verification
                (run / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
                if not verification["passed"]:
                    raise RuntimeError("Pairs completed but differ from the locked baseline; see verification.json")
                report["status"] = "passed" if not only else f"passed for {', '.join(only)} only (not a reproduction)"
                return
            for filename in (case["input"], *(n for n in case["files"] if n.startswith("data/"))):
                target = run / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(case_file(directory, filename), target)
            with (run / "model.stdout").open("w") as stream:
                command(["bash", "--login", "-c",
                         'exec mpiexec --allow-run-as-root --stdin none -n 1 "$AMANZI_DIR/bin/ats" --xml_file=input.xml'],
                        cwd=run, stdout=stream, stderr=subprocess.STDOUT)
            report["status"] = "checking"
            verification = verify_results(directory, case, run)
            report["verification"] = verification
            (run / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
            make_plot(directory, case, run)
            if not verification["passed"]:
                raise RuntimeError("Simulation completed but differs from the locked baseline; see verification.json")
            report["status"] = "passed"
        except Exception as exc:
            report["failed_stage"] = report["status"]
            report["status"] = "failed"
            report["error"] = str(exc)
            raise
        finally:
            report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            for logfile in (base / "logs").glob("*"):
                if logfile.is_file():
                    (run / "build").mkdir(exist_ok=True)
                    shutil.copy2(logfile, run / "build" / logfile.name)
            write_report(run, report, case)
            print(f"{report['status']}: {run / 'index.html'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        reproduce(args.case, args.prepare_only)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Reproduction failed: {exc}")
