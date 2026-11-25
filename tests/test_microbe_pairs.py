"""Tests of the microbe pairs (tools/ats_ecosim_pairs/microbe.py, report_microbe.py): reading the inputs, the layers,
calendar and forcing of the standalone, its namelist, job sample counts and the biomass index decoding. No model is run."""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

BOX = ROOT / "cases" / "microbe_bgc_box"
COLUMN = ROOT / "cases" / "microbe_bgc_column"
BOX_XML = BOX / "single_cell.boxsbgc_equiv.xml"
COLUMN_XML = COLUMN / "uniform_mesh.microbe_bgc_pheno_deciduous_year.xml"
HAVE_PKGS = all(importlib.util.find_spec(x) for x in ("numpy", "h5py", "netCDF4"))
ECOSIM = ROOT / "repos" / "ecosim"
HAVE_CASES = BOX_XML.is_file() and COLUMN_XML.is_file() and (COLUMN / "data" / "daymet_TaylorRiverCO.h5").is_file()

if HAVE_PKGS:
    import numpy as np
    from ats_ecosim_pairs import case as case_mod, jobs, microbe, xmledit


@unittest.skipUnless(HAVE_PKGS and HAVE_CASES, "needs numpy, h5py, netCDF4 and the microbe case folders")
class ReadInputTests(unittest.TestCase):
    def test_the_box_is_a_two_cell_saturated_incubation_with_constant_forcing(self):
        p = microbe.read_input(BOX_XML, BOX)
        self.assertEqual((p["ncells"], p["depth"], p["ndays"]), (2, 1.0, 30.0))
        self.assertEqual(len(p["components"]), 13)
        self.assertEqual(p["n_components"], 13)
        self.assertIsNone(p["head"])                         # a constant pressure: atmospheric means saturated
        self.assertEqual(p["pressure"], 101325.0)
        self.assertEqual((p["start_doy"], p["start_year"]), (181, 1995))
        self.assertFalse(p["prescribed"])
        self.assertFalse(p["surface_energy_coupled"])
        self.assertTrue(all(f[0] == "constant" for f in p["forcing"].values()))
        self.assertAlmostEqual(p["initial_mole_fractions"]["NO3"], 5e-5)

    def test_the_column_has_a_hundred_layers_a_daily_table_and_computed_longwave(self):
        p = microbe.read_input(COLUMN_XML, COLUMN)
        self.assertEqual((p["ncells"], p["depth"], p["ndays"]), (100, 10.0, 365.0))
        self.assertEqual(len(p["components"]), 17)
        self.assertEqual(p["head"], -2.0)
        self.assertTrue(p["prescribed"])
        self.assertTrue(p["surface_energy_coupled"])
        self.assertEqual(p["forcing"]["air_temperature"][0], "table")
        self.assertEqual(p["longwave"][0], "ats")
        self.assertTrue(Path(p["forcing"]["air_temperature"][1]).is_file())

    def test_an_input_without_the_microbial_model_is_not_a_microbe_input(self):
        pft = ROOT / "cases" / "pft_tests" / "bare_ground.xml"
        if not pft.is_file():
            self.skipTest("needs the pft_tests case")
        root = xmledit.parse_xml(pft.read_text()).getroot()
        self.assertFalse(microbe.is_microbe_input(root))
        self.assertTrue(microbe.is_microbe_input(xmledit.parse_xml(BOX_XML.read_text()).getroot()))
        with self.assertRaisesRegex(case_mod.CaseError, "microbe model"):
            microbe.read_input(pft, pft.parent)

    def test_a_data_file_missing_next_to_the_input_is_looked_up_in_the_case_data_folder(self):
        found = microbe.resolve_data(COLUMN, "../data/daymet_TaylorRiverCO.h5")
        self.assertEqual(found, (COLUMN / "data" / "daymet_TaylorRiverCO.h5").resolve())
        with self.assertRaisesRegex(case_mod.CaseError, "not found"):
            microbe.resolve_data(COLUMN, "../data/nothing.h5")


@unittest.skipUnless(HAVE_PKGS and HAVE_CASES, "needs numpy, h5py, netCDF4 and the microbe case folders")
class CaseDetectionTests(unittest.TestCase):
    def test_only_a_folder_of_microbe_inputs_is_a_microbe_case(self):
        from ats_ecosim_pairs import cli
        self.assertTrue(cli._is_microbe_case(case_mod.load(BOX)))
        self.assertTrue(cli._is_microbe_case(case_mod.load(COLUMN)))
        with tempfile.TemporaryDirectory() as d:                      # a hillslope case has namelists and no input to read
            (Path(d) / "ERT6.namelist").write_text("&ecosim\n/\n")
            self.assertFalse(cli._is_microbe_case(case_mod.load(d)))
        pft = ROOT / "cases" / "pft_tests"
        if (pft / "bare_ground.xml").is_file():
            self.assertFalse(cli._is_microbe_case(case_mod.load(pft)))


