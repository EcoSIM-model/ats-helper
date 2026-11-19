"""Tests of tools/ats_ecosim_pairs: XML edits, case folders, parameter reading, pair generation, run classification. No model is run."""
import importlib.util
import json
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from ats_ecosim_pairs import case as case_mod, jobs, render, runner, xmledit  # noqa: E402

CASE = ROOT / "cases" / "pft_tests"
ECOSIM = ROOT / "repos" / "dev" / "ecosim"
ATS = ROOT / "repos" / "dev" / "ats"

XML = textwrap.dedent("""\
    <ParameterList name="Main" type="ParameterList">
      <!-- <ParameterList name="PKs"><Parameter name="commented" type="int" value="1"/></ParameterList> -->
      <ParameterList name="PKs" type="ParameterList">
        <ParameterList name="eco" type="ParameterList">
          <Parameter name="engine" type="string" value="EcoSIM"/>
          <Parameter name="count" type="int" value="2"/>
          <Parameter name="factor" type="double" value="0.5"/>
          <Parameter name="flag" type="bool" value="true"/>
          <Parameter name="file" type="string" value="x"/>
        </ParameterList>
      </ParameterList>
      <ParameterList name="state" type="ParameterList">
        <ParameterList name="a" type="ParameterList"><ParameterList name="f"><ParameterList name="function-constant">
          <Parameter name="value" type="double" value="1.0"/></ParameterList></ParameterList></ParameterList>
        <ParameterList name="b" type="ParameterList"><ParameterList name="f"><ParameterList name="function-constant">
          <Parameter name="value" type="double" value="2.0"/></ParameterList></ParameterList></ParameterList>
      </ParameterList>
    </ParameterList>
    """)


class XmlEditTests(unittest.TestCase):
    def test_set_is_typed_and_leaves_the_rest_of_the_text_alone(self):
        out = xmledit.edit_text(XML, [
            {"set": "PKs/eco/count", "value": 7.0},
            {"set": "PKs/eco/factor", "value": 3},
            {"set": "PKs/eco/flag", "value": False},
            {"set": "PKs/eco/file", "value": "{name}.nc"},
        ], {"name": "case"})
        self.assertIn('name="count" type="int" value="7"', out)
        self.assertIn('name="factor" type="double" value="3.0"', out)
        self.assertIn('name="flag" type="bool" value="false"', out)
        self.assertIn('name="file" type="string" value="case.nc"', out)
        self.assertEqual(out.replace("7", "2").replace("3.0", "0.5").replace("false", "true").replace("case.nc", "x"), XML)

    def test_comments_are_ignored_and_a_path_must_match_exactly_one_parameter(self):
        with self.assertRaisesRegex(xmledit.EditError, "found 0"):
            xmledit.edit_text(XML, [{"set": "PKs/commented", "value": 1}])
        with self.assertRaisesRegex(xmledit.EditError, "found 2"):
            xmledit.edit_text(XML, [{"set": "state/**/function-constant/value", "value": 1.0}])
        out = xmledit.edit_text(XML, [{"set": "state/b/**/function-constant/value", "value": 9}])
        self.assertIn('value="9.0"', out)
        self.assertEqual(out.count('value="1.0"'), 1)

    def test_add_after_keeps_indentation_and_order_and_refuses_duplicates(self):
        out = xmledit.edit_text(XML, [{"add_after": "PKs/eco/engine", "params": [
            {"name": "first", "type": "bool", "value": False}, {"name": "second", "type": "int", "value": 3}]}])
        self.assertIn('      <Parameter name="engine" type="string" value="EcoSIM"/>\n'
                      '      <Parameter name="first" type="bool" value="false"/>\n'
                      '      <Parameter name="second" type="int" value="3"/>\n', out)
        with self.assertRaisesRegex(xmledit.EditError, "already exists"):
            xmledit.edit_text(XML, [{"add_after": "PKs/eco/engine", "params": [{"name": "count", "type": "int", "value": 1}]}])

    def test_paths_relative_to_the_run_directory_and_their_length_limit(self):
        edit = {"set": "PKs/eco/file", "value": "{p}", "relative_to_run_dir": True, "max_length": 20}
        out = xmledit.edit_text(XML, [edit], {"p": "/w/data/x.nc"}, "/w/run/a")
        self.assertIn('value="../../data/x.nc"', out)
        with self.assertRaisesRegex(xmledit.EditError, "longer than 20"):
            xmledit.edit_text(XML, [edit], {"p": "/w/" + "d" * 30 + "/x.nc"}, "/w/run/a")

    def test_unknown_variable_and_edit_are_reported(self):
        with self.assertRaisesRegex(xmledit.EditError, "unknown variable"):
            xmledit.edit_text(XML, [{"set": "PKs/eco/file", "value": "{nope}"}], {"a": 1})
        with self.assertRaisesRegex(xmledit.EditError, "unknown edit"):
            xmledit.edit_text(XML, [{"remove": "PKs/eco/file"}])


