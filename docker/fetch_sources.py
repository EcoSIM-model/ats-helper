#!/usr/bin/env python3
"""Put the pinned Amanzi, ATS and EcoSIM sources in repos/, where the manual build and the tools look.

The sources are a named set of the repo-level sources.json (default: its "default" set, or the set a case
names with --case). The set gives the URL and the full commit of each; the ATS commit is fetched from its
own URL because Amanzi's recorded ATS commit is another one (override_amanzi_gitlink).

    python3 docker/fetch_sources.py                    # the default set, into repos/amanzi and repos/ecosim
    python3 docker/fetch_sources.py --case pft_tests   # the set that case uses
    python3 docker/fetch_sources.py --set NAME --dest DIR

If repos/amanzi and repos/ecosim both exist they are never changed: the script only reports whether each is
at the pinned commit (a checkout of your own revisions is fine). A partial state (only one of them) is an
error. Needs only git, network access and Python; run it on the host or in the container.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reproduce_case", ROOT / "docker/reproduce_case.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

ATS_PATH = rc.ATS_PATH


def head(path):
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def populated(path):
    return (Path(path) / ".git").exists()


def trust(path):
    """Git refuses a checkout owned by another user (a bind mount): the build script trusts them the same way."""
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(path)],
                   capture_output=True)


def report(dest, sources):
    """compare each existing checkout with its pin; returns the number that differ"""
    differ = 0
    for name, path in (("amanzi", dest / "amanzi"), ("ats", dest / "amanzi" / ATS_PATH), ("ecosim", dest / "ecosim")):
        trust(path)
        found, pinned = head(path), sources[name]["commit"]
        if found == pinned:
            print(f"  {name:<7} {path}: at the pinned commit {pinned[:8]}")
        else:
            differ += 1
            print(f"  {name:<7} {path}: at {(found or 'unknown')[:8]}, the pin is {pinned[:8]} ({sources[name]['url']})")
    return differ


def fetch(dest, sources):
    """clone the sources into dest unless they are there; returns the exit status"""
    amanzi, ecosim = dest / "amanzi", dest / "ecosim"
    have = [populated(amanzi), populated(ecosim)]
    if all(have):
        print(f"Existing checkouts in {dest} are left as they are:")
        differ = report(dest, sources)
        if differ:
            print("They are not the pinned sources; results (and the PFT file the tools read) may differ from "
                  "the locked cases. To use the pins, move these folders away and run this again.")
        return 0
    if any(have):
        print(f"error: only one of {amanzi} and {ecosim} exists. Move it away or complete the other by hand, "
              "then run this again.", file=sys.stderr)
        return 2
    print(f"Fetching the pinned sources into {dest} ...")
    try:
        rc.prepare_sources(dest.parent, {"sources": sources}, dest)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report(dest, sources)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", dest="set_name", help="name of the source set (default: the file's default)")
    parser.add_argument("--case", help="use the sources of this case (cases/<case>/case.json)")
    parser.add_argument("--dest", default=str(ROOT / "repos"), help="folder that gets amanzi/ and ecosim/ (default: repos)")
    parser.add_argument("--sources-file", default=str(rc.SOURCES_FILE), help="the sets file (default: sources.json)")
    args = parser.parse_args(argv)
    try:
        if args.case:
            if args.set_name:
                parser.error("use --set or --case, not both")
            raw = json.loads((ROOT / "cases" / args.case / "case.json").read_text())["sources"]
            sources = rc.resolve_sources(raw, args.sources_file)
        else:
            sets, default = rc.load_source_sets(args.sources_file)
            name = args.set_name or default
            if name not in sets:
                raise ValueError(f"Unknown source set {name!r}; {args.sources_file} defines {sorted(sets)}")
            sources = sets[name]
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return fetch(Path(args.dest).resolve(), sources)


if __name__ == "__main__":
    sys.exit(main())
