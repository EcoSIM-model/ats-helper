#!/usr/bin/env python3
"""Launcher of the ATS-EcoSIM pairs tooling; see tools/README.md or `ats_pairs.py --help`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ats_ecosim_pairs.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