class CaseTests(unittest.TestCase):
    def folder(self, tmp, names=("b", "a")):
        for n in names:
            (Path(tmp) / f"{n}.xml").write_text("<ParameterList/>")
        return Path(tmp)

    def test_every_xml_of_the_folder_is_a_test_in_file_name_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = case_mod.load(self.folder(tmp, ("b", "a", "c")))
            self.assertEqual([t.name for t in c.tests()], ["a", "b", "c"])
            self.assertEqual([t.name for t in c.tests(only=["c", "a"])], ["a", "c"])
            self.assertEqual(c.pairs_dir, Path(tmp).resolve() / "pairs")
            with self.assertRaisesRegex(case_mod.CaseError, "unknown tests"):
                c.tests(only=["nope"])

    def test_a_folder_without_inputs_or_a_missing_folder_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(case_mod.CaseError, "no ATS-EcoSIM input"):
                case_mod.load(tmp)
            with self.assertRaisesRegex(case_mod.CaseError, "not found"):
                case_mod.load(Path(tmp) / "missing")

    def test_group_lai_and_bare_flag_come_from_the_pair_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = case_mod.load(self.folder(tmp, ("veg", "bare")))
            self.assertEqual([t.group for t in c.tests()], [None, None])      # before `pair` ran
            for name, meta in (("veg", {"bare": False, "group": "grass", "lai": 3.0}), ("bare", {"bare": True, "group": None, "lai": 0.0})):
                (Path(tmp) / "pairs" / name).mkdir(parents=True)
                (Path(tmp) / "pairs" / name / "pair.json").write_text(json.dumps(meta))
            self.assertEqual([(t.name, t.group, t.axis, t.is_baseline) for t in c.tests()],
                             [("bare", None, ("lai", 0.0), True), ("veg", "grass", ("lai", 3.0), False)])
            self.assertEqual([t.name for t in c.tests(baseline=False)], ["veg"])


class ParameterTests(unittest.TestCase):
    def parameters(self, name):
        root = xmledit.parse_xml((CASE / f"{name}.xml").read_text()).getroot()
        return render.ats_parameters(root, CASE)

    @unittest.skipUnless((CASE / "pft_grass_lai1.xml").is_file(), "needs the pft_tests case")
    def test_a_vegetated_input_gives_its_canopy_and_files(self):
        p = self.parameters("pft_grass_lai1")
        self.assertTrue(p["prescribed"])
        self.assertEqual((p["vegetation_type"], p["lai"], p["sai"], p["npfts"]), (77, 1.0, 0.1, 1))
        self.assertEqual((p["depth"], p["ndays"]), (10.0, 729.0))
        self.assertTrue(p["forcing"].endswith("cases/pft_tests/data/daymet_TaylorRiverCO_hourly.h5"))
        self.assertEqual(Path(p["engine_file"]).name, "ecosim_pftpar_20261021.nc")

    @unittest.skipUnless((CASE / "bare_ground.xml").is_file(), "needs the pft_tests case")
    def test_a_bare_ground_input_is_not_prescribed(self):
        self.assertFalse(self.parameters("bare_ground")["prescribed"])

    @unittest.skipUnless((CASE / "bare_ground.xml").is_file(), "needs the pft_tests case")
    def test_inputs_that_cannot_be_matched_are_refused_with_the_reason(self):
        text = (CASE / "bare_ground.xml").read_text()
        for label, broken, message in (
                ("a hillslope", text.replace('value="{1, 1, 100}"', 'value="{4, 1, 100}"'), "single column"),
                ("a missing forcing file", text.replace('name="starting year"', 'name="start year"'), "forcing"),
                ("a missing soil", text.replace('name="base_porosity"', 'name="porosity_x"'), "base_porosity")):
            root = xmledit.parse_xml(broken).getroot()
            with self.assertRaisesRegex(case_mod.CaseError, message, msg=label):
                render.ats_parameters(root, CASE)

    def test_end_time(self):
        self.assertEqual(render.end_time_days('name="end time" type="double" value="729.0"/>\n'
                                              'name="end time units" type="string" value="d"/>'), 729.0)
        self.assertEqual(render.end_time_days('name="end time" type="double" value="86400.0"/>'), 1.0)


