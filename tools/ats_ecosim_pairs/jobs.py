"""Jobs of a suite for the runner: where each backend's input, run directory and summary file are.

  ats           input/<test>.xml                  run in <suite>/<test>/             suite_summary.txt
  ats-matched   pairs/<test>/ats_match.xml        run in pairs/<test>/ats/           pairs/ats_match_summary.txt
  standalone    pairs/<test>/standalone.namelist  run in pairs/<test>/standalone/    pairs/standalone_summary.txt

The manifest's baseline (bare ground) is a test of every backend; its pair is optional, so a missing
pair file is skipped for ats-matched and standalone instead of being an error.
"""

import re

from . import render
from .runner import Job

DEFAULT_STANDALONE_EXE = "/ats-build/install/tpls/ecosim/bin/ecosim.f90.x"
BACKENDS = ("ats", "ats-matched", "standalone")


def summary_file(m, backend):
    return {"ats": m.root / "suite_summary.txt",
            "ats-matched": m.pairs_dir / "ats_match_summary.txt",
            "standalone": m.pairs_dir / "standalone_summary.txt"}[backend]


def build_jobs(m, backend, only=None, ats_exe="ats", np=1, standalone_exe=DEFAULT_STANDALONE_EXE, require_inputs=True):
    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {BACKENDS}")
    jobs = []
    for test in m.tests(only):
        if backend in ("ats", "ats-matched"):
            xml = m.inputs_dir / f"{test.name}.xml" if backend == "ats" else m.pairs_dir / test.name / "ats_match.xml"
            if not xml.is_file():
                if require_inputs and not (test.is_baseline and backend != "ats"):
                    hint = "inputs" if backend == "ats" else "pairs"
                    raise FileNotFoundError(f"{xml} not found; write it with the '{hint}' command first")
                continue
            cmd = ["mpirun", "-np", str(np), ats_exe, f"--xml_file={xml}"] if np > 1 else [ats_exe, f"--xml_file={xml}"]
            run_dir = m.root / test.name if backend == "ats" else m.pairs_dir / test.name / "ats"
            jobs.append(Job(test.name, "ats", cmd, run_dir, end_days=render.end_time_days(xml.read_text())))
        else:
            namelist = m.pairs_dir / test.name / "standalone.namelist"
            if not namelist.is_file():
                if require_inputs and not test.is_baseline:
                    raise FileNotFoundError(f"{namelist} not found; write it with the 'pairs' command first")
                continue
            hours = int(re.search(r"stop_n\s*=\s*(\d+)", namelist.read_text()).group(1)) * 24
            jobs.append(Job(test.name, "standalone", [standalone_exe, "../standalone.namelist"],
                            m.pairs_dir / test.name / "standalone", expected_hours=hours))
    return jobs