@unittest.skipUnless(HAVE_PKGS, "needs numpy, h5py, netCDF4")
class StandaloneRuleTests(unittest.TestCase):
    def test_calendar_date_of_a_zero_based_day_of_a_365_day_year(self):
        self.assertEqual(microbe.calendar_date(0), (1, 1))
        self.assertEqual(microbe.calendar_date(181), (7, 1))       # the log of a run from day 181 reports month 7, day 1
        self.assertEqual(microbe.calendar_date(364), (12, 31))
        with self.assertRaises(ValueError):
            microbe.calendar_date(365)

    def test_the_standalone_counts_days_from_1_january_of_the_start_year(self):
        pl = dict(p=dict(name="t", ndays=30.0, start_doy=181, atmosphere={"CO2": 4.15e-4}), veg=None, years=[1995])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "n.namelist"
            microbe.write_namelist(path, pl, "pft.nc", "ghg.nc", ["TEMP_vr", "RootH2OUptake_vr"])
            text = path.read_text()
        self.assertIn("stop_n      = 211", text)                  # 181 + 30: a mid-year start would otherwise stop at once
        self.assertIn("start_date   = '19950701000000'", text)
        self.assertIn("hist_mfilt  = 24", text)                    # a history file spanning a year change is never closed
        self.assertIn("microbial_model = .true.", text)
        self.assertIn("plant_model     = .false.", text)
        self.assertNotIn("sp_phenol_in", text.replace("! no sp_phenol_in", ""))
        self.assertNotIn("RootH2OUptake_vr", text)                 # plant-only history field
        self.assertIn("atm_co2_fix        = 415.0", text)

    def test_the_standalone_layer_slots_and_the_regrading_rule(self):
        self.assertEqual(microbe.STANDALONE_SLOTS, 20)
        # an input with up to 19 layers keeps its own mesh, a larger one is regraded: checked on the column plan
        if not (HAVE_CASES and ECOSIM.is_dir() and (ECOSIM / "input_data").is_dir()):
            self.skipTest("needs the EcoSIM source tree")
        cfg = {**microbe.MICROBE_OPTIONS, "history_fields": microbe.HISTORY_FIELDS}
        box = microbe.plan(BOX_XML, BOX, cfg, ECOSIM)
        col = microbe.plan(COLUMN_XML, COLUMN, cfg, ECOSIM)
        self.assertFalse(box["regraded"])
        self.assertEqual(box["p"]["ncells"], 2)
        self.assertTrue(col["regraded"])
        self.assertEqual((col["p"]["ncells"], col["p"]["input_ncells"]), (19, 100))
        self.assertAlmostEqual(float(col["dz"].sum()), 10.0)
        self.assertAlmostEqual(float(col["dz"][0]), 0.1)           # 0.05 m (a 1.9 m bottom layer) made the ATS run lose its timestep
        self.assertIsNone(col["veg"])                              # the fork's standalone has no prescribed phenology input
        self.assertTrue(col["p"]["phenology_dropped"])
        self.assertEqual(col["years"], [1995, 1996])

    def test_organic_matter_follows_the_coupled_start_up(self):
        corgc, corgn, corgp = microbe.layer_values({"ncells": 3}, {})
        self.assertAlmostEqual(corgc[0], 20 * np.exp(-0.3))
        self.assertAlmostEqual(corgc[2], 20 * np.exp(-0.9))
        np.testing.assert_allclose(corgn, corgc / 12)
        np.testing.assert_allclose(corgp, corgc / 150)


@unittest.skipUnless(HAVE_PKGS and HAVE_CASES, "needs numpy, h5py, netCDF4 and the microbe case folders")
class ForcingTests(unittest.TestCase):
    def test_the_daily_table_is_interpolated_linearly_at_the_start_of_every_hour(self):
        p = microbe.read_input(COLUMN_XML, COLUMN)
        f, longwave = microbe.hourly_forcing(p, 3)
        self.assertEqual(len(f["air_temperature"]), 72)
        import h5py
        with h5py.File(p["forcing"]["air_temperature"][1], "r") as h:
            y = h["air temperature [K]"][:3]
        self.assertAlmostEqual(f["air_temperature"][0], y[0])
        self.assertAlmostEqual(f["air_temperature"][24], y[1])
        self.assertAlmostEqual(f["air_temperature"][12], 0.5 * (y[0] + y[1]))
        self.assertEqual(len(longwave), 72)                        # computed from air temperature and vapor pressure, as ATS does
        self.assertGreater(longwave.min(), 100.0)

    def test_the_box_has_no_surface_energy_exchange_so_the_standalone_gets_near_neutral_forcing(self):
        p = microbe.read_input(BOX_XML, BOX)
        f, longwave = microbe.hourly_forcing(p, 2)
        self.assertIsNone(longwave)                                # no XRADH: EcoSIM estimates the sky longwave itself
        self.assertAlmostEqual(f["vapor_pressure"][0], 3139.0, delta=15.0)    # saturated at 298 K (ATS input: 2000 Pa)
        self.assertTrue(np.all(f["air_temperature"] == 298.0))


