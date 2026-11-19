"""Check source pinning and case integrity without compiling dependencies."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reproduce_case", ROOT / "docker/reproduce_case.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ats-reproduce-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_bundled_case_integrity_and_changed_input_rejection(self):
        directory = self.base / "200day-adaptive"
        shutil.copytree(ROOT / "cases/200day-adaptive", directory)
        case = runner.load_case(directory)
        self.assertEqual(case["runtime"]["accepted_steps"], 4811)
        with (directory / "data/ecosim_freeze_thaw_dts.h5").open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            runner.load_case(directory)

    def test_rejects_case_paths_outside_bundle(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            runner.case_file(self.base, "../other-input.xml")

    def test_second_builder_cannot_use_an_active_case_volume(self):
        with (self.base / ".reproduce.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.dict(os.environ, ATS_BASE=str(self.base)):
                with self.assertRaisesRegex(RuntimeError, "Another reproduction"):
                    runner.reproduce("200day-adaptive")
        self.assertFalse((self.base / "case-lock.json").exists())

    def test_log_parser_retains_retry_cycles(self):
        text = "\n".join([
            "ATS | Cycle = 0,  Time [days] = 0,  dt [days] = 0.04166666666666",
            "ATS | Cycle = 0,  Time [days] = 0,  dt [days] = 2.083333333e-2",
            "ATS | Cycle = 1,  Time [days] = 2.083333333e-2,  dt [days] = 0.02"])
        rows = runner.cycle_records(text)
        self.assertEqual([row[0] for row in rows], [0, 0, 1])
        self.assertAlmostEqual(rows[1][2], .02083333333)

    @unittest.skipUnless(importlib.util.find_spec("h5py"), "h5py is provided by the Docker image")
    def test_checkpoint_difference_and_wrong_time_are_rejected(self):
        import h5py
        directory = ROOT / "cases/200day-adaptive"
        case = runner.load_case(directory)
        run = self.base / "run"
        run.mkdir()
        checkpoint = run / "checkpoint_final.h5"
        shutil.copy2(directory / case["reference_checkpoint"], checkpoint)
        (run / "model.stdout").write_text("\n".join(
            f"ATS | Cycle = {i},  Time [days] = 0,  dt [days] = 0.04"
            for i in range(case["runtime"]["accepted_steps"])))
        result = runner.verify_results(directory, case, run)
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(len(result["datasets"]), 168)
        with h5py.File(checkpoint, "r+") as f:
            f["temperature.cell.0"][0, 0] += 1
            f.attrs["time"] = 86400.
        result = runner.verify_results(directory, case, run)
        self.assertFalse(result["passed"])
        self.assertIn("Checkpoint values differ: temperature.cell.0", result["issues"])
        self.assertTrue(any("Final time differs" in issue for issue in result["issues"]))

    def git(self, path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def repo(self, name):
        path = self.base / name
        path.mkdir()
        self.git(path, "init")
        (path / "tracked.txt").write_text("original\n")
        self.commit(path)
        return path

    def commit(self, path):
        self.git(path, "add", ".")
        self.git(path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                 "commit", "-m", "Fixture")

    def test_recursive_pins_and_protection_of_existing_edits(self):
        ats = self.repo("ats-origin")
        amanzi = self.repo("amanzi-origin")
        nested = self.repo("nested-origin")
        ecosim = self.repo("ecosim-origin")
        for parent, child, destination in ((amanzi, ats, "src/physics/ats"),
                                           (ecosim, nested, "nested")):
            self.git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(child), destination)
            self.commit(parent)
        # Amanzi and ATS need a reachable tag for version derivation.
        for tagged in (ats, amanzi):
            self.git(tagged, "tag", "v1")
        sources = {name: {"url": str(path), "commit": self.git(path, "rev-parse", "HEAD")}
                   for name, path in (("amanzi", amanzi), ("ats", ats), ("ecosim", ecosim))}
        # Advance the origin after locking; the runner must still select the pin.
        (ecosim / "tracked.txt").write_text("new branch tip\n")
        self.commit(ecosim)
        work = self.base / "case-volume"
        work.mkdir()
        with patch.dict(os.environ, GIT_ALLOW_PROTOCOL="file"):
            result = runner.prepare_sources(work, {"sources": sources})
            self.assertEqual(result["ecosim"]["commit"], sources["ecosim"]["commit"])
            self.assertTrue((work / "sources/ecosim/nested/tracked.txt").exists())
            self.assertTrue((work / "sources/amanzi/src/physics/ats/tracked.txt").exists())
            runner.prepare_sources(work, {"sources": sources})
            edited = work / "sources/ecosim/tracked.txt"
            edited.write_text("local changes\n")
            with self.assertRaisesRegex(RuntimeError, "Local changes"):
                runner.prepare_sources(work, {"sources": sources})
            self.assertEqual(edited.read_text(), "local changes\n")

    def shallow_fork_clone(self):
        """A depth-1 clone of a fork that, like a GitHub fork, carries no tags."""
        fork = self.repo("fork")
        (fork / "tracked.txt").write_text("second\n")
        self.commit(fork)
        clone = self.base / "shallow"
        subprocess.run(["git", "clone", "--depth", "1", f"file://{fork}", str(clone)],
                       check=True, capture_output=True)
        self.assertEqual(self.git(clone, "rev-parse", "--is-shallow-repository"), "true")
        self.assertFalse(runner.describable(clone))
        return fork, clone

    def test_version_history_fetched_from_upstream_tags(self):
        fork, clone = self.shallow_fork_clone()
        upstream = self.base / "upstream"
        subprocess.run(["git", "clone", str(fork), str(upstream)], check=True, capture_output=True)
        self.git(upstream, "tag", "amanzi-1.7")
        head = self.git(clone, "rev-parse", "HEAD")
        with patch.dict(runner.VERSION_TAG_SOURCES, {"amanzi": str(upstream)}):
            runner.ensure_version_history(clone, "amanzi")
        self.assertTrue(runner.describable(clone))
        self.assertEqual(self.git(clone, "rev-parse", "HEAD"), head)
        self.assertEqual(self.git(clone, "rev-parse", "--is-shallow-repository"), "false")
        self.assertEqual(self.git(clone, "status", "--porcelain", "--untracked-files=normal"), "")
        runner.ensure_version_history(clone, "amanzi")  # already describable: no-op

    def test_version_history_without_any_tag_is_reported(self):
        _, clone = self.shallow_fork_clone()
        with patch.dict(runner.VERSION_TAG_SOURCES, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "No reachable tag for amanzi"):
                runner.ensure_version_history(clone, "amanzi")


if __name__ == "__main__":
    unittest.main()