class RunnerTests(unittest.TestCase):
    def job(self, tmp, backend="ats", **kw):
        return runner.Job("t", backend, ["true"], Path(tmp), **kw)

    def log(self, tmp, text):
        path = Path(tmp) / "out.log"
        path.write_text(text)
        return path

    def test_ats_run_passes_only_when_it_reaches_the_end_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.job(tmp, end_days=10.0)
            step = "ATS | Cycle = 5,  Time [days] = {t},  dt [days] = 0.04\n"
            self.assertEqual(runner.classify(job, self.log(tmp, step.format(t=9.96))), ("PASSED", "last cycle 5, t = 9.96 d, dt 0.04 d"))
            status, detail = runner.classify(job, self.log(tmp, step.format(t=3.0)))
            self.assertEqual(status, "FAILED (ended early)")
            self.assertIn("before the end time 10 d", detail)
            self.assertEqual(runner.classify(job, self.log(tmp, ""))[0], "FAILED (ended early)")

    def test_ecosim_abort_with_exit_code_zero_and_nonzero_exit_are_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.job(tmp, end_days=10.0)
            text = ("ATS | Cycle = 5,  Time [days] = 9.99,  dt [days] = 0.04\n"
                    " weird temp 6.6 25 1\n ENDRUN:/x/SnowPhysMod.F90 at line 1117\n")
            status, detail = runner.classify(job, self.log(tmp, text), returncode=0)
            self.assertEqual(status, "FAILED (EcoSIM abort)")
            self.assertIn("ENDRUN", detail)
            ok = "ATS | Cycle = 5,  Time [days] = 9.99,  dt [days] = 0.04\nError: parameter missing in the input\n"
            status, detail = runner.classify(job, self.log(tmp, ok), returncode=1)
            self.assertEqual(status, "FAILED (rc=1)")
            self.assertIn("missing", detail)

    def test_the_hint_of_a_real_missing_parameter_crash_is_the_what_line(self):
        # text of a real ATS run whose input lacked a required parameter (exit code -6)
        text = ("ATS | starting\n"
                "terminate called after throwing an instance of 'Teuchos::Exceptions::InvalidParameterName'\n"
                '  what():  Error!  The parameter "wilting point [Mpa]" does not exist\n'
                "[host:05291] [17] /ats-build/install/amanzi/lib/libats_executable.so(_ZN3ATS11Coordinator+0x3b8)\n"
                "[host:05291] *** End of error message ***\n")
        with tempfile.TemporaryDirectory() as tmp:
            hint = runner.failure_hint(self.log(tmp, text))
        self.assertIn('The parameter "wilting point [Mpa]" does not exist', hint)
        self.assertNotIn("End of error message", hint)

    def test_standalone_run_needs_every_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.job(tmp, backend="standalone", expected_hours=3)
            line = "HistFileMod.F90::hist_htapes_wrapup : Writing current time sample to local history file\n"
            self.assertEqual(runner.classify(job, self.log(tmp, line * 3))[0], "PASSED")
            self.assertEqual(runner.classify(job, self.log(tmp, line * 2))[0], "FAILED (ended early)")

    def fake(self, tmp, body):
        path = Path(tmp) / "fake_ats.py"
        path.write_text("#!/usr/bin/env python3\nimport sys, time\n" + textwrap.dedent(body))
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_watchdog_stops_a_collapsing_timestep_and_notes_an_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            collapse = self.fake(tmp, """
                print("ATS | Cycle = 9,  Time [days] = 1.0,  dt [days] = 1e-9", flush=True)
                time.sleep(60)
            """)
            job = runner.Job("collapse", "ats", [collapse], Path(tmp) / "a", end_days=10.0)
            name, status, detail = runner.run_one(job, min_dt_days=1e-5, max_wall_s=30)
            self.assertTrue(status.startswith("STOPPED (timestep below"), status)
            self.assertIn("cycle 9", detail)
            self.assertIn("STOPPED", (Path(tmp) / "a" / "status.txt").read_text())
            abort = self.fake(tmp, """
                print("ATS | Cycle = 9,  Time [days] = 2.0,  dt [days] = 0.04", flush=True)
                print(" ENDRUN:/x/Foo.F90 at line 3", flush=True)
            """)
            job = runner.Job("abort", "ats", [abort], Path(tmp) / "b", end_days=10.0)
            self.assertEqual(runner.run_one(job)[1], "FAILED (EcoSIM abort)")
            done = self.fake(tmp, """
                print("ATS | Cycle = 9,  Time [days] = 9.99,  dt [days] = 0.04", flush=True)
            """)
            job = runner.Job("done", "ats", [done], Path(tmp) / "c", end_days=10.0)
            self.assertEqual(runner.run_one(job)[1], "PASSED")

    def test_a_job_that_cannot_launch_is_a_failure_and_the_others_still_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self.fake(tmp, """
                print("ATS | Cycle = 9,  Time [days] = 9.99,  dt [days] = 0.04", flush=True)
            """)
            bad = runner.Job("bad", "ats", [str(Path(tmp) / "no_such_exe")], Path(tmp) / "a", end_days=10.0)
            good = runner.Job("good", "ats", [done], Path(tmp) / "b", end_days=10.0)
            results = runner.run_jobs([bad, good], parallel=2)
            self.assertEqual([r[0] for r in results], ["bad", "good"])
            self.assertEqual(results[0][1], "FAILED (runner error)")
            self.assertIn("no_such_exe", results[0][2])
            self.assertIn("runner error", (Path(tmp) / "a" / "status.txt").read_text())
            self.assertEqual(results[1][1], "PASSED")

    def test_log_open_retries_when_the_run_directory_is_briefly_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, real_open, calls = Path(tmp) / "r", open, []

            def flaky_open(path, mode="r", *a, **kw):
                calls.append(path)
                if len(calls) < 3:
                    raise FileNotFoundError(path)
                return real_open(path, mode, *a, **kw)
            with mock.patch("builtins.open", flaky_open):
                with runner.open_log(run_dir, pause_s=0) as f:
                    f.write(b"x")
            self.assertEqual(len(calls), 3)
            self.assertEqual((run_dir / "out.log").read_bytes(), b"x")
            with mock.patch("builtins.open", side_effect=FileNotFoundError("gone")):
                with self.assertRaises(FileNotFoundError):
                    runner.open_log(run_dir, attempts=2, pause_s=0)

    def test_summary_file_is_written_only_for_real_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.txt"
            runner.print_summary([("a", "skipped", "")], path)
            self.assertFalse(path.exists())
            runner.print_summary([("a", "PASSED", "ok"), ("b", "FAILED (rc=1)", "bad")], path)
            self.assertIn("1 passed, 1 did not complete", path.read_text())