@unittest.skipUnless(HAVE_PKGS and HAVE_CASES and ECOSIM.is_dir() and (ECOSIM / "examples").is_dir(),
                     "needs numpy, h5py, netCDF4, the microbe cases and an EcoSIM source tree")
class WritePairTests(unittest.TestCase):
    def test_a_box_pair_is_written_and_read_back(self):
        import netCDF4
        with tempfile.TemporaryDirectory() as d:
            case_dir = Path(d) / "microbe_bgc_box"
            shutil.copytree(BOX, case_dir, ignore=shutil.ignore_patterns("pairs"))
            c = case_mod.load(case_dir)
            microbe.generate_pairs(c, {"build": "fork"}, ECOSIM)
            pair = c.pair_dir("single_cell.boxsbgc_equiv")
            meta = json.loads((pair / "pair.json").read_text())
            self.assertEqual((meta["kind"], meta["layers"], meta["ndays"], meta["thermal_neutral"]), ("microbe", 2, 30.0, True))
            self.assertEqual(c.meta("single_cell.boxsbgc_equiv")["kind"], "microbe")
            self.assertTrue(all((pair / f).is_file() for f in ("ats_match.xml", "grid.nc", "clim.nc", "standalone.namelist")))
            g = netCDF4.Dataset(pair / "grid.nc")
            self.assertEqual(g.dimensions["nlevs"].size, 20)       # JZ = 20 in the standalone
            self.assertAlmostEqual(float(g["CDPTH"][0, 1]), 1.0)
            self.assertAlmostEqual(float(g["THW"][0, 0]), 0.65)    # saturated
            self.assertAlmostEqual(float(g["CORGC"][0, 0]), 20 * np.exp(-0.3), places=4)
            self.assertAlmostEqual(float(g["RSCfL"][0, 0]), 0.10 * 20 * np.exp(-0.3), places=4)
            ats = (pair / "ats_match.xml").read_text()
            self.assertIn("visualize ecosim state", ats)           # the biomass the report reads
            self.assertIn("../ecosim_pftpar_20261021.nc", ats)
            # jobs: the standalone must show every hour of the run, not stop_n * 24 (stop_n counts from 1 January)
            job = jobs.build_jobs(c, "standalone")[0]
            self.assertEqual(job.expected_hours, 720)

    def test_a_merged_build_migrates_the_ats_input_and_names_the_history_fields_it_has(self):
        import netCDF4
        with tempfile.TemporaryDirectory() as d:
            for src, name in ((BOX, "microbe_bgc_box"), (COLUMN, "microbe_bgc_column")):
                case_dir = Path(d) / name
                shutil.copytree(src, case_dir, ignore=shutil.ignore_patterns("pairs"))
            box = case_mod.load(Path(d) / "microbe_bgc_box")
            microbe.generate_pairs(box, {"build": "merged"}, ECOSIM)
            pair = box.pair_dir("single_cell.boxsbgc_equiv")
            ats = (pair / "ats_match.xml").read_text()
            self.assertIn('name="number of cells" type="Array(int)" value="{1, 1, 5}"', ats)       # the snow code needs 5 soil layers
            self.assertIn('name="number of heterotrophic guilds" type="int" value="8"', ats)
            self.assertIn('name="semi-implicit surface heat exchange" type="bool" value="false"', ats)    # the box has no energy source
            meta = json.loads((pair / "pair.json").read_text())
            self.assertEqual((meta["build"], meta["layers"], meta["layers_raised"]), ("merged", 5, True))
            text = (pair / "standalone.namelist").read_text()
            self.assertIn("'Acetate_vr'", text)
            self.assertNotIn("'acetate_vr'", text)
            g = netCDF4.Dataset(pair / "grid.nc")
            self.assertAlmostEqual(float(g["CDPTH"][0, 4]), 1.0)
            self.assertEqual(g.dimensions["nlevs"].size, 20)
            # the column stays plant-free: the merged standalone has sp_phenol_in but its driver turns the microbial model off with it
            col_xml = Path(d) / "microbe_bgc_column" / COLUMN_XML.name
            pl = microbe.plan(col_xml, col_xml.parent, {**microbe.MICROBE_OPTIONS, **microbe.MERGED_BUILD,
                                                         "history_fields": microbe.HISTORY_FIELDS}, ECOSIM)
            self.assertIsNone(pl["veg"])                      # the standalone turns its microbial model off with prescribed phenology
            self.assertTrue(pl["p"].get("phenology_dropped"))

    def test_the_site_and_calendar_may_be_values_or_sublists_read_from_the_forcing_file(self):
        hourly = ROOT / "cases" / "pft_tests" / "data" / "daymet_TaylorRiverCO_hourly.h5"
        if not hourly.is_file():
            self.skipTest("needs the pft_tests data")
        text = COLUMN_XML.read_text()
        lists = "".join(f'''<ParameterList name="{n}" type="ParameterList"><Parameter name="file" type="string" value="{hourly}"/>
            <Parameter name="header" type="string" value="{h}"/></ParameterList>''' for n, h in (
            ("starting year", "start year"), ("starting day of year [0-364]", "start day of year [0-364]"),
            ("latitude [degrees]", "latitude [deg]")))
        text = text.replace('<Parameter name="starting day of year [0-364]" type="int" value="181"/>', "")
        text = text.replace('<Parameter name="starting year" type="int" value="1995"/>', lists)
        with tempfile.TemporaryDirectory() as d:
            case_dir = Path(d) / "c"
            shutil.copytree(COLUMN, case_dir, ignore=shutil.ignore_patterns("pairs"))
            xml = case_dir / COLUMN_XML.name
            xml.write_text(text)
            p = microbe.read_input(xml, case_dir)
        self.assertEqual((p["start_year"], p["start_doy"]), (1980, 0))
        self.assertAlmostEqual(p["site_latitude"], 38.8837, places=3)

    def test_build_detection_reads_the_source_trees(self):
        if not (ECOSIM / "drivers" / "ecosim" / "EcoSIMAPI.F90").is_file():
            self.skipTest("needs the EcoSIM source tree")
        b = microbe.detect_build(ECOSIM, None, "auto")
        self.assertEqual(microbe.detect_build(ECOSIM, None, "fork")["name"], "fork")
        self.assertFalse(microbe.MERGED_BUILD["sp_phenol_input"])
        if b["name"] == "merged":
            self.assertFalse(b["sp_phenol_input"])           # even where sp_phenol_in exists
            self.assertEqual((b["xradh_factor"], b["n_heter"], b["min_layers"]), (1.0, 8, 5))
            self.assertEqual(b["acetate_field"], "Acetate_vr")

    def test_the_readme_lists_what_is_not_matched(self):
        with tempfile.TemporaryDirectory() as d:
            case_dir = Path(d) / "microbe_bgc_box"
            shutil.copytree(BOX, case_dir, ignore=shutil.ignore_patterns("pairs"))
            c = case_mod.load(case_dir)
            microbe.generate_pairs(c, {"build": "fork"}, ECOSIM)
            text = (c.pairs_dir / "README.md").read_text()
        self.assertIn("## Not matched", text)
        self.assertIn("ignores the grid's `CNH4`", text)


