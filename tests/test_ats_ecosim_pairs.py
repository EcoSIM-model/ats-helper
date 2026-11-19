"""Tests of tools/ats_ecosim_pairs: XML edits, manifest, rendering, run classification. No model is run."""
import importlib.util
import json
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from ats_ecosim_pairs import jobs, manifest, render, runner, xmledit  # noqa: E402

SUITE = ROOT / "cases" / "pft_tests" / "suite.json"

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


class ManifestTests(unittest.TestCase):
    def write(self, tmp, data, templates=("t.xml",)):
        for t in templates:
            (Path(tmp) / t).write_text(XML)
        (Path(tmp) / "suite.json").write_text(json.dumps(data))
        return Path(tmp) / "suite.json"

    def good(self):
        return {"name": "x", "ats": {"template": "t.xml", "forcing": "f.h5", "data_dir": "data"},
                "name_pattern": "t_{group}_{lai}", "sweep": [{"group": "a", "lai": [1, 0.5]}]}

    def test_the_pft_suite_expands_in_order_with_the_baseline_last(self):
        m = manifest.load(SUITE)
        names = [t.name for t in m.tests()]
        self.assertEqual(len(names), 14)
        self.assertEqual(names[0], "pft_deciduous_lai2")
        self.assertEqual(names[-1], "bare_ground")
        self.assertIn("pft_grass_lai0p5", names)
        self.assertEqual([t.axis for t in m.tests(baseline=False)][:2], [("lai", 2), ("lai", 4)])
        self.assertEqual(len(m.tests(baseline=False)), 13)

    def test_unknown_keys_missing_files_and_duplicate_names_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.good()
            self.assertEqual([t.name for t in manifest.load(self.write(tmp, data)).tests()], ["t_a_1", "t_a_0p5"])
            with self.assertRaisesRegex(manifest.ManifestError, "unknown keys"):
                manifest.load(self.write(tmp, {**data, "sweeep": []}))
            with self.assertRaisesRegex(manifest.ManifestError, "template not found"):
                manifest.load(self.write(tmp, {**data, "ats": {**data["ats"], "template": "missing.xml"}}))
            with self.assertRaisesRegex(manifest.ManifestError, "not unique"):
                manifest.load(self.write(tmp, {**data, "name_pattern": "same", "sweep": [{"group": "a", "lai": [1, 2]}]}))
            with self.assertRaisesRegex(manifest.ManifestError, "unknown tests"):
                manifest.load(self.write(tmp, data)).tests(only=["nope"])


@unittest.skipUnless(SUITE.is_file() and (ROOT / "repos/dev/ecosim/input_data").is_dir(), "needs the PFT suite and the dev EcoSIM tree")
class RenderTests(unittest.TestCase):
    def test_rendered_inputs_equal_the_committed_suite_inputs(self):
        m = manifest.load(SUITE)
        checked = 0
        for test in m.tests(only=["pft_grass_lai0p5", "pft_evergreen_lai4", "bare_ground"]):
            current = m.inputs_dir / f"{test.name}.xml"
            if not current.is_file():
                continue
            text = render.render_test(m, test, m.root / test.name, profile="dev-pk")
            self.assertEqual(text, current.read_text(), test.name)
            checked += 1
        self.assertGreater(checked, 0)

    def test_the_dev_profile_changes_only_what_it_says(self):
        m = manifest.load(SUITE)
        test = m.tests(only=["pft_shrub_lai1"])[0]
        plain = render.render_test(m, test, m.root / test.name)
        dev = render.render_test(m, test, m.root / test.name, profile="dev-pk")
        extra = set(dev.splitlines()) - set(plain.splitlines())
        self.assertEqual(len(extra), 3)
        self.assertTrue(any("shared surface layer" in line for line in extra))
        self.assertTrue(any("surface-ecosim_source_linearized" in line for line in extra))
        with self.assertRaisesRegex(manifest.ManifestError, "no profile"):
            render.render_test(m, test, m.root / test.name, profile="nope")

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

    def test_summary_file_is_written_only_for_real_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.txt"
            runner.print_summary([("a", "skipped", "")], path)
            self.assertFalse(path.exists())
            runner.print_summary([("a", "PASSED", "ok"), ("b", "FAILED (rc=1)", "bad")], path)
            self.assertIn("1 passed, 1 did not complete", path.read_text())


@unittest.skipUnless(SUITE.is_file(), "needs the PFT suite")
class JobTests(unittest.TestCase):
    def test_backends_use_the_documented_locations(self):
        m = manifest.load(SUITE)
        ats = jobs.build_jobs(m, "ats", only=["pft_grass_lai1"], require_inputs=False)
        self.assertEqual(ats[0].run_dir, m.root / "pft_grass_lai1")
        matched = jobs.build_jobs(m, "ats-matched", only=["pft_grass_lai1"], require_inputs=False)
        if matched:
            self.assertEqual(matched[0].run_dir, m.pairs_dir / "pft_grass_lai1" / "ats")
        self.assertEqual(jobs.summary_file(m, "standalone"), m.pairs_dir / "standalone_summary.txt")
        with self.assertRaisesRegex(manifest.ManifestError, "unknown tests"):
            jobs.build_jobs(m, "standalone", only=["not_a_test"])
        # the baseline (bare ground) is a test of every backend; its pair files are optional
        bare = jobs.build_jobs(m, "standalone", only=["bare_ground"])
        has_pair = (m.pairs_dir / "bare_ground" / "standalone.namelist").is_file()
        self.assertEqual(len(bare), 1 if has_pair else 0)
        if has_pair:
            self.assertEqual(bare[0].run_dir, m.pairs_dir / "bare_ground" / "standalone")
        self.assertEqual(len(jobs.build_jobs(m, "ats-matched", only=["bare_ground"])),
                         1 if (m.pairs_dir / "bare_ground" / "ats_match.xml").is_file() else 0)


@unittest.skipUnless(all(importlib.util.find_spec(x) for x in ("numpy", "h5py", "netCDF4")),
                     "pairs need numpy, h5py and netCDF4")
class BareNamelistTests(unittest.TestCase):
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