class JobTests(unittest.TestCase):
    def test_backends_use_the_documented_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            for n in ("a", "b"):
                (Path(tmp) / f"{n}.xml").write_text("<ParameterList/>")
            c = case_mod.load(tmp)
            self.assertEqual(jobs.build_jobs(c, "ats", require_inputs=False), [])
            with self.assertRaisesRegex(FileNotFoundError, "'pair' command"):
                jobs.build_jobs(c, "standalone")
            pair = c.pair_dir("a")
            (pair).mkdir(parents=True)
            (pair / "standalone.namelist").write_text("stop_n = 729\n")
            (pair / "ats_match.xml").write_text('name="end time" type="double" value="729.0"/>\n'
                                                'name="end time units" type="string" value="d"/>')
            sa = jobs.build_jobs(c, "standalone", only=["a"])[0]
            self.assertEqual((sa.run_dir, sa.expected_hours), (pair / "standalone", 729 * 24))
            ats = jobs.build_jobs(c, "ats", only=["a"])[0]
            self.assertEqual((ats.run_dir, ats.end_days), (pair / "ats", 729.0))
            self.assertEqual(jobs.summary_file(c, "ats"), c.pairs_dir / "ats_summary.txt")
            with self.assertRaisesRegex(case_mod.CaseError, "unknown tests"):
                jobs.build_jobs(c, "standalone", only=["not_a_test"])


