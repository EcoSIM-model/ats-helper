"""Run model executables with a watchdog and classify how each run ended. Standard library only.

One runner for both models. A job says what to execute, where, and how to tell that it finished:
  ats          the run must reach the end time of its input (read from the step lines of the log);
               stopped if the timestep falls below a limit (a collapsing timestep)
  standalone   the EcoSIM history must have an hourly sample for every hour of the namelist's run
Both are also stopped at a wall-clock or log-size limit. A run passes only if it finished, exited
normally and shows no EcoSIM ENDRUN: EcoSIM stops with exit code 0 when it aborts, so the exit code
alone is not enough. A failed run is noted with the last error text of its log (for example a missing
file or parameter) and the next job is started.
"""

import os
import re
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

CYCLE_RE = re.compile(rb"Cycle = (\d+),\s+Time \[days\] = ([0-9.eE+-]+),\s+dt \[days\] = ([0-9.eE+-]+)")
ERROR_RE = re.compile(rb"Errors::|terminate called|exception|what\(\)|cannot|no such file|not found|"
                      rb"missing|invalid|error|endrun|weird", re.IGNORECASE)
NOT_ERRORS = (b"error(res)", b"l2 error", b"error tolerance", b"enorm",       # solver progress lines
              b"end of error message")                                          # MPI crash banner, no content
HOUR_RE = re.compile(rb"Writing current time sample")


@dataclass
class Job:
    name: str
    backend: str                  # "ats" or "standalone"
    cmd: list
    run_dir: Path
    end_days: float = None        # ats: end time of the input [d]
    expected_hours: int = None    # standalone: hours of the run


def tail_bytes(path, n):
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - n))
        return f.read()


def last_cycle(path, n=2_000_000):
    """(cycle, time [d], dt [d]) of the last step line in the log, or None"""
    found = CYCLE_RE.findall(tail_bytes(path, n))
    return tuple(x.decode() for x in found[-1]) if found else None


def failure_hint(path):
    """the last error-looking lines of a failed run (a missing file or parameter, an exception)"""
    lines = [ln.strip() for ln in tail_bytes(path, 300_000).splitlines()
             if ERROR_RE.search(ln) and not any(k in ln.lower() for k in NOT_ERRORS)]
    return " | ".join(ln.decode(errors="replace") for ln in lines[-2:])[:400]


def ecosim_abort(path):
    """EcoSIM's endrun stops the program with exit code 0; find it in the log"""
    for ln in tail_bytes(path, 300_000).splitlines():
        if ln.lstrip().startswith(b"ENDRUN"):
            return ln.strip().decode(errors="replace")
    return None


def hours_written(log):
    """hourly history samples written so far"""
    marker = b"Writing current time sample"
    n, tail = 0, b""
    with open(log, "rb") as f:
        while chunk := f.read(8 << 20):
            data = tail + chunk
            n += len(HOUR_RE.findall(data))
            tail = data[-len(marker):]
    return n


def classify(job, log, returncode=0):
    """(status, detail) of a finished run"""
    if job.backend == "ats":
        lc = last_cycle(log)
        where = f"last cycle {lc[0]}, t = {float(lc[1]):.2f} d, dt {float(lc[2]):.3g} d" if lc else "no step completed"
        early = not lc or float(lc[1]) < job.end_days - 0.1
        early_text = f"{where}, before the end time {job.end_days:g} d"
    else:
        hours = hours_written(log)
        where = f"{hours} of {job.expected_hours} hours written"
        early = hours < job.expected_hours
        early_text = where
    if ecosim_abort(log):
        return "FAILED (EcoSIM abort)", f"{where}; {failure_hint(log)}"
    if returncode != 0:
        return f"FAILED (rc={returncode})", f"{where}; {failure_hint(log) or 'no error text found, see out.log'}"
    if early:
        return "FAILED (ended early)", f"{early_text}; {failure_hint(log)}"
    return "PASSED", where


def run_one(job, min_dt_days=1e-5, max_wall_s=45 * 60, max_log_gb=15):
    """Run one job with the watchdog; returns (name, status, detail)."""
    job.run_dir.mkdir(parents=True, exist_ok=True)
    log = job.run_dir / "out.log"
    print(f"--- {job.name}: start ({' '.join(map(str, job.cmd))}; run dir {job.run_dir})", flush=True)
    t0 = time.time()
    stopped = None
    with open(log, "wb") as out:
        proc = subprocess.Popen(job.cmd, cwd=job.run_dir, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        while proc.poll() is None:
            time.sleep(2)
            lc = last_cycle(log) if job.backend == "ats" else None
            if lc and float(lc[2]) < min_dt_days:
                stopped = (f"STOPPED (timestep below {min_dt_days:g} d)",
                           f"dt {float(lc[2]):.3g} d at cycle {lc[0]}, t = {float(lc[1]):.2f} d")
            elif time.time() - t0 > max_wall_s:
                stopped = ("STOPPED (wall-clock limit)", f"after {max_wall_s / 60:.0f} min")
            elif log.stat().st_size > max_log_gb * 1e9:
                stopped = ("STOPPED (log size limit)", f"over {max_log_gb:g} GB")
            if stopped:
                os.killpg(proc.pid, signal.SIGKILL)
                break
        proc.wait()
    wall = time.time() - t0
    if stopped:
        if job.backend == "ats":
            lc = last_cycle(log)
            where = f"last cycle {lc[0]}, t = {float(lc[1]):.2f} d, dt {float(lc[2]):.3g} d" if lc else "no step completed"
        else:
            where = f"{hours_written(log)} of {job.expected_hours} hours written"
        status, detail = stopped[0], f"{stopped[1]}; {where}"
    else:
        status, detail = classify(job, log, proc.returncode)
    (job.run_dir / "status.txt").write_text(f"{status} | wall {wall:.0f} s | {detail}\n")
    print(f"--- {job.name}: {status} after {wall:.0f} s; {detail}", flush=True)
    return job.name, status, detail


def run_jobs(jobs, parallel=1, dry_run=False, **limits):
    if dry_run:
        for job in jobs:
            print(f"--- {job.name} (skipped)")
        return [(job.name, "skipped", "") for job in jobs]
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        return list(pool.map(lambda job: run_one(job, **limits), jobs))


def summarize_jobs(jobs):
    """re-evaluate finished runs from their logs, without running anything; rewrites status.txt"""
    results = []
    for job in jobs:
        log = job.run_dir / "out.log"
        if not log.is_file():
            continue
        status, detail = classify(job, log)
        (job.run_dir / "status.txt").write_text(f"{status} | re-evaluated from out.log | {detail}\n")
        results.append((job.name, status, detail))
    return results


def print_summary(results, summary_file=None):
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    lines = []
    for name, status, detail in results:
        mark = "✓" if status == "PASSED" else ("–" if status == "skipped" else "✗")
        lines.append(f"  {mark}  {name:<26} {status}" + (f"  [{detail}]" if status != "skipped" and detail else ""))
    print("\n".join(lines))
    n_passed = sum(1 for _, s, _ in results if s == "PASSED")
    n_failed = sum(1 for _, s, _ in results if s not in ("PASSED", "skipped"))
    if any(s != "skipped" for _, s, _ in results):
        print(f"\n  {n_passed} passed, {n_failed} did not complete")
        if summary_file:
            Path(summary_file).write_text("\n".join(lines) + f"\n\n{n_passed} passed, {n_failed} did not complete\n")
