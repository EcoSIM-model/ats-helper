"""Jobs of a case for the runner: where each backend's input, run directory and summary file are.

  standalone   pairs/<test>/standalone.namelist  run in pairs/<test>/standalone/   pairs/standalone_summary.txt
  ats          pairs/<test>/ats_match.xml        run in pairs/<test>/ats/          pairs/ats_summary.txt

Both are written by the `pair` command.
"""

import re

from . import render
from .runner import Job

DEFAULT_STANDALONE_EXE = "/ats-build/install/tpls/ecosim/bin/ecosim.f90.x"
BACKENDS = ("standalone", "ats")


def summary_file(case, backend):
    return case.pairs_dir / f"{backend}_summary.txt"


def build_jobs(case, backend, only=None, ats_exe="ats", np=1, standalone_exe=DEFAULT_STANDALONE_EXE, require_inputs=True):
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    jobs = []
    for test in case.tests(only):
        pair = case.pair_dir(test.name)
        if backend == "ats":
            xml = pair / "ats_match.xml"
            if not xml.is_file():
                if require_inputs:
                    raise FileNotFoundError(f"{xml} not found; write it with the 'pair' command first")
                continue
            cmd = ["mpirun", "-np", str(np), ats_exe, f"--xml_file={xml}"] if np > 1 else [ats_exe, f"--xml_file={xml}"]
            jobs.append(Job(test.name, "ats", cmd, pair / "ats", end_days=render.end_time_days(xml.read_text())))
        else:
            namelist = pair / "standalone.namelist"
            if not namelist.is_file():
                if require_inputs:
                    raise FileNotFoundError(f"{namelist} not found; write it with the 'pair' command first")
                continue
            meta = case.meta(test.name)
            if meta and meta.get("kind") == "microbe":      # the namelist's stop_n counts from 1 January of the start year
                hours = int(meta["ndays"]) * 24
            else:
                hours = int(re.search(r"stop_n\s*=\s*(\d+)", namelist.read_text()).group(1)) * 24
            jobs.append(Job(test.name, "standalone", [standalone_exe, "../standalone.namelist"],
                            pair / "standalone", expected_hours=hours))
    return jobs