@unittest.skipUnless(all(importlib.util.find_spec(x) for x in ("numpy", "h5py", "netCDF4")),
                     "pairs need numpy, h5py and netCDF4")
class BareNamelistTests(unittest.TestCase):
    def test_ats_sky_longwave_matches_the_ats_formula(self):
        import numpy as np
        from ats_ecosim_pairs import pairs
        t, e = 273.15, 600.0
        expected = 1.08 * (1.0 - np.exp(-(e / 100.0) ** (t / 2016.0))) * 5.670374419e-8 * t ** 4
        self.assertAlmostEqual(float(pairs.ats_sky_longwave(t, e)), float(expected), places=9)
        self.assertTrue(150.0 < float(pairs.ats_sky_longwave(t, e)) < 330.0)         # a plausible W m-2

    def test_plant_free_namelist(self):
        from ats_ecosim_pairs import pairs
        with tempfile.TemporaryDirectory() as tmp:
            hist = ["PSI_vr", "RootH2OUptake_vr"]
            pairs.write_namelist(Path(tmp) / "bare", "bare", "pft.nc", 729, (1981, 1982), "ghg.nc", hist, plants=False)
            pairs.write_namelist(Path(tmp) / "veg", "veg", "pft.nc", 729, (1981, 1982), "ghg.nc", hist)
            bare, veg = [(Path(tmp) / n).read_text() for n in ("bare", "veg")]
            self.assertIn("plant_model     = .false.", bare)
            self.assertIn("ldo_sp_mode     = .false.", bare)
            self.assertNotIn("sp_phenol_in       =", bare)
            self.assertIn("hist_fincl1 = 'PSI_vr'\n", bare)             # no plant fields
            self.assertIn("plant_model     = .true.", veg)
            self.assertIn("sp_phenol_in       = '../sp_phenol.nc'", veg)
            self.assertIn("'RootH2OUptake_vr'", veg)


@unittest.skipUnless(all(importlib.util.find_spec(x) for x in ("numpy", "h5py", "netCDF4")) and ECOSIM.is_dir()
                     and ATS.is_dir() and (CASE / "pft_grass_lai1.xml").is_file(),
                     "needs numpy, h5py, netCDF4, the dev EcoSIM and ATS trees and the pft_tests case")