@unittest.skipUnless(HAVE_PKGS and importlib.util.find_spec("matplotlib"), "needs numpy, h5py, netCDF4, matplotlib")
class ReportTests(unittest.TestCase):
    def test_biomass_components_are_decoded_as_element_group_pool_complex(self):
        from ats_ecosim_pairs import report_microbe as rm
        k = np.arange(315)
        pair = SimpleNamespace(H=np.broadcast_to(k[:, None, None].astype(float), (315, 2, 4)).copy(), A=None)
        # element C is every third component; component 0 is (C, group 0, pool 0, complex 0)
        c_total = rm.biomass_by(pair, elem=0).sum()
        self.assertEqual(c_total, 8 * k[k % 3 == 0].sum())               # 2 times x 4 layers
        grp0 = rm.biomass_by(pair, elem=0, fg=0)
        self.assertEqual(grp0[0, 0], k[(k % 3 == 0) & (((k // 3) % 21) // 3 == 0)].sum())
        humus = rm.biomass_by(pair, elem=0, cplx=4)
        self.assertEqual(humus[0, 0], k[(k % 3 == 0) & (k // 63 == 4)].sum())
        pools = sum(rm.biomass_by(pair, elem=1, pool=p)[0, 0] for p in range(3))
        self.assertEqual(pools, rm.biomass_by(pair, elem=1)[0, 0])

    def test_numbers_are_formatted_for_the_tables(self):
        from ats_ecosim_pairs import report_microbe as rm
        self.assertEqual(rm.fnum(0), "0")
        self.assertEqual(rm.fnum(float("nan")), "&ndash;")
        self.assertEqual(rm.fnum(1234567.0), "1.23e+06")
        self.assertEqual(rm.fnum(1234.5), "1,234.5")


if __name__ == "__main__":
    unittest.main()
