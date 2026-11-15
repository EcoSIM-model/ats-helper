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


def verify_checkout(path, source):
    if git(path, "rev-parse", "HEAD") != source["commit"]:
        raise RuntimeError(f"Revision differs in {path}; preserve it and use another case volume")
    if git(path, "remote", "get-url", "origin") != source["url"]:
        raise RuntimeError(f"Unexpected origin in {path}")
    if git(path, "status", "--porcelain", "--untracked-files=normal"):
        raise RuntimeError(f"Local changes in {path}; refusing to overwrite them")


def prepare_sources(base, case):
    sources = base / "sources"
    sources.mkdir(exist_ok=True)
    for name in ("amanzi", "ecosim"):
        path, source = sources / name, case["sources"][name]
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
                verify_checkout(path, source)
        if git(path, "remote", "get-url", "origin") != source["url"]:
            raise RuntimeError(f"Unexpected origin in {path}")
        if not any(p.name != ".git" for p in path.iterdir()):
            command(["git", "-C", path, "fetch", "--depth", "1", "origin", source["commit"]])
            command(["git", "-C", path, "checkout", "--detach", source["commit"]])
        # Recursive initialization follows gitlinks, never moving branch tips.
        # EcoSIM records a GitHub SSH URL for ecosim-agent. Public recursive
        # downloads must also work in Docker without forwarding SSH credentials.
        command(["git", "-c", "url.https://github.com/.insteadOf=git@github.com:",
                 "-C", path, "submodule", "update", "--init", "--recursive", "--depth", "1"])
        verify_checkout(path, source)
        status = git(path, "submodule", "status", "--recursive")
        if any(line.startswith(("-", "+", "U")) for line in status.splitlines()):
            raise RuntimeError(f"Incomplete or mismatched recursive submodules in {path}")
    ats = sources / "amanzi/src/physics/ats"
    verify_checkout(ats, case["sources"]["ats"])
    return {name: {"commit": git(path, "rev-parse", "HEAD"),
                   "origin": git(path, "remote", "get-url", "origin"),
                   "recursive_submodules": git(path, "submodule", "status", "--recursive")}
            for name, path in (("amanzi", sources / "amanzi"), ("ats", ats),
                               ("ecosim", sources / "ecosim"))}


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
    body = (f"<h1>{esc(case['name'])}</h1><p>Status: <b>{esc(report['status'])}</b></p>"
            f"<p>{esc(case['description'])}</p><p><a href='report.json'>Full run record</a> · "
            "<a href='model.stdout'>Model log</a> · <a href='verification.json'>Comparison</a></p>")
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
