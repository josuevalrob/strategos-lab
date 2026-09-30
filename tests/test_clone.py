"""L8 acceptance: clone Sparta to Rome on a fixture repo; the issues are listed; the files are
committed through the same Apply path (and nothing else)."""

import json
import shutil
import unittest

import fixture
from fixture import git

from stlab import clone, code, config, editor, mapgraph, qa


class CloneTest(unittest.TestCase):
    def setUp(self):
        self.repo = fixture.make_repo()
        self.cfg = fixture.fixture_config(self.repo)
        self.facts = code.load(self.cfg)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        shutil.rmtree(self.cfg.cache_dir, ignore_errors=True)

    def test_civ_codes(self):
        codes = {c["code"]: c for c in qa.civ_codes(self.cfg)}
        self.assertIn("rome", codes)
        self.assertIn("gaul", codes)
        self.assertTrue(codes["spart"]["has_civ_file"])
        self.assertFalse(codes["rome"]["has_civ_file"])

    def test_preview_rome_lists_what_cannot_work(self):
        pv = clone.preview(self.cfg, self.facts, "spart", "rome", "Rome")
        self.assertTrue(pv["ok"])
        paths = [f["path"] for f in pv["files"]]
        self.assertEqual(paths[0], f"{config.CIVS_DIR}/rome.json")
        self.assertEqual(sorted(paths[1:]), [f"{config.HEROES_DIR}/rome/{n}.json"
                                             for n in ("agis", "brasidas", "leonidas", "pausanias")])
        what = [i["what"] for i in pv["issues"] if i["level"] == "cannot work"]
        self.assertIn("hero agis: no template units/rome/hero_agis", what)
        self.assertIn("hero building structures/rome/gerousia does not exist", what)
        self.assertTrue(any(w.startswith("raid leader units/rome/hero_brasidas") for w in what))
        self.assertTrue(any(i["level"] == "text" and "Sparta" in i["what"] for i in pv["issues"]))
        self.assertIn("units/rome/hero_scipio", pv["hero_templates"])
        civ = json.loads(pv["files"][0]["text"])
        self.assertEqual((civ["civ"], civ["name"]), ("rome", "Rome"))
        self.assertIn("units/rome/hero_agis", civ["params"]["heroOrder"]["order"])
        self.assertTrue(pv["files"][0]["text"].startswith('{\n\t"civ": "rome"'))
        # Nothing written by a preview.
        self.assertFalse((self.repo / config.CIVS_DIR / "rome.json").exists())

    def test_clone_commits_exactly_the_new_files(self):
        (self.repo / "unrelated.txt").write_text("dirty\n")
        res = clone.clone(self.cfg, self.facts, "spart", "rome", "Rome")
        committed = git(self.repo, "show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(committed), sorted([f"{config.CIVS_DIR}/rome.json"] +
                                                   [f"{config.HEROES_DIR}/rome/{n}.json" for n in
                                                    ("agis", "brasidas", "leonidas", "pausanias")]))
        subject = git(self.repo, "log", "-1", "--format=%s").strip()
        self.assertTrue(subject.startswith("Strategos lab: clone spart into rome (5 files"), subject)
        body = git(self.repo, "log", "-1", "--format=%b")
        self.assertIn("[cannot work] hero building structures/rome/gerousia does not exist", body)
        self.assertIn(" M unrelated.txt", git(self.repo, "status", "--porcelain", "--untracked-files=no"))
        self.assertTrue(res["issues"])
        g = mapgraph.build(self.cfg, "rome")
        bad = {n["id"] for n in g["nodes"] if n.get("bad")}
        self.assertIn("opt:hero:hero_agis", bad)
        self.assertIn("rome", g["civs"])
        self.assertTrue(any("gerousia" in w for w in g["warnings"]))

    def test_refusals(self):
        self.assertFalse(clone.preview(self.cfg, self.facts, "spart", "spart")["ok"])
        self.assertFalse(clone.preview(self.cfg, self.facts, "spart", "zzz")["ok"])
        self.assertFalse(clone.preview(self.cfg, self.facts, "spart", "../x")["ok"])
        self.assertFalse(clone.preview(self.cfg, self.facts, "nociv", "gaul")["ok"])
        git(self.repo, "checkout", "-q", "-b", "elsewhere")
        with self.assertRaises(editor.Refused) as cm:
            clone.clone(self.cfg, self.facts, "spart", "gaul")
        self.assertEqual(cm.exception.code, "branch")
        self.assertFalse((self.repo / config.CIVS_DIR / "gaul.json").exists())


if __name__ == "__main__":
    unittest.main()