class PairGenerationTests(unittest.TestCase):
    """`pair` on a copy of the pft_tests case: one vegetated input, the bare-ground input and any broken one"""

    def case_copy(self, tmp, names=("pft_grass_lai1", "bare_ground")):
        import shutil
        root = Path(tmp) / "mycase"
        root.mkdir()
        shutil.copytree(CASE / "data", root / "data")
        for n in names:
            shutil.copy2(CASE / f"{n}.xml", root / f"{n}.xml")
        return case_mod.load(root)

    def generate(self, c, **options):
        import contextlib, io
        from ats_ecosim_pairs import pairs
        with contextlib.redirect_stdout(io.StringIO()):
            return pairs.generate_pairs(c, options, ECOSIM, ATS, only=options.pop("only", None))

    def test_pairs_are_written_for_every_input_with_the_matched_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.case_copy(tmp)
            self.generate(c)
            veg, bare = c.pair_dir("pft_grass_lai1"), c.pair_dir("bare_ground")
            for f in ("ats_match.xml", "column.exo", "grid.nc", "clim.nc", "sp_phenol.nc", "standalone.namelist", "pair.json"):
                self.assertTrue((veg / f).is_file(), f)
            self.assertFalse((bare / "sp_phenol.nc").exists())                 # bare ground: no phenology file
            self.assertTrue((c.pairs_dir / "README.md").is_file())
            meta = json.loads((veg / "pair.json").read_text())
            self.assertEqual((meta["pft"], meta["group"], meta["row"], meta["lai"], meta["population"]),
                             ("gr3s43", "grass", 77, 1.0, 300.0))
            self.assertEqual((meta["start_temperature"], meta["bottom_temperature"]), (274.15, 274.43))
            self.assertEqual(meta["input_start_temperature"], 260.0)           # what the input had
            xml = (veg / "ats_match.xml").read_text()
            self.assertIn('value="274.15"', xml)
            self.assertIn('value="274.43"', xml)
            self.assertIn('name="EcoSIM precipitation" type="bool" value="true"', xml)
            self.assertIn("read mesh file", xml)
            # the data file is rebased: relative to pairs/<test>/ats, it resolves to the case's data folder
            import re
            rel = re.search(r'name="file" type="string" value="([^"]*daymet[^"]*)"', xml).group(1)
            self.assertTrue((veg / "ats" / rel).resolve().is_file())
            self.assertIn("'../grid.nc'", (veg / "standalone.namelist").read_text())
            self.assertIn("plant_model     = .false.", (bare / "standalone.namelist").read_text())
            self.assertIn('name="engine input file" type="string" value="Null"', (bare / "ats_match.xml").read_text())
            self.assertTrue(json.loads((bare / "pair.json").read_text())["bare"])

    def test_keeping_the_inputs_own_start_and_overriding_the_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.case_copy(tmp, ("pft_grass_lai1",))
            from ats_ecosim_pairs import pairs
            import contextlib, io
            with contextlib.redirect_stdout(io.StringIO()):
                pairs.generate_pairs(c, {"start_temperature": "xml"}, ECOSIM, ATS, populations={"grass": 50.0})
            meta = json.loads((c.pair_dir("pft_grass_lai1") / "pair.json").read_text())
            self.assertEqual((meta["start_temperature"], meta["population"]), (260.0, 50.0))
            self.assertIn('value="260.00"', (c.pair_dir("pft_grass_lai1") / "ats_match.xml").read_text())
            self.assertEqual(meta["bottom_temperature"], meta["input_bottom_temperature"])

    def test_a_broken_input_stops_everything_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.case_copy(tmp)
            bad = c.root / "zz_hillslope.xml"
            bad.write_text((CASE / "bare_ground.xml").read_text().replace('value="{1, 1, 100}"', 'value="{4, 1, 100}"'))
            with self.assertRaisesRegex(case_mod.CaseError, "zz_hillslope.xml.*single column"):
                self.generate(c)
            self.assertFalse(c.pairs_dir.exists())
            self.generate(c, only=["pft_grass_lai1"])                           # the good one alone works
            self.assertFalse((c.pairs_dir / "README.md").exists())              # a subset does not write the README


@unittest.skipUnless(all(importlib.util.find_spec(x) for x in ("numpy", "h5py", "netCDF4", "matplotlib")),
                     "report needs numpy, h5py, netCDF4 and matplotlib")
class ReportTests(unittest.TestCase):
    def test_grading_alignment_and_snow_seasons(self):
        import numpy as np
        from ats_ecosim_pairs import report
        self.assertEqual(report.grade(1.1, "ratio", 0.25, 0.5), "green")
        self.assertEqual(report.grade(1.4, "ratio", 0.25, 0.5), "amber")
        self.assertEqual(report.grade(2.0, "ratio", 0.25, 0.5), "red")
        self.assertEqual(report.grade(0.95, "min", 0.9, 0.7), "green")
        self.assertEqual(report.grade(float("nan"), "min", 0.9, 0.7), "na")
        self.assertEqual(report.grade(3.0, "max", 2.0, 5.0), "amber")
        # the top soil cell: the thinnest of a graded column (numbered from the top), the last of a uniform one
        self.assertEqual(report.top_cell(np.array([0.05, 0.061, 0.075, 1.8])), 0)
        self.assertEqual(report.top_cell(np.full(100, 0.1)), 99)
        # an ATS series that is the standalone one delayed by one hour is found with shift -1 ... or 0 for itself
        rng = np.random.default_rng(1)
        sa = rng.normal(size=report.HOURS)
        ats = np.full(report.HOURS + 2 * report.PAD + 2, np.nan)
        ats[report.PAD + 1 + 1:report.PAD + 1 + 1 + report.HOURS] = sa        # aligned(shift=+1) returns sa
        shift, _ = report.best_shift([(ats, sa)], "corr")
        self.assertEqual(shift, 1)
        depth = np.zeros(report.HOURS)
        depth[24 * 10:24 * 40] = 0.3
        depth[24 * 20] = 0.5
        seasons = report.snow_seasons(depth)
        self.assertEqual(len(seasons), 1)
        self.assertEqual((seasons[0]["start_day"], seasons[0]["melt_out_day"], round(seasons[0]["peak"], 2)), (10, 40, 0.5))


if __name__ == "__main__":
    unittest.main()
