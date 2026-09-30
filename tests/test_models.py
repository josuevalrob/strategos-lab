"""L7: models over time.  Counts per model x Q&A version; no scores."""

import shutil
import tempfile
import unittest
from pathlib import Path

import fixture
from fixture import REAL_REPO, real_config

from stlab import config, models, runs


class RealModelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = models.group(runs.Registry(real_config()))
        cls.by = {(g["model"], g["qa_version"]): g for g in cls.data["groups"]}

    def test_12c1_jev_vs_laya_side_by_side(self):
        jev = self.by[("jev jev-1.13.0", "7d592def10")]
        laya = self.by[("laya convaiinnovations/laya/typed-decisions", "7d592def10")]
        self.assertEqual(jev["runs"], ["results/phase12c1-jev-live"])
        self.assertEqual(laya["runs"], ["results/phase12c1-laya-live"])
        self.assertEqual(jev["kinds"]["pass_order"]["rows"], 117)
        self.assertEqual(laya["kinds"]["pass_order"]["rows"], 121)
        for g in (jev, laya):
            k = g["kinds"]["pass_order"]
            self.assertEqual(sum(k["choices"].values()), k["rows"])

    def test_versions_split(self):
        self.assertIn(("jev jev-1.13.0", "f9b1ced7bc"), self.by)
        self.assertIn(("jev jev-1.13.0", "f9b1ced7bc-dirty"), self.by)
        self.assertIn(("jev jev-1.13.0 [script]", "e150132f41"), self.by)
        self.assertEqual(self.by[("jev jev-1.13.0", "f9b1ced7bc")]["kinds"]["play"]["rows"], 114)

    def test_no_scores(self):
        for g in self.data["groups"]:
            self.assertEqual(set(g) - {"model", "adapter", "qa_version", "runs", "kinds", "first", "last"}, set())


class SyntheticModelsTest(unittest.TestCase):
    def test_grouping_uses_qa_commit_over_mod_sha(self):
        root = Path(tempfile.mkdtemp(prefix="lab-models-"))
        try:
            r = lambda q, c: fixture.play_row(q, ["hold", "economy"], c, "hold", 3.0 + q, "")  # noqa: E731
            h1 = fixture.header("a" * 40, qa={"commit": "b" * 40, "dirty": False})
            h2 = fixture.header("c" * 40, adapter="laya", version="laya-x")
            fixture.write_run(root / "results" / "one", h1, [r(1, "hold"), r(2, "hold"), r(3, None)], [])
            fixture.write_run(root / "results" / "two", h1, [r(1, "economy")], [])
            fixture.write_run(root / "results" / "three", h2, [r(1, "economy")], [])
            cfg = config.Config(repo=root / "none", extra_run_roots=[str(root / "results")])
            data = models.group(runs.Registry(cfg))
            by = {(g["model"], g["qa_version"]): g for g in data["groups"]}
            g = by[("jev jev-1.13.0", "bbbbbbbbbb")]
            self.assertEqual(sorted(g["runs"]), ["results/one", "results/two"])
            self.assertEqual(g["kinds"]["play"]["choices"], {"hold": 2, "(no answer)": 1, "economy": 1})
            self.assertEqual(by[("laya laya-x", "cccccccccc")]["kinds"]["play"]["rows"], 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
